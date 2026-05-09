"""Spec router — ontology graph authoring + publish gate.

Endpoint groups under `/spec`:
  - /spec/published/*   read the currently active spec graph
  - /spec/revisions/*   browse published-revision history (immortal audit chain)
  - /spec/drafts/*      author the next spec via mutation + publish gate

Drafts are mutable; published revisions are immortal.  Every spec mutation
must go through a draft per `goals.md` § 5.  The publish gate runs spec-graph
validation (steps 1+2 of the four-step gate); bindings-side checks (steps 3+4)
land with the modeling router.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from knot import db
from knot.api.auth.security import require_user
from knot.api.constraint_translator import ExprJson, translate_expr
from knot.db import spec_store
from knot.spec import (
    OntologyClass,
    PermissibleValue,
    ResolutionPolicy,
    Slot,
    Source,
    Spec,
    TypeDefinition,
    compute_content_hash,
)
from knot.spec.metaschema import Constraint, Severity

# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── Read summaries ─────────────────────────────────────────────────────────


class TypeSummary(_StrictBase):
    name: str
    base: str | None
    pattern: str | None


class SlotSummary(_StrictBase):
    name: str
    range_kind: str | None  # "type" | "class" | None
    range_name: str | None
    identifier: bool
    required: bool
    multivalued: bool
    resolution_policy: str


class ClassSummary(_StrictBase):
    name: str
    slots: list[str]
    is_a: str | None
    mixins: list[str]
    abstract: bool


class SourceSummary(_StrictBase):
    name: str
    entity_class: str
    identifier_slot: str
    description: str | None


class RevisionSummary(_StrictBase):
    revision: int
    content_hash: str
    label: str | None
    parent_revision: int | None
    created_at: str
    published_at: str | None = None


# ─── Mutation requests ──────────────────────────────────────────────────────

_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"


class TypeDefinitionCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    base: str | None = None
    pattern: str | None = None
    description: str | None = None


class SlotCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    range_kind: str | None = None  # "type" | "class" | None
    range_name: str | None = None
    identifier: bool = False
    required: bool = False
    multivalued: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    pattern: str | None = None
    minimum_value: float | None = None
    maximum_value: float | None = None
    permissible_values: list[str] | None = None
    description: str | None = None
    derivation: ExprJson | None = None


class ClassCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    slot_names: list[str] = Field(default_factory=list)
    is_a_name: str | None = None
    mixin_names: list[str] = Field(default_factory=list)
    abstract: bool = False
    description: str | None = None
    definition: ExprJson | None = None


class ClassUpdate(_StrictBase):
    slot_names: list[str] | None = None
    is_a_name: str | None = None
    mixin_names: list[str] | None = None
    abstract: bool | None = None
    description: str | None = None


class SourceCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    entity_class_name: str
    identifier_slot_name: str
    description: str | None = None


class ConstraintCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    primary_class_name: str
    body: ExprJson
    severity: Severity = Severity.ERROR
    message: str | None = None


class DraftCreate(_StrictBase):
    parent_revision: int | None = None
    label: str | None = None


# ─── Response shapes ────────────────────────────────────────────────────────


class DraftSummary(_StrictBase):
    revision: int
    label: str | None
    parent_revision: int | None
    content_hash: str
    created_at: str


class MutationResponse(_StrictBase):
    """Returned by every draft mutation endpoint."""

    draft_revision: int
    content_hash: str
    spec_summary: dict[str, int]  # {classes: n, slots: n, types: n, sources: n}


class PublishResponse(_StrictBase):
    revision: int
    content_hash: str
    published_at: str


# ---------------------------------------------------------------------------
# Helpers — find entities in a draft Spec by name
# ---------------------------------------------------------------------------


def _find_class(spec: Spec, name: str) -> OntologyClass:
    for c in spec.classes:
        if c.name == name:
            return c
    raise HTTPException(404, f"OntologyClass {name!r} not on this draft")


def _find_slot(spec: Spec, name: str) -> Slot:
    for s in spec.slots:
        if s.name == name:
            return s
    raise HTTPException(404, f"Slot {name!r} not on this draft")


def _find_type(spec: Spec, name: str) -> TypeDefinition:
    for t in spec.types:
        if t.name == name:
            return t
    raise HTTPException(404, f"TypeDefinition {name!r} not on this draft")


def _spec_summary(spec: Spec) -> dict[str, int]:
    return {
        "classes": len(spec.classes),
        "slots": len(spec.slots),
        "types": len(spec.types),
        "sources": len(spec.sources),
        "constraints": len(spec.constraints),
    }


def _response(draft_id: int, spec: Spec) -> MutationResponse:
    """Build the mutation response after ``edit_draft`` has written back."""
    return MutationResponse(
        draft_revision=draft_id,
        content_hash=compute_content_hash(spec),
        spec_summary=_spec_summary(spec),
    )


def _summarize_class(c: OntologyClass) -> ClassSummary:
    return ClassSummary(
        name=c.name,
        slots=[s.name for s in c.slots],
        is_a=c.is_a.name if c.is_a else None,
        mixins=[m.name for m in c.mixins],
        abstract=c.abstract,
    )


def _summarize_slot(s: Slot) -> SlotSummary:
    range_kind: str | None = None
    range_name: str | None = None
    if isinstance(s.range, OntologyClass):
        range_kind, range_name = "class", s.range.name
    elif isinstance(s.range, TypeDefinition):
        range_kind, range_name = "type", s.range.name
    rp = s.resolution_policy
    return SlotSummary(
        name=s.name,
        range_kind=range_kind,
        range_name=range_name,
        identifier=s.identifier,
        required=s.required,
        multivalued=s.multivalued,
        resolution_policy=rp.value if hasattr(rp, "value") else str(rp),
    )


def _summarize_type(t: TypeDefinition) -> TypeSummary:
    return TypeSummary(name=t.name, base=t.base, pattern=t.pattern)


def _summarize_source(s: Source) -> SourceSummary:
    return SourceSummary(
        name=s.name,
        entity_class=s.entity_class.name,
        identifier_slot=s.identifier_slot.name,
        description=s.description,
    )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/spec", tags=["spec"])


# ─── Published reads ────────────────────────────────────────────────────────


@router.get("/published", summary="Full currently-published spec (cycle-safe JSON)")
def get_published_spec() -> dict[str, Any]:
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
    if spec is None:
        raise HTTPException(404, "No spec is currently published.")
    return spec_store.spec_to_dict(spec)


@router.get("/published/classes", response_model=list[ClassSummary])
def list_published_classes() -> list[ClassSummary]:
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
    if spec is None:
        return []
    return [_summarize_class(c) for c in spec.classes]


@router.get("/published/classes/{name}", response_model=ClassSummary)
def get_published_class(name: str) -> ClassSummary:
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
    if spec is None:
        raise HTTPException(404, "No spec is currently published.")
    return _summarize_class(_find_class(spec, name))


@router.get("/published/slots", response_model=list[SlotSummary])
def list_published_slots() -> list[SlotSummary]:
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
    if spec is None:
        return []
    return [_summarize_slot(s) for s in spec.slots]


@router.get("/published/types", response_model=list[TypeSummary])
def list_published_types() -> list[TypeSummary]:
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
    if spec is None:
        return []
    return [_summarize_type(t) for t in spec.types]


@router.get("/published/sources", response_model=list[SourceSummary])
def list_published_sources() -> list[SourceSummary]:
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
    if spec is None:
        return []
    return [_summarize_source(s) for s in spec.sources]


# ─── Revision history ───────────────────────────────────────────────────────


@router.get("/revisions", response_model=list[RevisionSummary])
def list_revisions() -> list[RevisionSummary]:
    """All published revisions, newest first (immortal audit chain)."""
    with db.connect() as conn:
        rows = spec_store.list_published(conn)
    return [RevisionSummary(**r) for r in rows]


@router.get("/revisions/{revision}")
def get_revision_spec(revision: int) -> dict[str, Any]:
    with db.connect() as conn:
        try:
            spec = spec_store.get_revision(conn, revision)
        except spec_store.DraftNotFoundError as exc:
            raise HTTPException(404, f"Revision {revision} not found.") from exc
    return spec_store.spec_to_dict(spec)


# ─── Drafts ─────────────────────────────────────────────────────────────────


@router.get("/drafts", response_model=list[DraftSummary])
def list_drafts_endpoint() -> list[DraftSummary]:
    with db.connect() as conn:
        rows = spec_store.list_drafts(conn)
    return [DraftSummary(**r) for r in rows]


@router.post(
    "/drafts",
    response_model=DraftSummary,
    dependencies=[Depends(require_user)],
)
def create_draft_endpoint(body: DraftCreate) -> DraftSummary:
    with db.connect() as conn:
        try:
            new_id = spec_store.create_draft(
                conn,
                parent_revision=body.parent_revision,
                label=body.label,
            )
        except spec_store.DraftNotFoundError as exc:
            raise HTTPException(
                404,
                f"Parent revision {body.parent_revision} not found.",
            ) from exc
        rows = spec_store.list_drafts(conn)
    row = next(r for r in rows if r["revision"] == new_id)
    return DraftSummary(**row)


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int) -> dict[str, Any]:
    with db.connect() as conn:
        try:
            spec = spec_store.get_revision(conn, draft_id)
        except spec_store.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
    return spec_store.spec_to_dict(spec)


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_user)])
def discard_draft_endpoint(draft_id: int) -> dict[str, str]:
    with db.connect() as conn:
        try:
            spec_store.discard_draft(conn, draft_id)
        except spec_store.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
        except spec_store.DraftAlreadyPublishedError as exc:
            raise HTTPException(
                409,
                f"Draft {draft_id} is already published and cannot be discarded.",
            ) from exc
    return {"discarded": str(draft_id)}


# ─── Draft mutations ────────────────────────────────────────────────────────


@router.post(
    "/drafts/{draft_id}/types",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
def add_type(draft_id: int, body: TypeDefinitionCreate) -> MutationResponse:
    with db.connect() as conn:
        with spec_store.edit_draft(conn, draft_id) as spec:
            if any(t.name.lower() == body.name.lower() for t in spec.types):
                raise HTTPException(
                    409,
                    f"TypeDefinition collides (case-insensitive) for name {body.name!r}.",
                )
            spec.types.append(
                TypeDefinition(
                    name=body.name,
                    base=body.base,
                    pattern=body.pattern,
                    description=body.description,
                )
            )
        return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/slots",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
def add_slot(draft_id: int, body: SlotCreate) -> MutationResponse:
    with db.connect() as conn:
        with spec_store.edit_draft(conn, draft_id) as spec:
            if any(s.name.lower() == body.name.lower() for s in spec.slots):
                raise HTTPException(
                    409,
                    f"Slot collides (case-insensitive) for name {body.name!r}.",
                )

            range_obj: Any | None = None
            if body.range_kind == "type":
                if body.range_name is None:
                    raise HTTPException(400, "range_kind='type' requires range_name")
                range_obj = _find_type(spec, body.range_name)
            elif body.range_kind == "class":
                if body.range_name is None:
                    raise HTTPException(400, "range_kind='class' requires range_name")
                range_obj = _find_class(spec, body.range_name)
            elif body.range_kind is not None:
                raise HTTPException(
                    400, f"range_kind must be 'type', 'class', or null; got {body.range_kind!r}"
                )

            permissible = None
            if body.permissible_values is not None:
                permissible = [PermissibleValue(text=t) for t in body.permissible_values]

            derivation = None
            if body.derivation is not None:
                placeholder_primary = OntologyClass(name="__derivation_ctx__")
                derivation = translate_expr(body.derivation, spec, placeholder_primary)

            spec.slots.append(
                Slot(
                    name=body.name,
                    range=range_obj,
                    identifier=body.identifier,
                    required=body.required,
                    multivalued=body.multivalued,
                    resolution_policy=body.resolution_policy,
                    pattern=body.pattern,
                    minimum_value=body.minimum_value,
                    maximum_value=body.maximum_value,
                    permissible_values=permissible,
                    description=body.description,
                    derivation=derivation,
                )
            )
        return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/classes",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
def add_class(draft_id: int, body: ClassCreate) -> MutationResponse:
    with db.connect() as conn:
        with spec_store.edit_draft(conn, draft_id) as spec:
            if any(c.name.lower() == body.name.lower() for c in spec.classes):
                raise HTTPException(
                    409,
                    f"OntologyClass collides (case-insensitive) with an existing "
                    f"class for name {body.name!r}.",
                )

            slots = [_find_slot(spec, n) for n in body.slot_names]
            is_a = _find_class(spec, body.is_a_name) if body.is_a_name else None
            mixins = [_find_class(spec, n) for n in body.mixin_names]

            definition = None
            if body.definition is not None:
                primary = (
                    is_a
                    if is_a is not None
                    else _find_class(spec, body.name)
                    if any(c.name == body.name for c in spec.classes)
                    else OntologyClass(name=body.name)
                )
                definition = translate_expr(body.definition, spec, primary)

            spec.classes.append(
                OntologyClass(
                    name=body.name,
                    slots=slots,
                    is_a=is_a,
                    mixins=mixins,
                    abstract=body.abstract,
                    description=body.description,
                    definition=definition,
                )
            )
        return _response(draft_id, spec)


@router.patch(
    "/drafts/{draft_id}/classes/{name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
def update_class(draft_id: int, name: str, body: ClassUpdate) -> MutationResponse:
    with db.connect() as conn:
        with spec_store.edit_draft(conn, draft_id) as spec:
            cls = _find_class(spec, name)

            if body.slot_names is not None:
                cls.slots = [_find_slot(spec, n) for n in body.slot_names]
            if body.is_a_name is not None:
                cls.is_a = _find_class(spec, body.is_a_name) if body.is_a_name else None
            if body.mixin_names is not None:
                cls.mixins = [_find_class(spec, n) for n in body.mixin_names]
            if body.abstract is not None:
                cls.abstract = body.abstract
            if body.description is not None:
                cls.description = body.description

        return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/sources",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
def add_source(draft_id: int, body: SourceCreate) -> MutationResponse:
    with db.connect() as conn:
        with spec_store.edit_draft(conn, draft_id) as spec:
            if any(s.name.lower() == body.name.lower() for s in spec.sources):
                raise HTTPException(
                    409,
                    f"Source collides (case-insensitive) for name {body.name!r}.",
                )

            cls = _find_class(spec, body.entity_class_name)
            slot = next((s for s in cls.slots if s.name == body.identifier_slot_name), None)
            if slot is None:
                raise HTTPException(
                    400,
                    f"Slot {body.identifier_slot_name!r} is not on class {cls.name!r}",
                )

            spec.sources.append(
                Source(
                    name=body.name,
                    entity_class=cls,
                    identifier_slot=slot,
                    description=body.description,
                )
            )
        return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/constraints",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
def add_constraint(draft_id: int, body: ConstraintCreate) -> MutationResponse:
    with db.connect() as conn:
        with spec_store.edit_draft(conn, draft_id) as spec:
            if any(c.name.lower() == body.name.lower() for c in spec.constraints):
                raise HTTPException(
                    409,
                    f"Constraint collides (case-insensitive) for name {body.name!r}.",
                )

            primary = _find_class(spec, body.primary_class_name)
            expr = translate_expr(body.body, spec, primary)

            spec.constraints.append(
                Constraint(
                    name=body.name,
                    primary=primary,
                    body=expr,
                    severity=body.severity,
                    message=body.message,
                )
            )
        return _response(draft_id, spec)


# ─── Publish ────────────────────────────────────────────────────────────────


@router.post(
    "/drafts/{draft_id}/publish",
    response_model=PublishResponse,
    dependencies=[Depends(require_user)],
)
def publish(
    draft_id: int,
    allow_destructive: bool = Query(
        False,
        description=(
            "Required to confirm destructive migrations (DropClass / DropSlot / "
            "ChangeSlotType). Publish fails with 400 otherwise."
        ),
    ),
) -> PublishResponse:
    """Run the publish gate; on pass, atomically promote this draft to published."""
    with db.connect() as conn:
        try:
            spec_store.publish_draft(
                conn,
                draft_id,
                allow_destructive=allow_destructive,
            )
        except spec_store.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
        except spec_store.PublishGateError as exc:
            raise HTTPException(400, f"Draft {draft_id} failed the publish gate: {exc}") from exc

        spec_store.get_revision(conn, draft_id)
        rows = spec_store.list_published(conn)
    row = next(r for r in rows if r["revision"] == draft_id)
    return PublishResponse(
        revision=draft_id,
        content_hash=row["content_hash"],
        published_at=row["published_at"] or "",
    )


# ─── Rollback ───────────────────────────────────────────────────────────────


@router.post(
    "/rollback/{target_revision}",
    response_model=PublishResponse,
    dependencies=[Depends(require_user)],
)
def rollback(
    target_revision: int,
    allow_destructive: bool = Query(
        False,
        description=(
            "Required to confirm destructive migrations. Rollbacks typically "
            "drop classes/slots that the newer spec added; pass "
            "allow_destructive=true to confirm those rows are forfeit."
        ),
    ),
) -> PublishResponse:
    """Promote a prior revision back to published.

    Mechanically identical to publish: the diff (current_published → target)
    is applied to the data plane, the constraint gate runs against current
    data, and the published flag flips atomically. Rejects an attempt to
    rollback to the currently-published revision (no-op).
    """
    with db.connect() as conn:
        current = spec_store.get_published_revision(conn)
        if current == target_revision:
            raise HTTPException(
                400,
                f"Revision {target_revision} is already the published spec; "
                "nothing to roll back to.",
            )
        try:
            spec_store.publish_draft(
                conn,
                target_revision,
                allow_destructive=allow_destructive,
            )
        except spec_store.DraftNotFoundError as exc:
            raise HTTPException(
                404,
                f"Revision {target_revision} not found.",
            ) from exc
        except spec_store.PublishGateError as exc:
            raise HTTPException(
                400,
                f"Rollback to revision {target_revision} failed the publish gate: {exc}",
            ) from exc

        rows = spec_store.list_published(conn)
    row = next(r for r in rows if r["revision"] == target_revision)
    return PublishResponse(
        revision=target_revision,
        content_hash=row["content_hash"],
        published_at=row["published_at"] or "",
    )
