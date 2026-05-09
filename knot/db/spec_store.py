"""Spec persistence against postgres `spec_revisions`.

Round-trip serialization (`spec_to_dict` / `spec_from_dict`) lives in
`knot.spec.serialization`. Content hashing lives in `knot.spec.canonical`.
This module is just the postgres-side persistence + draft lifecycle +
publish gate.

Draft lifecycle:
  - Drafts are spec_revisions rows with `published=FALSE`; mutable in place.
  - Publishing flips `published=TRUE` (atomically deactivates prior).
  - The publish gate (`publish_gate`) runs Pydantic re-parse + reference
    resolution across the spec graph before flipping the flag.  Steps 3 + 4
    (DataContext cross-checks, impact preview) are bindings-side and land
    with the modeling router.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import psycopg

from knot.spec.canonical import compute_content_hash
from knot.spec.errors import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    PublishGateError,
)
from knot.spec.metaschema import (
    Constraint,
    OntologyClass,
    Slot,
    Source,
    Spec,
    TypeDefinition,
)
from knot.spec.serialization import spec_from_dict, spec_to_dict

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Publish gate
# ---------------------------------------------------------------------------


def _detect_mixin_cycle(start: OntologyClass) -> list[str] | None:
    """If ``start``'s mixin chain has a cycle, return the offending name path.
    Else None. Identity-compared (mixins are object refs, not name lookups)."""
    stack: list[OntologyClass] = []

    def visit(c: OntologyClass) -> list[str] | None:
        if any(c is s for s in stack):
            idx = next(i for i, s in enumerate(stack) if s is c)
            return [s.name for s in stack[idx:]] + [c.name]
        stack.append(c)
        for mx in c.mixins:
            cycle = visit(mx)
            if cycle is not None:
                return cycle
        stack.pop()
        return None

    return visit(start)


def _detect_mixin_slot_collision(
    start: OntologyClass,
) -> tuple[str, str, str] | None:
    """Walk the effective slot set; return ``(slot_name, source_a, source_b)``
    if two distinct mixins contribute the same slot name. Own slots shadow
    mixin slots silently and are not a collision."""
    own_names = {s.name for s in start.slots}
    contributors: dict[str, str] = {}
    visited: list[OntologyClass] = []
    queue: list[OntologyClass] = list(start.mixins)
    while queue:
        current = queue.pop(0)
        if any(current is v for v in visited):
            continue
        visited.append(current)
        for s in current.slots:
            if s.name in own_names:
                continue
            prev = contributors.get(s.name)
            if prev is not None and prev != current.name:
                return (s.name, prev, current.name)
            contributors[s.name] = current.name
        queue.extend(current.mixins)
    return None


def publish_gate(candidate: Spec) -> None:
    """Run all spec-graph-side validation.  Raises `PublishGateError` on failure.

    Step 1: Pydantic shape (already enforced by Spec instantiation; re-runs
            model_validate over the canonical dump as a defensive recheck).
    Step 2: Reference resolution — every Slot range, every Source.entity_class,
            every Source.identifier_slot, every Constraint.primary, every
            SlotOverride.slot must point at an entity present on the spec.

    Steps 3+4 (DataContext cross-checks + impact preview) are bindings-side
    and land with the modeling router.  This function is the natural place
    for them to be added.
    """
    # Step 1: shape was enforced at construction (extra='forbid' on every
    # SpecBase subclass; bad fields raise immediately).  Pydantic's
    # model_dump-and-revalidate trick doesn't work on the cyclic spec graph
    # (slot.range → OntologyClass → slots → Slot → range → ...), so we trust
    # that the in-memory Pydantic models are well-formed.
    if not isinstance(candidate, Spec):
        raise PublishGateError(
            f"publish_gate expected a Spec instance, got {type(candidate).__name__}"
        )

    # Step 2: reference resolution.
    classes_by_id = {id(c): c for c in candidate.classes}
    slots_by_id = {id(s): s for s in candidate.slots}
    types_by_id = {id(t): t for t in candidate.types}

    errors: list[str] = []

    for s in candidate.slots:
        if s.range is None:
            # Derived slots with deferred range are valid; structural slots
            # without range are an error.
            if s.derivation is None:
                errors.append(f"Slot {s.name!r} has no range and no derivation.")
            continue
        if isinstance(s.range, OntologyClass):
            if id(s.range) not in classes_by_id:
                errors.append(
                    f"Slot {s.name!r}.range references OntologyClass "
                    f"{s.range.name!r} not on spec.classes."
                )
        elif isinstance(s.range, TypeDefinition):
            if id(s.range) not in types_by_id:
                errors.append(
                    f"Slot {s.name!r}.range references TypeDefinition "
                    f"{s.range.name!r} not on spec.types."
                )

    for c in candidate.classes:
        for slot in c.slots:
            if id(slot) not in slots_by_id:
                errors.append(
                    f"Class {c.name!r}.slots includes Slot {slot.name!r} not on spec.slots."
                )

    for src in candidate.sources:
        if id(src.entity_class) not in classes_by_id:
            errors.append(
                f"Source {src.name!r}.entity_class references OntologyClass "
                f"{src.entity_class.name!r} not on spec.classes."
            )
        # Polymorphic classes cannot be pointed at by a Source (commitment 11,
        # slice restriction (b)). Their rows arrive only via Add corrections.
        if getattr(src.entity_class, "identifier_pattern", None) is not None:
            errors.append(
                f"Source {src.name!r}.entity_class {src.entity_class.name!r} has an "
                f"identifier_pattern and is polymorphic. Sources cannot target "
                f"polymorphic classes directly; use Add corrections instead."
            )
        if id(src.identifier_slot) not in slots_by_id:
            errors.append(
                f"Source {src.name!r}.identifier_slot references Slot "
                f"{src.identifier_slot.name!r} not on spec.slots."
            )
        # identifier_slot must be one of entity_class.slots
        if not any(slot is src.identifier_slot for slot in src.entity_class.slots):
            errors.append(
                f"Source {src.name!r}.identifier_slot ({src.identifier_slot.name!r}) "
                f"is not on its entity_class {src.entity_class.name!r}."
            )

    for con in candidate.constraints:
        if id(con.primary) not in classes_by_id:
            errors.append(
                f"Constraint {con.name!r}.primary references OntologyClass "
                f"{con.primary.name!r} not on spec.classes."
            )

    # DiscriminatedRef target_class validation: every DiscriminatedRef on any
    # slot must name a target_class that is present on spec.classes.
    from knot.spec.metaschema import DiscriminatedRef as _DiscriminatedRef

    for s in candidate.slots:
        ref = getattr(s, "reference", None)
        if isinstance(ref, _DiscriminatedRef) and ref.target_class is not None:
            if id(ref.target_class) not in classes_by_id:
                errors.append(
                    f"Slot {s.name!r}.reference.target_class references OntologyClass "
                    f"{ref.target_class.name!r} not on spec.classes."
                )

    # Mixin validation: every mixin must be on spec.classes; the mixin chain
    # must be acyclic; the effective slot set (own + transitive mixins) must
    # have no name collisions.
    for c in candidate.classes:
        for mx in c.mixins:
            if id(mx) not in classes_by_id:
                errors.append(
                    f"Class {c.name!r}.mixins references OntologyClass "
                    f"{mx.name!r} not on spec.classes."
                )
        cycle_path = _detect_mixin_cycle(c)
        if cycle_path is not None:
            errors.append(f"Class {c.name!r} has a cyclic mixin chain: " + " -> ".join(cycle_path))
            continue
        collision = _detect_mixin_slot_collision(c)
        if collision is not None:
            slot_name, source_a, source_b = collision
            errors.append(
                f"Class {c.name!r} has a slot name collision on {slot_name!r} "
                f"between mixins {source_a!r} and {source_b!r}."
            )

    # Defined-class validation.
    for c in candidate.classes:
        if getattr(c, "definition", None) is None:
            continue
        # is_a must be set for defined classes.
        if c.is_a is None:
            errors.append(f"Defined class {c.name!r} must have is_a set to a parent class.")
            continue
        if id(c.is_a) not in classes_by_id:
            errors.append(
                f"Defined class {c.name!r}.is_a references OntologyClass "
                f"{c.is_a.name!r} not on spec.classes."
            )
            continue
        # Definition must compile without error.
        from knot.spec.compile.postgres import CompileContext, compile_predicate
        from knot.spec.compile.postgres._dispatch import CompilerError

        try:
            ctx = CompileContext(primary_class=c.is_a, alias="s")
            compile_predicate(c.definition, ctx)
        except (CompilerError, NotImplementedError) as exc:
            errors.append(f"Defined class {c.name!r}.definition failed to compile: {exc}")

    if errors:
        raise PublishGateError("Publish gate failed:\n  - " + "\n  - ".join(errors))


# ---------------------------------------------------------------------------
# Postgres storage layer
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=UTC)


async def get_published(conn: psycopg.AsyncConnection) -> Spec | None:
    """Return the currently-published spec, or None if none is published."""
    row = await (
        await conn.execute(
            "SELECT spec FROM spec_revisions WHERE published = TRUE",
        )
    ).fetchone()
    if row is None:
        return None
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return spec_from_dict(payload)


async def get_revision(conn: psycopg.AsyncConnection, revision: int) -> Spec:
    """Return the spec at a specific revision (draft or published)."""
    row = await (
        await conn.execute(
            "SELECT spec FROM spec_revisions WHERE revision = %s",
            (revision,),
        )
    ).fetchone()
    if row is None:
        raise DraftNotFoundError(f"spec_revisions {revision} not found")
    payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return spec_from_dict(payload)


async def list_drafts(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    """Summary rows for all unpublished drafts."""
    rows = await (
        await conn.execute(
            "SELECT revision, label, parent_revision, content_hash, created_at "
            "FROM spec_revisions WHERE published = FALSE "
            "ORDER BY revision DESC",
        )
    ).fetchall()
    return [
        {
            "revision": r[0],
            "label": r[1],
            "parent_revision": r[2],
            "content_hash": r[3],
            "created_at": r[4].isoformat(),
        }
        for r in rows
    ]


async def list_published(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    """All published revisions, newest first.  Immortal."""
    rows = await (
        await conn.execute(
            "SELECT revision, content_hash, parent_revision, label, "
            "created_at, published_at "
            "FROM spec_revisions WHERE published_at IS NOT NULL "
            "ORDER BY published_at DESC",
        )
    ).fetchall()
    return [
        {
            "revision": r[0],
            "content_hash": r[1],
            "parent_revision": r[2],
            "label": r[3],
            "created_at": r[4].isoformat(),
            "published_at": r[5].isoformat() if r[5] else None,
        }
        for r in rows
    ]


async def create_draft(
    conn: psycopg.AsyncConnection,
    *,
    parent_revision: int | None = None,
    label: str | None = None,
) -> int:
    """Create a draft branched from a published parent (or specified revision).

    If `parent_revision` is None, branches from the currently-published spec.
    If no spec is published yet, the draft starts as an empty Spec.
    Returns the new draft's revision number.
    """
    if parent_revision is not None:
        parent = await get_revision(conn, parent_revision)
    else:
        parent = await get_published(conn) or Spec(id="", version="0.0.0")

    payload = spec_to_dict(parent)
    content_hash = compute_content_hash(parent)

    row = await (
        await conn.execute(
            """
            INSERT INTO spec_revisions
                (spec, content_hash, published, parent_revision, label, created_at)
            VALUES (%s, %s, FALSE, %s, %s, %s)
            RETURNING revision
            """,
            (json.dumps(payload), content_hash, parent_revision, label, _now()),
        )
    ).fetchone()
    return row[0]


async def update_draft(conn: psycopg.AsyncConnection, draft_id: int, spec: Spec) -> None:
    """Overwrite a draft's spec content. Drafts are mutable; published rows are not.

    NOTE: this primitive is *not* concurrency-safe on its own — two parallel
    callers that read the same draft, mutate, and call ``update_draft`` will
    silently overwrite each other. For mutate-in-place flows (every endpoint
    in ``api/spec.py``), use :func:`edit_draft` instead, which holds a row
    lock for the full read-modify-write window.
    """
    row = await (
        await conn.execute(
            "SELECT published FROM spec_revisions WHERE revision = %s",
            (draft_id,),
        )
    ).fetchone()
    if row is None:
        raise DraftNotFoundError(f"spec_revisions {draft_id} not found")
    if row[0] is True:
        raise DraftAlreadyPublishedError(
            f"spec_revisions {draft_id} is already published; create a new draft "
            "branched from it instead."
        )

    payload = spec_to_dict(spec)
    content_hash = compute_content_hash(spec)
    await conn.execute(
        "UPDATE spec_revisions SET spec = %s, content_hash = %s WHERE revision = %s",
        (json.dumps(payload), content_hash, draft_id),
    )


@asynccontextmanager
async def edit_draft(conn: psycopg.AsyncConnection, draft_id: int) -> AsyncIterator[Spec]:
    """Atomic read-modify-write of a draft.

    Opens a transaction, locks the ``spec_revisions`` row with FOR UPDATE,
    yields the rehydrated Spec, and writes the (possibly mutated) spec back
    on context exit. Concurrent calls on the same draft serialize on the row
    lock, eliminating the lost-update race that plain ``get_revision`` +
    ``update_draft`` would have.

    Raises ``DraftNotFoundError`` if the revision doesn't exist and
    ``DraftAlreadyPublishedError`` if it's already published.
    """
    async with conn.transaction():
        row = await (
            await conn.execute(
                "SELECT spec, published FROM spec_revisions WHERE revision = %s FOR UPDATE",
                (draft_id,),
            )
        ).fetchone()
        if row is None:
            raise DraftNotFoundError(f"spec_revisions {draft_id} not found")
        if row[1] is True:
            raise DraftAlreadyPublishedError(
                f"spec_revisions {draft_id} is already published; create a new "
                "draft branched from it instead."
            )

        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        spec = spec_from_dict(payload)

        yield spec

        new_payload = spec_to_dict(spec)
        new_hash = compute_content_hash(spec)
        await conn.execute(
            "UPDATE spec_revisions SET spec = %s, content_hash = %s WHERE revision = %s",
            (json.dumps(new_payload), new_hash, draft_id),
        )


async def get_published_revision(conn: psycopg.AsyncConnection) -> int | None:
    """Revision number of the currently-published spec, or None."""
    row = await (
        await conn.execute(
            "SELECT revision FROM spec_revisions WHERE published = TRUE",
        )
    ).fetchone()
    return row[0] if row else None


async def get_published_content_hash(conn: psycopg.AsyncConnection) -> str | None:
    """Content hash of the currently-published spec, or None."""
    row = await (
        await conn.execute(
            "SELECT content_hash FROM spec_revisions WHERE published = TRUE",
        )
    ).fetchone()
    return row[0] if row else None


async def publish_draft(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    allow_destructive: bool = False,
) -> int:
    """Run the publish gate; on pass, atomically flip this draft to published
    and bring the data-plane schema in line with the new spec.

    The destructive-change check (DropClass / DropSlot / ChangeSlotType)
    runs against the diff before the flag flip; if any destructive change
    is present and ``allow_destructive`` is False, raises
    ``PublishGateError`` and the draft stays a draft.

    The whole flow — gate, diff, classify, flip, migrate — runs inside one
    transaction so concurrent publishes can't observe the partial unique
    index in a transient state, and the data plane and the spec are
    never out of sync.
    """
    from knot.spec.compile.postgres import compile_constraint
    from knot.spec.compile.postgres.migration import (
        apply_changes,
        diff_specs,
        is_destructive,
    )

    async with conn.transaction():
        candidate = await get_revision(conn, draft_id)
        publish_gate(candidate)
        prev = await get_published(conn)

        changes = diff_specs(prev, candidate)
        destructive = [type(c).__name__ for c in changes if is_destructive(c)]
        if destructive and not allow_destructive:
            raise PublishGateError(
                "Publish would apply destructive changes "
                f"({', '.join(sorted(set(destructive)))}); pass "
                "allow_destructive=true to confirm."
            )

        # Step 4: constraint gate — for every constraint that is NEW or CHANGED
        # in the candidate vs prev, compile + run it against existing data.
        # ERROR severity with any violations → PublishGateError.
        # WARNING severity → log and continue.
        #
        # Only run against classes that already have tables (i.e., present in
        # prev).  Constraints on brand-new classes are skipped — no rows exist yet.
        prev_class_names: set[str] = {c.name for c in (prev.classes if prev else [])}
        prev_constraint_hashes: dict[str, str] = {}
        if prev:
            for con in prev.constraints:
                prev_constraint_hashes[con.name] = compute_content_hash(con)

        # Index defined classes by name for skip logic below.
        defined_class_names: set[str] = {
            c.name for c in candidate.classes if getattr(c, "definition", None) is not None
        }

        for con in candidate.constraints:
            cand_hash = compute_content_hash(con)
            prev_hash = prev_constraint_hashes.get(con.name)
            if cand_hash == prev_hash:
                continue  # unchanged — skip
            if con.primary.name not in prev_class_names:
                continue  # new class — no rows to check yet
            if con.primary.name in defined_class_names:
                continue  # defined classes are views; VIEW handles inclusion

            # Find the primary class on the candidate spec (by identity from
            # the rehydrated spec; `con.primary` already points to it).
            cls = con.primary
            stmt, params = compile_constraint(con, cls)
            try:
                violations = await (await conn.execute(stmt, params)).fetchall()
            except Exception as exc:
                raise PublishGateError(
                    f"Constraint {con.name!r} SQL execution failed: {exc}"
                ) from exc

            if violations:
                n = len(violations)
                if con.severity.value == "error":
                    raise PublishGateError(
                        f"Constraint {con.name!r} has {n} violation(s) against "
                        f"existing data (severity=ERROR). Publish rejected."
                    )
                else:
                    logger.warning(
                        "Constraint %r has %d violation(s) against existing data "
                        "(severity=WARNING); publishing anyway.",
                        con.name,
                        n,
                    )

        # Demote → promote in two statements so the partial unique index
        # `(published) WHERE published = TRUE` doesn't see two TRUE rows
        # transiently (postgres validates per-row, not per-statement).
        await conn.execute(
            "UPDATE spec_revisions SET published = FALSE WHERE published = TRUE AND revision <> %s",
            (draft_id,),
        )
        await conn.execute(
            "UPDATE spec_revisions SET published = TRUE, published_at = %s WHERE revision = %s",
            (_now(), draft_id),
        )
        await apply_changes(conn, changes)

    return draft_id


async def discard_draft(conn: psycopg.AsyncConnection, draft_id: int) -> None:
    """Delete an unpublished draft.  Published revisions are immortal."""
    row = await (
        await conn.execute(
            "SELECT published FROM spec_revisions WHERE revision = %s",
            (draft_id,),
        )
    ).fetchone()
    if row is None:
        raise DraftNotFoundError(f"spec_revisions {draft_id} not found")
    if row[0] is True:
        raise DraftAlreadyPublishedError(
            f"spec_revisions {draft_id} is published; cannot be discarded."
        )
    await conn.execute("DELETE FROM spec_revisions WHERE revision = %s", (draft_id,))
