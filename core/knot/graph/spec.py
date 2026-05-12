"""Spec orchestration — published reads, draft lifecycle, mutations, publish.

Composes ``knot.db.spec_store`` with ``knot.spec.expressions.translate_expr``
to handle:

  - Published-spec reads + revision history
  - Draft create / discard / list
  - Draft mutations (add slot/class, update class, add source/constraint)
  - Publish + rollback

Validation (collision checks, "is this slot/class on the draft", expression
translation) runs in this layer; the route catches typed exceptions and
maps to HTTP. The route layer keeps the Pydantic request/response shapes
and the per-entity summary helpers — those are HTTP-shape concerns.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import psycopg
from pydantic import BaseModel

from knot.db import spec_store
from knot.spec import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    NullSemantics,
    OntologyClass,
    PublishGateError,
    ResolutionPolicy,
    Slot,
    SlotMapping,
    Source,
    SourceBinding,
    Spec,
    compute_content_hash,
)
from knot.spec.expressions import ExprJson, ExprTranslationError, translate_expr
from knot.spec.metaschema import (
    Array,
    ClassRef,
    Constraint,
    Primitive,
    Severity,
    SlotConstraints,
    TypeExpression,
)

__all__ = (
    # Re-exports of spec-layer errors so callers in the api layer can import
    # from a single place alongside the orchestration helpers.
    "DraftAlreadyPublishedError",
    "DraftNotFoundError",
    "PublishGateError",
    # Local errors
    "CollisionError",
    "EntityNotOnDraftError",
    "ExprTranslationError",
    "InvalidTypeExprError",
    "InvalidIdentifierSlotError",
    "ReferencedEntityError",
    "RollbackToCurrentError",
    # Reads
    "get_published",
    "get_revision",
    "list_revisions",
    "list_drafts",
    "get_draft",
    # Draft lifecycle
    "create_draft",
    "discard_draft",
    # Mutations
    "add_slot",
    "add_class",
    "update_class",
    "add_source",
    "add_source_binding",
    "update_source_binding_trust",
    "add_constraint",
    "remove_slot",
    "remove_class",
    "remove_source",
    "remove_source_binding",
    "remove_constraint",
    "rename_slot",
    # Publish / rollback / preview
    "publish_draft",
    "rollback",
    "preview_publish",
    "PreviewResult",
)


# ─── Local exceptions ───────────────────────────────────────────────────────


class CollisionError(Exception):
    """Raised when a name collides (case-insensitive) with an existing entity."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"{kind} collides (case-insensitive) for name {name!r}.")


class EntityNotOnDraftError(Exception):
    """Raised when a referenced entity name isn't on the draft."""

    def __init__(self, kind: str, name: str) -> None:
        self.kind = kind
        self.name = name
        super().__init__(f"{kind} {name!r} not on this draft")


class InvalidTypeExprError(Exception):
    """Raised when a type expression descriptor is invalid."""


class InvalidIdentifierSlotError(Exception):
    """Raised when a Source's identifier_slot isn't on the entity_class."""


class ReferencedEntityError(Exception):
    """Raised when a draft entity can't be removed because others reference it."""

    def __init__(
        self,
        entity_kind: str,
        name: str,
        references: list[tuple[str, str]],
    ) -> None:
        self.entity_kind = entity_kind
        self.name = name
        self.references = references
        ref_summary = ", ".join(f"{k}={n}" for k, n in references[:5])
        super().__init__(
            f"Cannot remove {entity_kind} {name!r} — referenced by: {ref_summary}"
        )


class RollbackToCurrentError(Exception):
    """Raised when attempting to roll back to the currently-published revision."""


# ─── Local helpers — name lookups raise EntityNotOnDraftError ───────────────


def _find_class(spec: Spec, name: str) -> OntologyClass:
    for c in spec.classes:
        if c.name == name:
            return c
    raise EntityNotOnDraftError("OntologyClass", name)


def _find_slot(spec: Spec, name: str) -> Slot:
    for s in spec.slots:
        if s.name == name:
            return s
    raise EntityNotOnDraftError("Slot", name)


def _find_source(spec: Spec, name: str) -> Source:
    for s in spec.sources:
        if s.name == name:
            return s
    raise EntityNotOnDraftError("Source", name)


def _find_constraint(spec: Spec, name: str) -> Constraint:
    for c in spec.constraints:
        if c.name == name:
            return c
    raise EntityNotOnDraftError("Constraint", name)


def _build_type_expr(
    type_kind: str | None,
    type_name: str | None,
    spec: Spec,
) -> TypeExpression | None:
    """Build a TypeExpression from the API's (type_kind, type_name) descriptor.

    type_kind values:
      "primitive"  — type_name must be a STANDARD_PRIMITIVE_NAMES entry
      "class"      — type_name must be an OntologyClass on the draft
      "array"      — not a top-level kind; handled via array_of_kind + array_of_name
      None         — no type (derived slot)
    """
    if type_kind is None:
        return None
    if type_kind == "primitive":
        from knot.spec.metaschema import STANDARD_PRIMITIVE_NAMES

        if type_name is None or type_name not in STANDARD_PRIMITIVE_NAMES:
            raise InvalidTypeExprError(
                f"type_kind='primitive' requires type_name in {STANDARD_PRIMITIVE_NAMES!r}"
            )
        return Primitive(name=type_name)
    if type_kind == "class":
        if type_name is None:
            raise InvalidTypeExprError("type_kind='class' requires type_name")
        cls = _find_class(spec, type_name)
        return ClassRef(target_class=cls)
    if type_kind == "array_of_primitive":
        from knot.spec.metaschema import STANDARD_PRIMITIVE_NAMES

        if type_name is None or type_name not in STANDARD_PRIMITIVE_NAMES:
            raise InvalidTypeExprError(
                f"type_kind='array_of_primitive' requires type_name in {STANDARD_PRIMITIVE_NAMES!r}"
            )
        return Array(of=Primitive(name=type_name))
    if type_kind == "array_of_class":
        if type_name is None:
            raise InvalidTypeExprError("type_kind='array_of_class' requires type_name")
        cls = _find_class(spec, type_name)
        return Array(of=ClassRef(target_class=cls))
    raise InvalidTypeExprError(
        f"type_kind must be 'primitive', 'class', 'array_of_primitive', "
        f"'array_of_class', or null; got {type_kind!r}"
    )


# ─── Reads ──────────────────────────────────────────────────────────────────


async def get_published(conn: psycopg.AsyncConnection) -> Spec | None:
    """Currently-published spec, or None if none published."""
    return await spec_store.get_published(conn)


async def get_revision(conn: psycopg.AsyncConnection, revision: int) -> Spec:
    """Spec at a specific revision (draft or published).

    Raises ``DraftNotFoundError`` if the revision doesn't exist.
    """
    return await spec_store.get_revision(conn, revision)


async def list_revisions(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    """All published revisions, newest first."""
    return await spec_store.list_published(conn)


async def list_drafts(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    """All unpublished draft summaries."""
    return await spec_store.list_drafts(conn)


async def get_draft(conn: psycopg.AsyncConnection, draft_id: int) -> Spec:
    """A specific draft. Raises ``DraftNotFoundError`` if missing."""
    return await spec_store.get_revision(conn, draft_id)


# ─── Draft lifecycle ────────────────────────────────────────────────────────


async def create_draft(
    conn: psycopg.AsyncConnection,
    *,
    parent_revision: int | None,
    label: str | None,
) -> int:
    """Create a draft.

    ``parent_revision=None`` branches from the latest published revision.
    Pass an explicit revision number to branch from a specific historical
    revision. If nothing has been published yet, the draft starts empty.

    Raises ``DraftNotFoundError`` if ``parent_revision`` is given but missing.
    """
    if parent_revision is None:
        parent_revision = await spec_store.get_published_revision(conn)
    return await spec_store.create_draft(conn, parent_revision=parent_revision, label=label)


async def discard_draft(conn: psycopg.AsyncConnection, draft_id: int) -> None:
    """Delete an unpublished draft. Raises ``DraftNotFoundError`` or
    ``DraftAlreadyPublishedError``."""
    await spec_store.discard_draft(conn, draft_id)


# ─── Mutations ──────────────────────────────────────────────────────────────


async def add_slot(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    name: str,
    type_kind: str | None,
    type_name: str | None,
    identifier: bool,
    required: bool,
    resolution_policy: ResolutionPolicy,
    constraints: SlotConstraints | None,
    description: str | None,
    derivation: ExprJson | None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(s.name.lower() == name.lower() for s in spec.slots):
            raise CollisionError("Slot", name)

        type_expr = _build_type_expr(type_kind, type_name, spec)

        derivation_obj = None
        if derivation is not None:
            placeholder_primary = OntologyClass(name="__derivation_ctx__")
            derivation_obj = translate_expr(derivation, spec, placeholder_primary)

        spec.slots.append(
            Slot(
                name=name,
                type=type_expr,
                identifier=identifier,
                required=required,
                resolution_policy=resolution_policy,
                constraints=constraints,
                description=description,
                derivation=derivation_obj,
            )
        )
    return spec


async def add_class(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    name: str,
    slot_names: list[str],
    is_a_name: str | None,
    mixin_names: list[str],
    abstract: bool,
    description: str | None,
    definition: ExprJson | None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(c.name.lower() == name.lower() for c in spec.classes):
            raise CollisionError("OntologyClass", name)

        slots = [_find_slot(spec, n) for n in slot_names]
        is_a = _find_class(spec, is_a_name) if is_a_name else None
        mixins = [_find_class(spec, n) for n in mixin_names]

        definition_obj = None
        if definition is not None:
            primary = (
                is_a
                if is_a is not None
                else _find_class(spec, name)
                if any(c.name == name for c in spec.classes)
                else OntologyClass(name=name)
            )
            definition_obj = translate_expr(definition, spec, primary)

        spec.classes.append(
            OntologyClass(
                name=name,
                slots=slots,
                is_a=is_a,
                mixins=mixins,
                abstract=abstract,
                description=description,
            )
        )
    return spec


async def update_class(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    name: str,
    *,
    slot_names: list[str] | None = None,
    is_a_name: str | None = None,
    mixin_names: list[str] | None = None,
    abstract: bool | None = None,
    description: str | None = None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        cls = _find_class(spec, name)

        if slot_names is not None:
            cls.slots = [_find_slot(spec, n) for n in slot_names]
        if is_a_name is not None:
            cls.is_a = _find_class(spec, is_a_name) if is_a_name else None
        if mixin_names is not None:
            cls.mixins = [_find_class(spec, n) for n in mixin_names]
        if abstract is not None:
            cls.abstract = abstract
        if description is not None:
            cls.description = description
    return spec


async def add_source(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    name: str,
    description: str | None = None,
) -> Spec:
    """Add a thin Source (name + description only).

    The per-class relationship metadata lives on ``SourceBinding``.
    Use ``add_source_binding`` after creating the source.
    """
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(s.name.lower() == name.lower() for s in spec.sources):
            raise CollisionError("Source", name)
        spec.sources.append(Source(name=name, description=description))
    return spec


def _find_source_binding(spec: Spec, source_name: str, class_name: str) -> SourceBinding:
    """Look up a SourceBinding by (source_name, class_name).

    Raises ``EntityNotOnDraftError`` if no matching binding is found.
    """
    for b in spec.source_bindings:
        if b.source.name == source_name and b.class_.name == class_name:
            return b
    raise EntityNotOnDraftError("SourceBinding", f"{source_name}__{class_name}")


async def add_source_binding(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    source_name: str,
    class_name: str,
    identifier_slot_name: str,
    mappings: list[dict] | None = None,
    trust_prior: tuple[float, float] = (1.0, 1.0),
    required_slot_names: list[str] | None = None,
    description: str | None = None,
) -> Spec:
    """Add a SourceBinding (source, class) to the draft.

    ``mappings`` is a list of dicts with keys:
      slot_name, source_field, default (optional), null_semantics (optional), prior (optional).

    ``identifier_slot_name`` must be a slot on the class (via effective_slots).
    """
    from knot.spec import effective_slots as _effective_slots

    async with spec_store.edit_draft(conn, draft_id) as spec:
        src = _find_source(spec, source_name)
        cls = _find_class(spec, class_name)

        # Check for duplicate binding
        if any(b.source.name == source_name and b.class_.name == class_name
               for b in spec.source_bindings):
            raise CollisionError("SourceBinding", f"{source_name}__{class_name}")

        # Resolve identifier_slot from effective slots (includes inherited)
        all_slots = {s.name: s for s in _effective_slots(cls)}
        id_slot = all_slots.get(identifier_slot_name)
        if id_slot is None:
            raise InvalidIdentifierSlotError(
                f"Slot {identifier_slot_name!r} is not on class {cls.name!r}"
            )

        # Build SlotMapping objects
        slot_mappings: list[SlotMapping] = []
        for m in (mappings or []):
            slot = all_slots.get(m["slot_name"])
            if slot is None:
                raise InvalidIdentifierSlotError(
                    f"Slot {m['slot_name']!r} is not on class {cls.name!r}"
                )
            null_sem_val = m.get("null_semantics", NullSemantics.NO_CLAIM)
            if isinstance(null_sem_val, str):
                null_sem_val = NullSemantics(null_sem_val)
            prior_raw = m.get("prior")
            prior: tuple[float, float] | None = None
            if prior_raw is not None:
                prior = (float(prior_raw[0]), float(prior_raw[1]))
            slot_mappings.append(SlotMapping(
                slot=slot,
                source_field=m.get("source_field", slot.name),
                default=m.get("default"),
                null_semantics=null_sem_val,
                prior=prior,
            ))

        # Resolve required slots
        req_slots: list[Slot] = []
        for rname in (required_slot_names or []):
            rslot = all_slots.get(rname)
            if rslot is None:
                raise InvalidIdentifierSlotError(
                    f"required_slot {rname!r} is not on class {cls.name!r}"
                )
            req_slots.append(rslot)

        spec.source_bindings.append(
            SourceBinding(
                source=src,
                class_=cls,  # type: ignore[call-arg]  # populate_by_name=True allows class_= at runtime
                identifier_slot=id_slot,
                mappings=slot_mappings,
                trust_prior=trust_prior,
                required_slots=req_slots,
                description=description,
            )
        )
    return spec


async def update_source_binding_trust(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    source_name: str,
    class_name: str,
    *,
    trust_prior: tuple[float, float],
) -> Spec:
    """Update the trust_prior for a SourceBinding (RUNTIME — no content hash change)."""
    async with spec_store.edit_draft(conn, draft_id) as spec:
        binding = _find_source_binding(spec, source_name, class_name)
        binding.trust_prior = trust_prior
    return spec


async def add_constraint(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    name: str,
    primary_class_name: str,
    body: ExprJson,
    severity: Severity,
    message: str | None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(c.name.lower() == name.lower() for c in spec.constraints):
            raise CollisionError("Constraint", name)

        primary = _find_class(spec, primary_class_name)
        expr = translate_expr(body, spec, primary)

        spec.constraints.append(
            Constraint(
                name=name,
                primary=primary,
                body=expr,
                severity=severity,
                message=message,
            )
        )
    return spec


# ─── Removals ───────────────────────────────────────────────────────────────


async def remove_slot(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    name: str,
) -> Spec:
    """Remove a Slot by name.

    Raises ``ReferencedEntityError`` if any class lists this slot, or any
    source binding uses it as ``identifier_slot``.
    """
    async with spec_store.edit_draft(conn, draft_id) as spec:
        target = _find_slot(spec, name)
        refs: list[tuple[str, str]] = []
        for c in spec.classes:
            if any(s is target for s in c.slots):
                refs.append(("class", c.name))
        for b in spec.source_bindings:
            if b.identifier_slot is target:
                refs.append(("source_binding", b.binding_id))
        if refs:
            raise ReferencedEntityError("slot", name, refs)
        spec.slots = [s for s in spec.slots if s is not target]
    return spec


async def rename_slot(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    old_name: str,
    new_name: str,
) -> Spec:
    """Rename a slot on a draft, recording a rename hint for publish time.

    The slot is renamed in-memory across the full spec (slot list, every
    class that references it, every source whose identifier_slot is it).
    A rename hint ``{class_name, old_name, new_name}`` is appended to
    ``spec_revisions.pending_renames`` for every concrete class that has
    the slot as a stored column, so ``diff_specs`` at publish time can emit
    a non-destructive ``RenameSlot`` instead of ``DropSlot + AddSlot``.

    Raises ``CollisionError`` if ``new_name`` already exists on the draft.
    Raises ``EntityNotOnDraftError`` if ``old_name`` isn't on the draft.
    Raises ``DraftAlreadyPublishedError`` if the draft is already published.
    """
    import json as _json

    from knot.spec import effective_slots as _effective_slots
    from knot.spec import is_stored as _is_stored

    async with spec_store.edit_draft(conn, draft_id) as spec:
        # Validate
        if any(s.name.lower() == new_name.lower() for s in spec.slots):
            raise CollisionError("Slot", new_name)
        target = _find_slot(spec, old_name)

        # Collect the concrete classes that have this as a stored column —
        # these are the classes that will get a RenameSlot DDL record.
        rename_hints: list[dict] = []
        for cls in spec.classes:
            if cls.abstract:
                continue
            stored_names = {s.name for s in _effective_slots(cls) if _is_stored(s)}
            if old_name in stored_names:
                rename_hints.append(
                    {
                        "class_name": cls.name,
                        "old_name": old_name,
                        "new_name": new_name,
                    }
                )

        # Rename the slot in-memory.
        target.name = new_name

    # Append rename hints to pending_renames OUTSIDE the edit_draft context
    # (which has already committed the spec update above).
    if rename_hints:
        # Read existing hints, append new ones, write back.
        existing_row = await (
            await conn.execute(
                "SELECT pending_renames FROM spec_revisions WHERE revision = %s",
                (draft_id,),
            )
        ).fetchone()
        existing: list[dict] = []
        if existing_row and existing_row[0]:
            raw = existing_row[0]
            existing = raw if isinstance(raw, list) else _json.loads(raw)
        merged = existing + rename_hints
        await conn.execute(
            "UPDATE spec_revisions SET pending_renames = %s WHERE revision = %s",
            (_json.dumps(merged), draft_id),
        )

    return spec


async def remove_class(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    name: str,
) -> Spec:
    """Remove an OntologyClass by name.

    Raises ``ReferencedEntityError`` if any slot's type references this class
    (via ClassRef), any other class names it via ``is_a`` or ``mixins``, any
    source binding's ``class_`` is this class, or any constraint's ``primary``
    is this class.
    """
    from knot.spec.metaschema import Array, ClassRef

    async with spec_store.edit_draft(conn, draft_id) as spec:
        target = _find_class(spec, name)
        refs: list[tuple[str, str]] = []

        def _type_refs_class(type_expr: Any, cls: OntologyClass) -> bool:
            if isinstance(type_expr, ClassRef):
                return type_expr.target_class is cls
            if isinstance(type_expr, Array):
                return _type_refs_class(type_expr.of, cls)
            return False

        for s in spec.slots:
            if s.type is not None and _type_refs_class(s.type, target):
                refs.append(("slot", s.name))
        for c in spec.classes:
            if c is target:
                continue
            if c.is_a is target:
                refs.append(("class.is_a", c.name))
            if any(m is target for m in c.mixins):
                refs.append(("class.mixin", c.name))
        for b in spec.source_bindings:
            if b.class_ is target:
                refs.append(("source_binding", b.binding_id))
        for con in spec.constraints:
            if con.primary is target:
                refs.append(("constraint", con.name))
        if refs:
            raise ReferencedEntityError("class", name, refs)
        spec.classes = [c for c in spec.classes if c is not target]
    return spec


async def remove_source(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    name: str,
) -> Spec:
    """Remove a Source by name, cascade-dropping all its SourceBindings.

    Sources are only referenced by SourceBindings; the bindings are dropped
    together with the source rather than blocking removal.
    """
    async with spec_store.edit_draft(conn, draft_id) as spec:
        target = _find_source(spec, name)
        spec.source_bindings = [
            b for b in spec.source_bindings if b.source is not target
        ]
        spec.sources = [s for s in spec.sources if s is not target]
    return spec


async def remove_source_binding(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    source_name: str,
    class_name: str,
) -> Spec:
    """Remove a single SourceBinding by (source_name, class_name).

    Raises ``EntityNotOnDraftError`` if the binding doesn't exist.
    """
    async with spec_store.edit_draft(conn, draft_id) as spec:
        target = _find_source_binding(spec, source_name, class_name)
        spec.source_bindings = [b for b in spec.source_bindings if b is not target]
    return spec


async def remove_constraint(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    name: str,
) -> Spec:
    """Remove a Constraint by name. Constraints can't be referenced by other
    entities, so no inbound-reference check is needed."""
    async with spec_store.edit_draft(conn, draft_id) as spec:
        target = _find_constraint(spec, name)
        spec.constraints = [c for c in spec.constraints if c is not target]
    return spec


# ─── Publish / rollback ─────────────────────────────────────────────────────


async def publish_draft(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Publish a draft. Returns ``{revision, content_hash, published_at}``.

    Raises ``DraftNotFoundError`` if missing, ``PublishGateError`` on gate
    failure or destructive-without-flag.
    """
    await spec_store.publish_draft(conn, draft_id, allow_destructive=allow_destructive)
    rows = await spec_store.list_published(conn)
    row = next(r for r in rows if r["revision"] == draft_id)
    return {
        "revision": draft_id,
        "content_hash": row["content_hash"],
        "published_at": row["published_at"] or "",
    }


async def rollback(
    conn: psycopg.AsyncConnection,
    target_revision: int,
    *,
    allow_destructive: bool = False,
) -> dict[str, Any]:
    """Roll back to a prior revision. Mechanically identical to publish:
    diff (current → target) is applied to the data plane, gate runs, flag
    flips. Rejects rollback to the currently-published revision (no-op).

    Raises ``RollbackToCurrentError`` for the no-op case;
    ``DraftNotFoundError`` if the target doesn't exist;
    ``PublishGateError`` on gate failure.
    """
    current = await spec_store.get_published_revision(conn)
    if current == target_revision:
        raise RollbackToCurrentError(
            f"Revision {target_revision} is already the published spec; nothing to roll back to."
        )
    await spec_store.publish_draft(conn, target_revision, allow_destructive=allow_destructive)
    rows = await spec_store.list_published(conn)
    row = next(r for r in rows if r["revision"] == target_revision)
    return {
        "revision": target_revision,
        "content_hash": row["content_hash"],
        "published_at": row["published_at"] or "",
    }


# ─── Helper: full spec content_hash for response ────────────────────────────


def content_hash(spec: Spec) -> str:
    """Convenience re-export for routes that build mutation responses."""
    return compute_content_hash(spec)


# ─── Preview ─────────────────────────────────────────────────────────────────


class PreviewResult(BaseModel):
    """What publish would do, without doing it."""

    draft_revision: int
    changes: list[dict]  # serialized Change records
    buckets: dict[str, list[str]]  # {"A": [...], "B": [...], "C": [...]}
    blockers: list[dict]  # what would fail Gate 1, 2, or 3 right now
    publishable: bool  # True iff blockers is empty
    requires_allow_destructive: bool  # True if any Bucket A change present


async def preview_publish(
    conn: psycopg.AsyncConnection,
    draft_id: int,
) -> PreviewResult:
    """Evaluate all publish gates for a draft without emitting DDL.

    Returns a ``PreviewResult`` describing what would happen if
    ``publish_draft`` were called right now.

    Raises ``DraftNotFoundError`` if the draft doesn't exist.
    Raises ``PublishGateError`` only if the spec-graph gate (Step 1)
    fails — structural errors that indicate a malformed spec, not data
    blockers (those go into the ``blockers`` list instead).
    """
    from knot.spec.compile.postgres.migration import (
        Change,
        ChangeClassAbstract,
        ChangeClassIsA,
        ChangeConstraintBody,
        ChangeConstraintPrimary,
        ChangeSlotMaximum,
        ChangeSlotMinimum,
        ChangeSlotPattern,
        ChangeSlotPermissibleValues,
        ChangeSlotTypeExpression,
        ChangeSourceBindingIdentifierSlot,
        DropClass,
        DropDefinedClass,
        DropSlot,
        DropSource,
        DropSourceBinding,
    )

    # Bucket classification (independent of is_destructive — Bucket B and C
    # changes are not in _DESTRUCTIVE_CHANGE_TYPES).
    _BUCKET_B: tuple[type[Change], ...] = (
        ChangeSlotPattern,
        ChangeSlotPermissibleValues,
        ChangeSlotMinimum,
        ChangeSlotMaximum,
        ChangeConstraintBody,
        ChangeConstraintPrimary,
    )
    _BUCKET_A: tuple[type[Change], ...] = (
        DropClass,
        DropSlot,
        DropDefinedClass,
        ChangeSlotTypeExpression,
        ChangeClassAbstract,
        ChangeClassIsA,
        DropSource,
        DropSourceBinding,
        ChangeSourceBindingIdentifierSlot,
    )

    async with conn.transaction():
        report = await spec_store.evaluate_gates(conn, draft_id, run_preflight=True)

    changes = report.changes
    blockers = report.blockers

    # Serialize changes.
    serialized_changes: list[dict] = []
    for c in changes:
        try:
            d = dataclasses.asdict(c)
        except TypeError:
            d = {}
        serialized_changes.append({"type": type(c).__name__, **d})

    # Bucket classification.
    bucket_a: list[str] = []
    bucket_b: list[str] = []
    bucket_c: list[str] = []
    for c in changes:
        name = type(c).__name__
        if isinstance(c, _BUCKET_A):
            bucket_a.append(name)
        elif isinstance(c, _BUCKET_B):
            bucket_b.append(name)
        else:
            bucket_c.append(name)

    return PreviewResult(
        draft_revision=draft_id,
        changes=serialized_changes,
        buckets={"A": bucket_a, "B": bucket_b, "C": bucket_c},
        blockers=blockers,
        publishable=len(blockers) == 0,
        requires_allow_destructive=report.requires_allow_destructive,
    )
