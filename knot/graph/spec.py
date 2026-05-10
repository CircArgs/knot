"""Spec orchestration — published reads, draft lifecycle, mutations, publish.

Composes ``knot.db.spec_store`` with ``knot.spec.expressions.translate_expr``
to handle:

  - Published-spec reads + revision history
  - Draft create / discard / list
  - Draft mutations (add type/slot/class, update class, add source/constraint)
  - Publish + rollback

Validation (collision checks, "is this slot/class on the draft", expression
translation) runs in this layer; the route catches typed exceptions and
maps to HTTP. The route layer keeps the Pydantic request/response shapes
and the per-entity summary helpers — those are HTTP-shape concerns.
"""

from __future__ import annotations

from typing import Any

import psycopg

from knot.db import spec_store
from knot.spec import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    OntologyClass,
    PermissibleValue,
    PublishGateError,
    ResolutionPolicy,
    Slot,
    Source,
    Spec,
    TypeDefinition,
    compute_content_hash,
)
from knot.spec.expressions import ExprJson, ExprTranslationError, translate_expr
from knot.spec.metaschema import Constraint, Severity

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
    "InvalidRangeKindError",
    "InvalidIdentifierSlotError",
    "RollbackToCurrentError",
    # Reads
    "get_published",
    "get_revision",
    "list_revisions",
    "list_drafts",
    "get_draft",
    # Draft lifecycle
    "bootstrap_base_spec",
    "create_draft",
    "discard_draft",
    # Mutations
    "add_type",
    "add_slot",
    "add_class",
    "update_class",
    "add_source",
    "add_constraint",
    # Publish / rollback
    "publish_draft",
    "rollback",
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


class InvalidRangeKindError(Exception):
    """Raised when range_kind is invalid or range_name is missing."""


class InvalidIdentifierSlotError(Exception):
    """Raised when a Source's identifier_slot isn't on the entity_class."""


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


def _find_type(spec: Spec, name: str) -> TypeDefinition:
    for t in spec.types:
        if t.name == name:
            return t
    raise EntityNotOnDraftError("TypeDefinition", name)


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

    ``parent_revision=None`` (the default) branches from the latest published
    revision — so authors get the standard primitives (and any prior published
    content) via lineage. Pass an explicit revision number to branch from a
    specific historical revision. If nothing has been published yet (this
    shouldn't happen post-bootstrap, but defend against it), the draft starts
    empty.

    Raises ``DraftNotFoundError`` if ``parent_revision`` is given but missing.
    """
    if parent_revision is None:
        parent_revision = await spec_store.get_published_revision(conn)
    return await spec_store.create_draft(conn, parent_revision=parent_revision, label=label)


async def bootstrap_base_spec(conn: psycopg.AsyncConnection) -> None:
    """Publish the standard-primitives base spec if no published revision exists.

    Idempotent: if anything is already published, this is a no-op. The base
    spec carries the six standard primitives (string/integer/float/boolean/
    datetime/date); new drafts branch from it by default so authors don't
    have to register them per-spec.
    """
    existing = await spec_store.get_published_revision(conn)
    if existing is not None:
        return
    from knot.spec.metaschema import Spec
    from knot.spec.primitives import STANDARD_PRIMITIVES

    base_spec = Spec(
        id="knot.base",
        version="1.0.0",
        types=list(STANDARD_PRIMITIVES),
        slots=[],
        classes=[],
        sources=[],
        constraints=[],
    )
    draft_id = await spec_store.create_draft(conn, parent_revision=None, label="knot.base")
    await spec_store.update_draft(conn, draft_id, base_spec)
    await spec_store.publish_draft(conn, draft_id, allow_destructive=False)


async def discard_draft(conn: psycopg.AsyncConnection, draft_id: int) -> None:
    """Delete an unpublished draft. Raises ``DraftNotFoundError`` or
    ``DraftAlreadyPublishedError``."""
    await spec_store.discard_draft(conn, draft_id)


# ─── Mutations ──────────────────────────────────────────────────────────────


async def add_type(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    name: str,
    base: str | None,
    pattern: str | None,
    description: str | None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(t.name.lower() == name.lower() for t in spec.types):
            raise CollisionError("TypeDefinition", name)
        spec.types.append(
            TypeDefinition(name=name, base=base, pattern=pattern, description=description)
        )
    return spec


async def add_slot(
    conn: psycopg.AsyncConnection,
    draft_id: int,
    *,
    name: str,
    range_kind: str | None,
    range_name: str | None,
    identifier: bool,
    required: bool,
    multivalued: bool,
    resolution_policy: ResolutionPolicy,
    pattern: str | None,
    minimum_value: float | None,
    maximum_value: float | None,
    permissible_values: list[str] | None,
    description: str | None,
    derivation: ExprJson | None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(s.name.lower() == name.lower() for s in spec.slots):
            raise CollisionError("Slot", name)

        range_obj: Any | None = None
        if range_kind == "type":
            if range_name is None:
                raise InvalidRangeKindError("range_kind='type' requires range_name")
            range_obj = _find_type(spec, range_name)
        elif range_kind == "class":
            if range_name is None:
                raise InvalidRangeKindError("range_kind='class' requires range_name")
            range_obj = _find_class(spec, range_name)
        elif range_kind is not None:
            raise InvalidRangeKindError(
                f"range_kind must be 'type', 'class', or null; got {range_kind!r}"
            )

        permissible = None
        if permissible_values is not None:
            permissible = [PermissibleValue(text=t) for t in permissible_values]

        derivation_obj = None
        if derivation is not None:
            placeholder_primary = OntologyClass(name="__derivation_ctx__")
            derivation_obj = translate_expr(derivation, spec, placeholder_primary)

        spec.slots.append(
            Slot(
                name=name,
                range=range_obj,
                identifier=identifier,
                required=required,
                multivalued=multivalued,
                resolution_policy=resolution_policy,
                pattern=pattern,
                minimum_value=minimum_value,
                maximum_value=maximum_value,
                permissible_values=permissible,
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
                definition=definition_obj,
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
    entity_class_name: str,
    identifier_slot_name: str,
    description: str | None,
) -> Spec:
    async with spec_store.edit_draft(conn, draft_id) as spec:
        if any(s.name.lower() == name.lower() for s in spec.sources):
            raise CollisionError("Source", name)

        cls = _find_class(spec, entity_class_name)
        slot = next((s for s in cls.slots if s.name == identifier_slot_name), None)
        if slot is None:
            raise InvalidIdentifierSlotError(
                f"Slot {identifier_slot_name!r} is not on class {cls.name!r}"
            )

        spec.sources.append(
            Source(
                name=name,
                entity_class=cls,
                identifier_slot=slot,
                description=description,
            )
        )
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
