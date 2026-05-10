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
from knot.graph import spec as graph_spec
from knot.spec import (
    OntologyClass,
    ResolutionPolicy,
    Slot,
    Source,
    Spec,
    TypeDefinition,
    spec_to_dict,
)
from knot.spec.expressions import ExprJson
from knot.spec.metaschema import Severity

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
# HTTP-shape helpers
# ---------------------------------------------------------------------------


def _spec_summary(spec: Spec) -> dict[str, int]:
    return {
        "classes": len(spec.classes),
        "slots": len(spec.slots),
        "types": len(spec.types),
        "sources": len(spec.sources),
        "constraints": len(spec.constraints),
    }


def _response(draft_id: int, spec: Spec) -> MutationResponse:
    """Build the mutation response after the orchestration call has written back."""
    return MutationResponse(
        draft_revision=draft_id,
        content_hash=graph_spec.content_hash(spec),
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


# ─── Exception → HTTP mapping helpers ───────────────────────────────────────


def _map_collision(exc: graph_spec.CollisionError) -> HTTPException:
    return HTTPException(409, str(exc))


def _map_entity_not_on_draft(exc: graph_spec.EntityNotOnDraftError) -> HTTPException:
    return HTTPException(404, str(exc))


def _map_invalid(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


def _map_already_published(exc: graph_spec.DraftAlreadyPublishedError) -> HTTPException:
    return HTTPException(409, str(exc))


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/spec", tags=["spec"])


# Mount the GraphQL endpoint sibling-module on this router.  Same pattern as
# ``knot.api.graph`` mounting ``graph/graphql.py`` — the spec metaschema is
# static, so a side-by-side GraphQL surface composes the same way.
from knot.api import spec_graphql as _spec_graphql_mod  # noqa: E402

router.include_router(_spec_graphql_mod.router)


# ─── Published reads ────────────────────────────────────────────────────────


@router.get("/published", summary="Full currently-published spec (cycle-safe JSON)")
async def get_published_spec() -> dict[str, Any]:
    async with db.connect() as conn:
        spec = await graph_spec.get_published(conn)
    if spec is None:
        raise HTTPException(404, "No spec is currently published.")
    return spec_to_dict(spec)


@router.get("/published/classes", response_model=list[ClassSummary])
async def list_published_classes() -> list[ClassSummary]:
    async with db.connect() as conn:
        spec = await graph_spec.get_published(conn)
    if spec is None:
        return []
    return [_summarize_class(c) for c in spec.classes]


@router.get("/published/classes/{name}", response_model=ClassSummary)
async def get_published_class(name: str) -> ClassSummary:
    async with db.connect() as conn:
        spec = await graph_spec.get_published(conn)
    if spec is None:
        raise HTTPException(404, "No spec is currently published.")
    try:
        cls = next(c for c in spec.classes if c.name == name)
    except StopIteration as exc:
        raise HTTPException(404, f"OntologyClass {name!r} not on this draft") from exc
    return _summarize_class(cls)


@router.get("/published/slots", response_model=list[SlotSummary])
async def list_published_slots() -> list[SlotSummary]:
    async with db.connect() as conn:
        spec = await graph_spec.get_published(conn)
    if spec is None:
        return []
    return [_summarize_slot(s) for s in spec.slots]


@router.get("/published/types", response_model=list[TypeSummary])
async def list_published_types() -> list[TypeSummary]:
    async with db.connect() as conn:
        spec = await graph_spec.get_published(conn)
    if spec is None:
        return []
    return [_summarize_type(t) for t in spec.types]


@router.get("/published/sources", response_model=list[SourceSummary])
async def list_published_sources() -> list[SourceSummary]:
    async with db.connect() as conn:
        spec = await graph_spec.get_published(conn)
    if spec is None:
        return []
    return [_summarize_source(s) for s in spec.sources]


# ─── Revision history ───────────────────────────────────────────────────────


@router.get("/revisions", response_model=list[RevisionSummary])
async def list_revisions() -> list[RevisionSummary]:
    """All published revisions, newest first (immortal audit chain)."""
    async with db.connect() as conn:
        rows = await graph_spec.list_revisions(conn)
    return [RevisionSummary(**r) for r in rows]


@router.get("/revisions/{revision}")
async def get_revision_spec(revision: int) -> dict[str, Any]:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.get_revision(conn, revision)
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Revision {revision} not found.") from exc
    return spec_to_dict(spec)


# ─── Drafts ─────────────────────────────────────────────────────────────────


@router.get("/drafts", response_model=list[DraftSummary])
async def list_drafts_endpoint() -> list[DraftSummary]:
    async with db.connect() as conn:
        rows = await graph_spec.list_drafts(conn)
    return [DraftSummary(**r) for r in rows]


@router.post(
    "/drafts",
    response_model=DraftSummary,
    dependencies=[Depends(require_user)],
)
async def create_draft_endpoint(body: DraftCreate) -> DraftSummary:
    async with db.connect() as conn:
        try:
            new_id = await graph_spec.create_draft(
                conn, parent_revision=body.parent_revision, label=body.label
            )
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Parent revision {body.parent_revision} not found.") from exc
        rows = await graph_spec.list_drafts(conn)
    row = next(r for r in rows if r["revision"] == new_id)
    return DraftSummary(**row)


@router.get("/drafts/{draft_id}")
async def get_draft(draft_id: int) -> dict[str, Any]:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.get_draft(conn, draft_id)
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
    return spec_to_dict(spec)


@router.delete("/drafts/{draft_id}", dependencies=[Depends(require_user)])
async def discard_draft_endpoint(draft_id: int) -> dict[str, str]:
    async with db.connect() as conn:
        try:
            await graph_spec.discard_draft(conn, draft_id)
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
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
async def add_type(draft_id: int, body: TypeDefinitionCreate) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.add_type(
                conn,
                draft_id,
                name=body.name,
                base=body.base,
                pattern=body.pattern,
                description=body.description,
            )
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/slots",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def add_slot(draft_id: int, body: SlotCreate) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.add_slot(
                conn,
                draft_id,
                name=body.name,
                range_kind=body.range_kind,
                range_name=body.range_name,
                identifier=body.identifier,
                required=body.required,
                multivalued=body.multivalued,
                resolution_policy=body.resolution_policy,
                pattern=body.pattern,
                minimum_value=body.minimum_value,
                maximum_value=body.maximum_value,
                permissible_values=body.permissible_values,
                description=body.description,
                derivation=body.derivation,
            )
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.InvalidRangeKindError as exc:
            raise _map_invalid(exc) from exc
        except graph_spec.ExprTranslationError as exc:
            raise HTTPException(404, str(exc)) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/classes",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def add_class(draft_id: int, body: ClassCreate) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.add_class(
                conn,
                draft_id,
                name=body.name,
                slot_names=body.slot_names,
                is_a_name=body.is_a_name,
                mixin_names=body.mixin_names,
                abstract=body.abstract,
                description=body.description,
                definition=body.definition,
            )
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.ExprTranslationError as exc:
            raise HTTPException(404, str(exc)) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.patch(
    "/drafts/{draft_id}/classes/{name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def update_class(draft_id: int, name: str, body: ClassUpdate) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.update_class(
                conn,
                draft_id,
                name,
                slot_names=body.slot_names,
                is_a_name=body.is_a_name,
                mixin_names=body.mixin_names,
                abstract=body.abstract,
                description=body.description,
            )
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/sources",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def add_source(draft_id: int, body: SourceCreate) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.add_source(
                conn,
                draft_id,
                name=body.name,
                entity_class_name=body.entity_class_name,
                identifier_slot_name=body.identifier_slot_name,
                description=body.description,
            )
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.InvalidIdentifierSlotError as exc:
            raise _map_invalid(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/constraints",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def add_constraint(draft_id: int, body: ConstraintCreate) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.add_constraint(
                conn,
                draft_id,
                name=body.name,
                primary_class_name=body.primary_class_name,
                body=body.body,
                severity=body.severity,
                message=body.message,
            )
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.ExprTranslationError as exc:
            raise HTTPException(404, str(exc)) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


# ─── Publish ────────────────────────────────────────────────────────────────


@router.post(
    "/drafts/{draft_id}/publish",
    response_model=PublishResponse,
    dependencies=[Depends(require_user)],
)
async def publish(
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
    async with db.connect() as conn:
        try:
            result = await graph_spec.publish_draft(
                conn, draft_id, allow_destructive=allow_destructive
            )
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
        except graph_spec.PublishGateError as exc:
            raise HTTPException(400, f"Draft {draft_id} failed the publish gate: {exc}") from exc
    return PublishResponse(**result)


# ─── Rollback ───────────────────────────────────────────────────────────────


@router.post(
    "/rollback/{target_revision}",
    response_model=PublishResponse,
    dependencies=[Depends(require_user)],
)
async def rollback(
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
    async with db.connect() as conn:
        try:
            result = await graph_spec.rollback(
                conn, target_revision, allow_destructive=allow_destructive
            )
        except graph_spec.RollbackToCurrentError as exc:
            raise HTTPException(400, str(exc)) from exc
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Revision {target_revision} not found.") from exc
        except graph_spec.PublishGateError as exc:
            raise HTTPException(
                400,
                f"Rollback to revision {target_revision} failed the publish gate: {exc}",
            ) from exc
    return PublishResponse(**result)
