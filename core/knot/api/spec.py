"""Spec router — ontology graph authoring + publish gate.

Endpoint groups under `/spec`:
  - /spec/published/*   read the currently active spec graph
  - /spec/revisions/*   browse published-revision history (immortal audit chain)
  - /spec/drafts/*      author the next spec via mutation + publish gate

Drafts are mutable; published revisions are immortal.  Every spec mutation
must go through a draft.  The publish gate runs spec-graph validation
(steps 1+2 of the four-step gate); bindings-side checks (steps 3+4)
land with the modeling router.

TypeExpression is described in the API via (type_kind, type_name):
  type_kind = "primitive"           type_name = "string"|"integer"|...
  type_kind = "class"               type_name = <OntologyClass name>
  type_kind = "array_of_primitive"  type_name = "string"|"integer"|...
  type_kind = "array_of_class"      type_name = <OntologyClass name>
  type_kind = null                  (derived slot — no type)
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
    spec_to_dict,
)
from knot.spec.expressions import ExprJson
from knot.spec.metaschema import Array, ClassRef, Primitive, Severity, SlotConstraints

# ---------------------------------------------------------------------------
# Request / response shapes
# ---------------------------------------------------------------------------


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ─── Read summaries ─────────────────────────────────────────────────────────


class SlotSummary(_StrictBase):
    name: str
    type_kind: str | None  # "primitive"|"class"|"array_of_primitive"|"array_of_class"|None
    type_name: str | None  # primitive name or class name
    identifier: bool
    required: bool
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
    trust_score: float
    slot_priors: dict[str, list[float]]  # slot_name -> [alpha, beta]
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


class SlotConstraintsCreate(_StrictBase):
    pattern: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    permissible_values: list[str] | None = None


class SlotCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    type_kind: str | None = None  # "primitive"|"class"|"array_of_primitive"|"array_of_class"|null
    type_name: str | None = None
    identifier: bool = False
    required: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    constraints: SlotConstraintsCreate | None = None
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
    trust_score: float = 1.0
    slot_priors: dict[str, list[float]] = Field(default_factory=dict)
    description: str | None = None


class TrustScoreUpdate(_StrictBase):
    trust_score: float


class SlotPriorUpdate(_StrictBase):
    alpha: float
    beta: float


class ConstraintCreate(_StrictBase):
    name: str = Field(pattern=_NAME_PATTERN)
    primary_class_name: str
    body: ExprJson
    severity: Severity = Severity.ERROR
    message: str | None = None


class DraftCreate(_StrictBase):
    parent_revision: int | None = None
    label: str | None = None


class SlotRename(_StrictBase):
    new_name: str = Field(pattern=_NAME_PATTERN)


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
    spec_summary: dict[str, int]  # {classes: n, slots: n, sources: n}


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


def _type_kind_name(slot: Slot) -> tuple[str | None, str | None]:
    """Return (type_kind, type_name) for the API summary."""
    t = slot.type
    if t is None:
        return None, None
    if isinstance(t, Primitive):
        return "primitive", t.name
    if isinstance(t, ClassRef):
        return "class", t.target_class.name
    if isinstance(t, Array):
        inner = t.of
        if isinstance(inner, Primitive):
            return "array_of_primitive", inner.name
        if isinstance(inner, ClassRef):
            return "array_of_class", inner.target_class.name
    return None, None


def _summarize_slot(s: Slot) -> SlotSummary:
    type_kind, type_name = _type_kind_name(s)
    rp = s.resolution_policy
    return SlotSummary(
        name=s.name,
        type_kind=type_kind,
        type_name=type_name,
        identifier=s.identifier,
        required=s.required,
        resolution_policy=rp.value if hasattr(rp, "value") else str(rp),
    )


def _summarize_source(s: Source) -> SourceSummary:
    return SourceSummary(
        name=s.name,
        entity_class=s.entity_class.name,
        identifier_slot=s.identifier_slot.name,
        trust_score=s.trust_score,
        slot_priors={k: list(v) for k, v in s.slot_priors.items()},
        description=s.description,
    )


def _parse_slot_priors(raw: dict[str, list[float]]) -> dict[str, tuple[float, float]]:
    """Convert API list[float] representation to (alpha, beta) tuples."""
    out: dict[str, tuple[float, float]] = {}
    for slot_name, ab in raw.items():
        if len(ab) != 2:
            raise ValueError(f"slot_prior for {slot_name!r} must be [alpha, beta]; got {ab!r}")
        out[slot_name] = (ab[0], ab[1])
    return out


def _build_slot_constraints(body: SlotConstraintsCreate | None) -> SlotConstraints | None:
    if body is None:
        return None
    if all(
        v is None
        for v in [body.pattern, body.min_value, body.max_value, body.permissible_values]
    ):
        return None
    return SlotConstraints(
        pattern=body.pattern,
        min_value=body.min_value,
        max_value=body.max_value,
        permissible_values=body.permissible_values,
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


def _map_referenced(exc: graph_spec.ReferencedEntityError) -> HTTPException:
    return HTTPException(409, str(exc))


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/spec", tags=["spec"])


# Mount the GraphQL endpoint sibling-module on this router.
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
                type_kind=body.type_kind,
                type_name=body.type_name,
                identifier=body.identifier,
                required=body.required,
                resolution_policy=body.resolution_policy,
                constraints=_build_slot_constraints(body.constraints),
                description=body.description,
                derivation=body.derivation,
            )
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.InvalidTypeExprError as exc:
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
    try:
        slot_priors = _parse_slot_priors(body.slot_priors)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    async with db.connect() as conn:
        try:
            spec = await graph_spec.add_source(
                conn,
                draft_id,
                name=body.name,
                entity_class_name=body.entity_class_name,
                identifier_slot_name=body.identifier_slot_name,
                trust_score=body.trust_score,
                slot_priors=slot_priors,
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
    "/drafts/{draft_id}/sources/{source_name}/trust_score",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def update_source_trust_score(
    draft_id: int, source_name: str, body: TrustScoreUpdate
) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.update_source_trust_score(
                conn, draft_id, source_name, trust_score=body.trust_score
            )
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/sources/{source_name}/slot_priors/{slot_name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def update_source_slot_prior(
    draft_id: int, source_name: str, slot_name: str, body: SlotPriorUpdate
) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.update_source_slot_prior(
                conn, draft_id, source_name, slot_name, alpha=body.alpha, beta=body.beta
            )
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.delete(
    "/drafts/{draft_id}/sources/{source_name}/slot_priors/{slot_name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def reset_source_slot_prior(
    draft_id: int, source_name: str, slot_name: str
) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.reset_source_slot_prior(
                conn, draft_id, source_name, slot_name
            )
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
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


# ─── Draft removals ─────────────────────────────────────────────────────────


@router.delete(
    "/drafts/{draft_id}/slots/{name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def remove_slot(draft_id: int, name: str) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.remove_slot(conn, draft_id, name)
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.ReferencedEntityError as exc:
            raise _map_referenced(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.post(
    "/drafts/{draft_id}/slots/{slot_name}/rename",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
    summary="Rename a slot (non-destructive RENAME COLUMN at publish)",
)
async def rename_slot(draft_id: int, slot_name: str, body: SlotRename) -> MutationResponse:
    """Rename a slot on a draft.

    Records a rename hint so that ``publish`` emits ``ALTER TABLE … RENAME COLUMN``
    instead of the destructive ``DROP + ADD`` pair. The spec is updated in-place
    on the draft; any class or source that references the old name is updated too.
    """
    async with db.connect() as conn:
        try:
            spec = await graph_spec.rename_slot(conn, draft_id, slot_name, body.new_name)
        except graph_spec.CollisionError as exc:
            raise _map_collision(exc) from exc
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.delete(
    "/drafts/{draft_id}/classes/{name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def remove_class(draft_id: int, name: str) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.remove_class(conn, draft_id, name)
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.ReferencedEntityError as exc:
            raise _map_referenced(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.delete(
    "/drafts/{draft_id}/sources/{name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def remove_source(draft_id: int, name: str) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.remove_source(conn, draft_id, name)
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


@router.delete(
    "/drafts/{draft_id}/constraints/{name}",
    response_model=MutationResponse,
    dependencies=[Depends(require_user)],
)
async def remove_constraint(draft_id: int, name: str) -> MutationResponse:
    async with db.connect() as conn:
        try:
            spec = await graph_spec.remove_constraint(conn, draft_id, name)
        except graph_spec.EntityNotOnDraftError as exc:
            raise _map_entity_not_on_draft(exc) from exc
        except graph_spec.DraftAlreadyPublishedError as exc:
            raise _map_already_published(exc) from exc
    return _response(draft_id, spec)


# ─── Preview ────────────────────────────────────────────────────────────────


@router.post(
    "/drafts/{draft_id}/preview",
    response_model=graph_spec.PreviewResult,
    summary="Preview what publish would do — gates + diff, no DDL emitted",
)
async def preview_draft(draft_id: int) -> graph_spec.PreviewResult:
    """Evaluate all publish gates for a draft without applying any DDL.

    Returns the full diff, bucket classification, and any blockers that
    would prevent publish right now.  ``publishable=True`` means
    ``POST /publish`` would succeed (without allow_destructive if
    ``requires_allow_destructive=False``).
    """
    async with db.connect() as conn:
        try:
            return await graph_spec.preview_publish(conn, draft_id)
        except graph_spec.DraftNotFoundError as exc:
            raise HTTPException(404, f"Draft {draft_id} not found.") from exc
        except graph_spec.PublishGateError as exc:
            raise HTTPException(400, f"Draft {draft_id} failed the publish gate: {exc}") from exc


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
            "ChangeSlotTypeExpression). Publish fails with 400 otherwise."
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
    """Promote a prior revision back to published."""
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
