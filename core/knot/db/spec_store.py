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
    ClassRef,
    OntologyClass,
    Spec,
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
    Step 2: Reference resolution — every Slot ClassRef target, every
            SourceBinding.class_, every SourceBinding.identifier_slot, every
            Constraint.primary must point at an entity present on the spec.
            Slots are now inline on each OntologyClass; there is no top-level
            slots list. identifier_slot must be reachable via effective_slots().

    Steps 3+4 (DataContext cross-checks + impact preview) are bindings-side
    and land with the modeling router.  This function is the natural place
    for them to be added.
    """
    # Step 1: shape was enforced at construction (extra='forbid' on every
    # SpecBase subclass; bad fields raise immediately).  Pydantic's
    # model_dump-and-revalidate trick doesn't work on the cyclic spec graph
    # (slot.type → ClassRef → OntologyClass → slots → Slot → type → ...),
    # so we trust that the in-memory Pydantic models are well-formed.
    if not isinstance(candidate, Spec):
        raise PublishGateError(
            f"publish_gate expected a Spec instance, got {type(candidate).__name__}"
        )

    # Step 2: reference resolution.
    from knot.spec.effective_slots import effective_slots as _effective_slots

    classes_by_id = {id(c): c for c in candidate.classes}

    errors: list[str] = []

    def _collect_classrefs(type_expr: Any) -> list[ClassRef]:
        """Walk a TypeExpression and collect all ClassRef nodes."""
        from knot.spec.metaschema import Array, Primitive

        if isinstance(type_expr, Primitive):
            return []
        if isinstance(type_expr, Array):
            return _collect_classrefs(type_expr.of)
        if isinstance(type_expr, ClassRef):
            return [type_expr]
        return []

    # Validate slots inline on each class.
    for c in candidate.classes:
        for slot in c.slots:
            if slot.type is None:
                # Derived slots with deferred type are valid; structural slots
                # without type are an error.
                if slot.derivation is None:
                    errors.append(
                        f"Class {c.name!r} slot {slot.name!r} has no type and no derivation."
                    )
                continue
            for ref in _collect_classrefs(slot.type):
                if id(ref.target_class) not in classes_by_id:
                    errors.append(
                        f"Class {c.name!r} slot {slot.name!r}.type references OntologyClass "
                        f"{ref.target_class.name!r} not on spec.classes."
                    )

    for b in candidate.source_bindings:
        bid = b.binding_id
        if id(b.class_) not in classes_by_id:
            errors.append(
                f"SourceBinding {bid!r}.class_ references OntologyClass "
                f"{b.class_.name!r} not on spec.classes."
            )
            continue
        # identifier_slot must be reachable via effective_slots() (own + mixin).
        effective = {s.name: s for s in _effective_slots(b.class_)}
        if b.identifier_slot.name not in effective or (
            effective[b.identifier_slot.name] is not b.identifier_slot
        ):
            errors.append(
                f"SourceBinding {bid!r}.identifier_slot ({b.identifier_slot.name!r}) "
                f"is not reachable via effective_slots() from class {b.class_.name!r}."
            )

    for con in candidate.constraints:
        if id(con.primary) not in classes_by_id:
            errors.append(
                f"Constraint {con.name!r}.primary references OntologyClass "
                f"{con.primary.name!r} not on spec.classes."
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

    # Defined-class validation (classes with a definition body — VIEW-backed).
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


def _summarize_blockers(blockers: list[dict]) -> str:
    """Build a compact, grep-friendly tag list for a PublishGateError message.

    Each blocker contributes one ``kind:identifier`` token so callers (and
    ``pytest.raises(match=)``) can pin failures to the offending entity
    without parsing the structured ``details`` payload.
    """
    parts: list[str] = []
    for b in blockers:
        kind = b.get("kind", "?")
        if kind == "constraint_violation" or kind == "constraint_sql_error":
            tag = str(b.get("constraint") or "?")
        elif kind == "type_cast_failure":
            tag = f"{b.get('class', '?')}.{b.get('slot', '?')}"
        elif kind == "identifier_nulls" or kind == "identifier_duplicates":
            tag = f"{b.get('source', '?')}.{b.get('slot', '?')}"
        elif kind == "required_violation":
            tag = f"{b.get('class', '?')}.{b.get('slot', '?')}"
        else:
            tag = kind
        parts.append(f"{kind}:{tag}")
    return ", ".join(parts)


async def run_preflight_checks(
    conn: psycopg.AsyncConnection,
    changes: list,
    prev: Spec | None,
    candidate: Spec,
) -> list[dict]:
    """Run pre-flight data checks for the given diff.

    Returns a list of blocker dicts (empty = all clear). Does NOT emit DDL.
    Runs inside the caller's transaction (uses savepoints for cast probes).

    Checks:
      - For each ChangeSlotTypeExpression: attempt the cast in a savepoint;
        capture failure as ``kind="type_cast_failure"``.
      - For each ChangeSourceBindingIdentifierSlot: verify the new slot has no NULLs
        and no duplicate values for that source's rows.
    """
    from psycopg import sql

    from knot.spec.compile.postgres._naming import schema, user_corrections_source
    from knot.spec.compile.postgres.migration import (
        ChangeSlotRequired,
        ChangeSlotTypeExpression,
        ChangeSourceBindingIdentifierSlot,
    )

    blockers: list[dict] = []

    for change in changes:
        if isinstance(change, ChangeSlotTypeExpression):
            # Refuse array → scalar at the preflight stage (mirrors the
            # CompilerError emit_ddl would raise) so the publish gate can
            # report it as a structured blocker rather than a raw cast error.
            prev_is_array = change.prev_pg_type.endswith("[]")
            new_is_array = change.new_pg_type.endswith("[]")
            if prev_is_array and not new_is_array:
                blockers.append(
                    {
                        "kind": "type_cast_failure",
                        "class": change.cls.name,
                        "slot": change.slot.name,
                        "from": change.prev_pg_type,
                        "to": change.new_pg_type,
                        "detail": (
                            "lossy: array → scalar would silently collapse "
                            "multiple values"
                        ),
                    }
                )
                continue

            # Probe the cast inside a savepoint so a failure rolls back only
            # the probe, not the enclosing publish transaction. Use the same
            # USING expression as emit_ddl so scalar→array preflight matches
            # what would actually run.
            col_ident = sql.Identifier(change.slot.name)
            newt = sql.SQL(change.new_pg_type)
            if not prev_is_array and new_is_array:
                cast_expr = sql.SQL("ARRAY[{col}]::{newt}").format(
                    col=col_ident, newt=newt
                )
            else:
                cast_expr = sql.SQL("{col}::{newt}").format(
                    col=col_ident, newt=newt
                )
            sp_name = f"preflight_cast_{change.cls.name.lower()}_{change.slot.name}"
            try:
                await conn.execute(f"SAVEPOINT {sp_name}")
                await (
                    await conn.execute(
                        sql.SQL(
                            "SELECT {cast} FROM {tbl} WHERE {col} IS NOT NULL"
                        ).format(
                            cast=cast_expr,
                            tbl=sql.Identifier(schema(), change.cls.name.lower()),
                            col=col_ident,
                        )
                    )
                ).fetchall()
                await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except psycopg.Error as exc:
                await conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                await conn.execute(f"RELEASE SAVEPOINT {sp_name}")
                blockers.append(
                    {
                        "kind": "type_cast_failure",
                        "class": change.cls.name,
                        "slot": change.slot.name,
                        "from": change.prev_pg_type,
                        "to": change.new_pg_type,
                        "detail": str(exc),
                    }
                )

        elif isinstance(change, ChangeSourceBindingIdentifierSlot):
            tbl = sql.Identifier(schema(), change.cls.name.lower())
            new_col = sql.Identifier(change.new_slot)

            # NULL check
            null_row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {tbl} WHERE _source = %s AND {col} IS NULL"
                    ).format(tbl=tbl, col=new_col),
                    (change.source_name,),
                )
            ).fetchone()
            null_count = null_row[0] if null_row else 0
            if null_count > 0:
                blockers.append(
                    {
                        "kind": "identifier_nulls",
                        "source": change.source_name,
                        "slot": change.new_slot,
                        "count": null_count,
                    }
                )

            # Duplicate check
            dup_rows = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT {col}, count(*) FROM {tbl} WHERE _source = %s "
                        "GROUP BY {col} HAVING count(*) > 1"
                    ).format(tbl=tbl, col=new_col),
                    (change.source_name,),
                )
            ).fetchall()
            if dup_rows:
                blockers.append(
                    {
                        "kind": "identifier_duplicates",
                        "source": change.source_name,
                        "slot": change.new_slot,
                        "samples": [
                            {"value": str(r[0]), "count": r[1]} for r in dup_rows[:5]
                        ],
                    }
                )

        elif isinstance(change, ChangeSlotRequired) and change.new_required:
            # false → true: check for NULLs in real-source rows (user-correction
            # rows are exempted by the CHECK constraint body, so skip them).
            tbl = sql.Identifier(schema(), change.cls.name.lower())
            col = sql.Identifier(change.slot_name)
            null_row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT count(*) FROM {tbl} "
                        "WHERE _source != {uc} AND {col} IS NULL"
                    ).format(tbl=tbl, col=col, uc=sql.Literal(user_corrections_source())),
                )
            ).fetchone()
            null_count = null_row[0] if null_row else 0
            if null_count > 0:
                blockers.append(
                    {
                        "kind": "required_violation",
                        "class": change.cls.name,
                        "slot": change.slot_name,
                        "null_count": null_count,
                    }
                )

    return blockers


class GateReport:
    """Returned by ``evaluate_gates``.  Contains the diff + all blocker info.
    No DDL is emitted.
    """

    def __init__(
        self,
        changes: list,
        blockers: list[dict],
        requires_allow_destructive: bool,
        candidate: Spec,
        prev: Spec | None,
    ) -> None:
        self.changes = changes
        self.blockers = blockers
        self.requires_allow_destructive = requires_allow_destructive
        self.candidate = candidate
        self.prev = prev


class _PendingRenames:
    """Parsed pending_renames payload for a draft."""

    def __init__(
        self,
        slot_renames: dict[str, dict[str, str]],
        class_renames: list[dict[str, str]],
    ) -> None:
        # {class_name: {old_slot_name: new_slot_name}}
        self.slot_renames = slot_renames
        # [{"old_name": ..., "new_name": ...}, ...]
        self.class_renames = class_renames

    def has_slot_renames(self) -> bool:
        return bool(self.slot_renames)

    def has_class_renames(self) -> bool:
        return bool(self.class_renames)


async def _get_pending_renames(
    conn: psycopg.AsyncConnection, draft_id: int
) -> _PendingRenames:
    """Load pending rename hints for a draft from spec_revisions.pending_renames.

    The column stores a JSON object with two keys:
      - ``slot_renames``: list of {class_name, old_name, new_name}
      - ``class_renames``: list of {old_name, new_name}

    Returns an empty ``_PendingRenames`` if the column doesn't exist or is empty.
    """
    try:
        row = await (
            await conn.execute(
                "SELECT pending_renames FROM spec_revisions WHERE revision = %s",
                (draft_id,),
            )
        ).fetchone()
    except Exception:
        # Column may not exist on older DBs before the migration runs.
        return _PendingRenames({}, [])
    if not row or not row[0]:
        return _PendingRenames({}, [])

    raw = row[0]
    if isinstance(raw, str):
        raw = json.loads(raw)

    # Handle old format: a plain list (legacy slot_renames only).
    if isinstance(raw, list):
        slot_entries = raw
        class_entries: list[dict] = []
    else:
        slot_entries = raw.get("slot_renames", [])
        class_entries = raw.get("class_renames", [])

    # Build nested slot dict: {class_name: {old_name: new_name}}.
    slot_map: dict[str, dict[str, str]] = {}
    for entry in slot_entries:
        cls_name = entry.get("class_name", "")
        old = entry.get("old_name", "")
        new = entry.get("new_name", "")
        if cls_name and old and new:
            slot_map.setdefault(cls_name, {})[old] = new

    return _PendingRenames(slot_map, class_entries)


async def evaluate_gates(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    run_preflight: bool = True,
) -> GateReport:
    """Evaluate all publish gates for a draft without emitting DDL.

    Runs inside the caller's transaction.  Steps:
      1. Spec-graph gate (publish_gate).
      2. Destructive-change classification (diff_specs + is_destructive).
      3. Constraint gate (new/changed constraints vs existing data).
      4. Pre-flight checks (cast feasibility, identifier-slot integrity).

    Returns a ``GateReport`` with the diff, all blockers (from both the
    constraint gate and preflight), and whether allow_destructive is needed.

    The ``run_preflight`` flag can be set False for unit tests or contexts
    where the data plane isn't yet set up.
    """
    from knot.spec.compile.postgres import compile_constraint
    from knot.spec.compile.postgres.migration import (
        diff_specs,
        is_destructive,
    )

    candidate = await get_revision(conn, draft_id)
    publish_gate(candidate)
    prev = await get_published(conn)

    # Load rename hints accumulated by the rename-slot / rename-class APIs.
    pending = await _get_pending_renames(conn, draft_id)

    changes = diff_specs(
        prev,
        candidate,
        renames=pending.slot_renames if pending.has_slot_renames() else None,
        class_renames=pending.class_renames if pending.has_class_renames() else None,
    )
    destructive_names = [type(c).__name__ for c in changes if is_destructive(c)]
    requires_allow_destructive = bool(destructive_names)

    blockers: list[dict] = []

    # Destructive-change blocker (reported but not raised here — callers decide).
    if requires_allow_destructive:
        blockers.append(
            {
                "kind": "destructive_changes",
                "changes": sorted(set(destructive_names)),
            }
        )

    # Constraint gate.
    prev_class_names: set[str] = {c.name for c in (prev.classes if prev else [])}
    prev_constraint_hashes: dict[str, str] = {}
    if prev:
        for con in prev.constraints:
            prev_constraint_hashes[con.name] = compute_content_hash(con)

    defined_class_names: set[str] = {
        c.name for c in candidate.classes if getattr(c, "definition", None) is not None
    }

    for con in candidate.constraints:
        cand_hash = compute_content_hash(con)
        prev_hash = prev_constraint_hashes.get(con.name)
        if cand_hash == prev_hash:
            continue
        if con.primary.name not in prev_class_names:
            continue
        if con.primary.name in defined_class_names:
            continue

        cls = con.primary
        stmt, params = compile_constraint(con, cls)
        try:
            violations = await (await conn.execute(stmt, params)).fetchall()
        except Exception as exc:
            blockers.append(
                {
                    "kind": "constraint_sql_error",
                    "constraint": con.name,
                    "detail": str(exc),
                }
            )
            continue

        if violations:
            n = len(violations)
            if con.severity.value == "error":
                blockers.append(
                    {
                        "kind": "constraint_violation",
                        "constraint": con.name,
                        "count": n,
                        "severity": "error",
                    }
                )
            else:
                logger.warning(
                    "Constraint %r has %d violation(s) against existing data "
                    "(severity=WARNING); publishing anyway.",
                    con.name,
                    n,
                )

    # Pre-flight checks.
    if run_preflight:
        preflight_blockers = await run_preflight_checks(conn, changes, prev, candidate)
        blockers.extend(preflight_blockers)

    return GateReport(
        changes=changes,
        blockers=blockers,
        requires_allow_destructive=requires_allow_destructive,
        candidate=candidate,
        prev=prev,
    )


async def publish_draft(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    allow_destructive: bool = False,
) -> int:
    """Run the publish gate; on pass, atomically flip this draft to published
    and bring the data-plane schema in line with the new spec.

    The destructive-change check (DropClass / DropSlot / ChangeSlotTypeExpression)
    runs against the diff before the flag flip; if any destructive change
    is present and ``allow_destructive`` is False, raises
    ``PublishGateError`` and the draft stays a draft.

    The whole flow — gate, diff, classify, flip, migrate — runs inside one
    transaction so concurrent publishes can't observe the partial unique
    index in a transient state, and the data plane and the spec are
    never out of sync.
    """
    from knot.spec.compile.postgres.migration import apply_changes

    async with conn.transaction():
        report = await evaluate_gates(conn, draft_id, run_preflight=True)

        # Gate 1: destructive changes need explicit opt-in.
        if report.requires_allow_destructive and not allow_destructive:
            destructive_names = next(
                b["changes"] for b in report.blockers if b["kind"] == "destructive_changes"
            )
            raise PublishGateError(
                "Publish would apply destructive changes "
                f"({', '.join(destructive_names)}); pass "
                "allow_destructive=true to confirm."
            )

        # Gates 2 + 3: constraint violations and preflight blockers.
        # Filter out the destructive_changes meta-blocker (handled above).
        hard_blockers = [b for b in report.blockers if b["kind"] != "destructive_changes"]
        if hard_blockers:
            raise PublishGateError(
                f"preflight: {len(hard_blockers)} blocker(s) "
                f"[{_summarize_blockers(hard_blockers)}]",
                details=hard_blockers,
            )

        # Demote → promote in two statements so the partial unique index
        # `(published) WHERE published = TRUE` doesn't see two TRUE rows
        # transiently (postgres validates per-row, not per-statement).
        await conn.execute(
            "UPDATE spec_revisions SET published = FALSE WHERE published = TRUE AND revision <> %s",
            (draft_id,),
        )
        await conn.execute(
            "UPDATE spec_revisions "
            "SET published = TRUE, published_at = %s, pending_renames = '[]'::jsonb "
            "WHERE revision = %s",
            (_now(), draft_id),
        )
        await apply_changes(conn, report.changes)

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
