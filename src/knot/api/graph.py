"""Graph router — data plane.

Distinct from the spec router (which authors meaning): this router pushes,
reads, and corrects facts in the graph. No draft lifecycle — every mutation
validates against the *currently published* spec and lands directly.

Endpoint groups under ``/graph``:
  - /graph/ingest/{source_name}                          push source rows
  - /graph/classes/{class_name}                          list rows for a class
  - /graph/classes/{class_name}/{canonical_id}           contributions for one entity
  - /graph/classes/{class_name}/{canonical_id}/resolved  trust-resolved single record
  - /graph/trust                                         list per-source trust scores
  - /graph/trust/{source_name}                           get/set per-source trust score
  - /graph/trust/posteriors[/{source}/{slot}]            bandit Beta posteriors
  - /graph/trust/feedback                                raw Bernoulli observation
  - /graph/corrections                                   user corrections (Property today)
  - /graph/query                                         ontology-shaped queries (later)

Storage shape (locked, see ``knot.db.migration``): per-class postgres
tables; columns mirror stored slots; primary key
``(_source, _source_row_id)``; each row carries ``_canonical_id``
(initially the source identifier-slot value, refined by ER), ``_ingest_at``,
and ``_spec_revision`` (FK into spec_revisions for audit walk-back).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knot import db
from knot.security import Principal, require_user
from knot.db import dq, graph_store, spec_store, trust_config, trust_posteriors
from knot.middleware import get_request_id
from knot.graph import corrections as graph_corrections
from knot.graph import resolve
from knot.ontology import OntologyClass, Slot, Spec, TypeDefinition
from knot.api.row_models import build_row_model, build_row_model_for_class, build_value_model_for_slot


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestBatch(_StrictBase):
    rows: list[dict[str, Any]] = Field(..., max_length=10_000)


class IngestResponse(_StrictBase):
    accepted: int
    source: str
    entity_class: str
    spec_revision: int


class ListResponse(_StrictBase):
    entity_class: str
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    as_of: int | None = None


class EntityResponse(_StrictBase):
    entity_class: str
    canonical_id: str
    contributions: list[dict[str, Any]]
    as_of: int | None = None


class ResolvedEntityResponse(_StrictBase):
    entity_class: str
    canonical_id: str
    resolved: dict[str, Any]
    as_of: int | None = None


class TrustScore(_StrictBase):
    source: str
    trust_score: float


class TrustUpdate(_StrictBase):
    trust_score: float = Field(..., ge=0.0, le=1.0)


class PosteriorView(_StrictBase):
    source: str
    slot: str
    alpha: float
    beta: float
    mean: float
    observations: float


class FeedbackBody(_StrictBase):
    source: str
    slot: str
    success: bool


# ─── Corrections (typed discriminated union; expand as more types land) ─────


class PropertyCorrection(_StrictBase):
    # ``applied_by`` is derived from the auth principal — never self-reported.
    type: Literal["property"] = "property"
    class_name: str
    canonical_id: str
    slot: str
    value: Any


class Merge(_StrictBase):
    type: Literal["merge"] = "merge"
    class_name: str
    keep_canonical_id: str
    merge_canonical_ids: list[str] = Field(..., max_length=1_000)


class Split(_StrictBase):
    type: Literal["split"] = "split"
    class_name: str
    source_canonical_id: str
    # partitions: new_canonical_id -> [(source, source_row_id), ...]
    partitions: dict[str, list[tuple[str, str]]]


class Add(_StrictBase):
    type: Literal["add"] = "add"
    class_name: str
    new_canonical_id: str
    values: dict[str, Any] = Field(default_factory=dict)


class Tombstone(_StrictBase):
    type: Literal["tombstone"] = "tombstone"
    class_name: str
    canonical_id: str
    reason: str | None = None


class RejectContribution(_StrictBase):
    type: Literal["reject_contribution"] = "reject_contribution"
    class_name: str
    canonical_id: str
    source: str


Correction = Annotated[
    Union[PropertyCorrection, Merge, Split, Add, Tombstone, RejectContribution],
    Field(discriminator="type"),
]


class CorrectionResponse(_StrictBase):
    id: int
    correction_type: str
    applied_revision: int


router = APIRouter(prefix="/graph", tags=["graph"])


def _resolve_class(spec: Spec, class_name: str) -> OntologyClass:
    cls = next((c for c in spec.classes if c.name == class_name), None)
    if cls is None:
        raise HTTPException(404, f"Class {class_name!r} not on the published spec.")
    if cls.abstract:
        raise HTTPException(
            400, f"Class {class_name!r} is abstract; no rows are stored."
        )
    return cls


def _published_or_404(conn) -> Spec:
    spec = spec_store.get_published(conn)
    if spec is None:
        raise HTTPException(409, "No spec is published yet.")
    return spec


@router.post(
    "/ingest/{source_name}",
    response_model=IngestResponse,
    dependencies=[Depends(require_user)],
)
def ingest(
    source_name: str,
    body: IngestBatch,
    validate_constraints: bool = Query(
        False,
        description=(
            "When true, run all published ERROR-severity constraints whose "
            "primary_class matches the source's class after INSERTs.  If any "
            "violation is found the entire batch is rolled back and a 422 is "
            "returned with the violation list."
        ),
    ),
) -> IngestResponse:
    """Push a batch of rows attributed to a known source.

    Validation:
      - 409 if no spec is published yet.
      - 404 if ``source_name`` isn't a Source on the published spec.
      - 422 with FastAPI-shaped error detail if any row fails Pydantic
        validation against the source's class slot shape (extra="forbid",
        identifier required, types coerced from slot.range, pattern/min/max
        enforced, permissible_values constrained, multivalued list-shape).

    When ``validate_constraints=true``, post-INSERT constraint check:
      - Compiles and runs each published ERROR-severity ``Constraint`` whose
        ``primary_class`` matches the source's entity class.
      - If any constraint returns offending rows, the transaction is rolled
        back and a 422 is returned with the violation list.
      - WARNING-severity constraints are skipped; they never block ingest.

    Successful rows are upserted into the per-class table; system columns
    are set from the source + currently-published revision.
    """
    from knot.db.sql_compiler import compile_constraint
    from knot.ontology.metaschema import Severity

    with db.connect() as conn:
        spec = _published_or_404(conn)
        source = next((s for s in spec.sources if s.name == source_name), None)
        if source is None:
            raise HTTPException(
                404, f"Source {source_name!r} not on the published spec."
            )

        revision = spec_store.get_published_revision(conn)
        RowModel = build_row_model(source)
        validated: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for i, row in enumerate(body.rows):
            try:
                m = RowModel.model_validate(row)
            except ValidationError as exc:
                for err in exc.errors():
                    errors.append({**err, "loc": ("body", "rows", i, *err["loc"])})
                continue
            validated.append(m.model_dump(exclude_none=False))

        if errors:
            raise HTTPException(422, detail=errors)

        if validate_constraints:
            # Run inside an explicit transaction so violations cause a rollback.
            cls = source.entity_class
            relevant = [
                c for c in spec.constraints
                if c.primary.name == cls.name
                and getattr(c, "severity", Severity.ERROR) == Severity.ERROR
            ]
            try:
                with conn.transaction():
                    count = graph_store.insert_rows(
                        conn,
                        source=source,
                        spec_revision=revision,
                        rows=validated,
                    )
                    violations: list[dict[str, Any]] = []
                    for constraint in relevant:
                        stmt, params = compile_constraint(constraint, cls)
                        try:
                            rows = conn.execute(stmt, params).fetchall()
                        except Exception:
                            continue
                        for row in rows:
                            violations.append({
                                "rule_id": row[0],
                                "class_name": row[1],
                                "slot_name": row[2],
                                "offending_pk": str(row[3]),
                                "detail": row[4] or "",
                            })
                    if violations:
                        raise _ConstraintViolationError(violations)
                    # DQ inside the transaction so it rolls back if a later
                    # constraint check fails.
                    dq.record_incremental(
                        conn,
                        source_name=source.name,
                        cls=cls,
                        batch_id=get_request_id(),
                        rows=validated,
                    )
            except _ConstraintViolationError as exc:
                raise HTTPException(422, detail={"violations": exc.violations})
        else:
            count = graph_store.insert_rows(
                conn,
                source=source,
                spec_revision=revision,
                rows=validated,
            )
            dq.record_incremental(
                conn,
                source_name=source.name,
                cls=source.entity_class,
                batch_id=get_request_id(),
                rows=validated,
            )

    return IngestResponse(
        accepted=count,
        source=source_name,
        entity_class=source.entity_class.name,
        spec_revision=revision,
    )


class _ConstraintViolationError(Exception):
    """Internal sentinel raised inside a transaction to trigger rollback."""

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(f"{len(violations)} constraint violation(s)")


@router.get("/classes/{class_name}", response_model=ListResponse)
def list_class_rows(
    class_name: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    as_of: int | None = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
    include_tombstoned: bool = Query(False, description="Include tombstoned entities"),
) -> ListResponse:
    """List rows for a published class. Disagreement-aware: one row per
    ``(_canonical_id, _source)`` — same canonical_id may appear N times when
    N sources contribute.
    """
    with db.connect() as conn:
        spec = _published_or_404(conn)
        cls = _resolve_class(spec, class_name)
        rows = graph_store.list_rows(
            conn, cls=cls, limit=limit, offset=offset, as_of=as_of,
            include_tombstoned=include_tombstoned,
        )
        total = graph_store.count_rows(
            conn, cls=cls, as_of=as_of, include_tombstoned=include_tombstoned,
        )
    return ListResponse(
        entity_class=cls.name,
        rows=rows,
        total=total,
        limit=limit,
        offset=offset,
        as_of=as_of,
    )


@router.get(
    "/classes/{class_name}/{canonical_id}",
    response_model=EntityResponse,
)
def get_canonical_entity(
    class_name: str,
    canonical_id: str,
    as_of: int | None = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
    include_tombstoned: bool = Query(False, description="Include tombstoned entities"),
) -> EntityResponse:
    """All per-source contributions for a single canonical entity.

    Returns 404 if the canonical_id has no contributions under the (optionally
    pinned) revision.
    """
    with db.connect() as conn:
        spec = _published_or_404(conn)
        cls = _resolve_class(spec, class_name)
        contributions = graph_store.get_canonical_contributions(
            conn, cls=cls, canonical_id=canonical_id, as_of=as_of,
            include_tombstoned=include_tombstoned,
        )
    if not contributions:
        raise HTTPException(
            404,
            f"No contributions for {class_name}/{canonical_id}"
            + (f" as_of={as_of}" if as_of is not None else ""),
        )
    return EntityResponse(
        entity_class=cls.name,
        canonical_id=canonical_id,
        contributions=contributions,
        as_of=as_of,
    )


@router.get(
    "/classes/{class_name}/{canonical_id}/resolved",
    response_model=ResolvedEntityResponse,
)
def get_resolved_entity(
    class_name: str,
    canonical_id: str,
    as_of: int | None = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
) -> ResolvedEntityResponse:
    """One trust-resolved record for the canonical_id (per-slot resolution).

    Each slot's value comes from the source that wins for that slot under
    its declared ``resolution_policy``; multivalued slots return the union
    across all sources.
    """
    with db.connect() as conn:
        spec = _published_or_404(conn)
        cls = _resolve_class(spec, class_name)
        record = resolve.resolve_entity(
            conn, cls=cls, canonical_id=canonical_id, as_of=as_of,
        )
    if record is None:
        raise HTTPException(
            404, f"No contributions for {class_name}/{canonical_id}"
        )
    return ResolvedEntityResponse(
        entity_class=cls.name,
        canonical_id=canonical_id,
        resolved=record,
        as_of=as_of,
    )


def _posterior_view(p: trust_posteriors.Posterior) -> PosteriorView:
    return PosteriorView(
        source=p.source,
        slot=p.slot,
        alpha=p.alpha,
        beta=p.beta,
        mean=p.mean,
        observations=p.observations,
    )


# NOTE: literal-path routes are declared BEFORE `{source_name}` so FastAPI
# matches /trust/posteriors and /trust/feedback exactly rather than
# treating them as source names.

@router.get("/trust", response_model=list[TrustScore])
def list_trust_scores() -> list[TrustScore]:
    """All configured per-source trust scores. Sources without an entry
    use the default (``trust_config.DEFAULT_TRUST``)."""
    with db.connect() as conn:
        scores = trust_config.list_scores(conn)
    return [TrustScore(source=s, trust_score=v) for s, v in scores.items()]


@router.get("/trust/posteriors", response_model=list[PosteriorView])
def list_posteriors() -> list[PosteriorView]:
    """All Beta posteriors recorded so far. Pairs without a row use the
    uniform prior (Beta(1, 1))."""
    with db.connect() as conn:
        return [_posterior_view(p) for p in trust_posteriors.list_posteriors(conn)]


@router.get(
    "/trust/posteriors/{source_name}/{slot_name}",
    response_model=PosteriorView,
)
def get_posterior(source_name: str, slot_name: str) -> PosteriorView:
    with db.connect() as conn:
        spec = _published_or_404(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        if not any(s.name == slot_name for s in spec.slots):
            raise HTTPException(404, f"Slot {slot_name!r} not on the published spec.")
        return _posterior_view(trust_posteriors.get_posterior(conn, source_name, slot_name))


@router.delete(
    "/trust/posteriors/{source_name}/{slot_name}",
    dependencies=[Depends(require_user)],
)
def reset_posterior(source_name: str, slot_name: str) -> dict[str, Any]:
    """Drop the per-(source, slot) posterior, reverting it to the uniform prior."""
    with db.connect() as conn:
        existed = trust_posteriors.reset_posterior(conn, source_name, slot_name)
    return {"reset": existed, "source": source_name, "slot": slot_name}


@router.post(
    "/trust/feedback",
    response_model=PosteriorView,
    dependencies=[Depends(require_user)],
)
def submit_feedback(body: FeedbackBody) -> PosteriorView:
    """Record one Bernoulli observation (source, slot, success) — increments
    α on success, β on failure. Source and slot must be on the published spec."""
    with db.connect() as conn:
        spec = _published_or_404(conn)
        if not any(s.name == body.source for s in spec.sources):
            raise HTTPException(404, f"Source {body.source!r} not on the published spec.")
        if not any(s.name == body.slot for s in spec.slots):
            raise HTTPException(404, f"Slot {body.slot!r} not on the published spec.")
        post = trust_posteriors.record_feedback(conn, body.source, body.slot, body.success)
    return _posterior_view(post)


# Parameterized `/trust/{source_name}` routes go LAST so the literal-path
# routes above (/trust/posteriors, /trust/feedback) take precedence.

@router.get("/trust/{source_name}", response_model=TrustScore)
def get_trust_score(source_name: str) -> TrustScore:
    with db.connect() as conn:
        spec = _published_or_404(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        score = trust_config.get_score(conn, source_name)
    return TrustScore(source=source_name, trust_score=score)


@router.put(
    "/trust/{source_name}",
    response_model=TrustScore,
    dependencies=[Depends(require_user)],
)
def set_trust_score(source_name: str, body: TrustUpdate) -> TrustScore:
    with db.connect() as conn:
        spec = _published_or_404(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        trust_config.set_score(conn, source_name, body.trust_score)
    return TrustScore(source=source_name, trust_score=body.trust_score)


# ─── Corrections ─────────────────────────────────────────────────────────────


def _validate_property_value(slot: Slot, value: Any) -> Any:
    """Validate the corrected value against the slot's full constraint set
    (type, pattern, min/max, Literal-from-permissible-values, multivalued
    list-shape). Raises HTTPException(422) on mismatch with FastAPI-shaped
    error detail.

    Uses the same constraint-application logic as ingest's per-source row
    model so corrections enforce the same rules as ingestion."""
    one_field = build_value_model_for_slot(slot)
    try:
        return one_field.model_validate({slot.name: value}).model_dump()[slot.name]
    except ValidationError as exc:
        errors = [{**e, "loc": ("body", "value", *e["loc"])} for e in exc.errors()]
        raise HTTPException(422, detail=errors)


@router.post(
    "/corrections",
    response_model=CorrectionResponse,
)
def submit_correction(
    body: Correction,
    principal: Principal = Depends(require_user),
) -> CorrectionResponse:
    """Submit a typed correction. Auto-applies in one transaction:
    audit row + per-class data mutation + bandit feedback against
    disagreeing sources. 422 on payload type mismatch; 404 on unknown
    class/slot/canonical_id."""
    with db.connect() as conn:
        spec = _published_or_404(conn)

        if isinstance(body, PropertyCorrection):
            cls = _resolve_class(spec, body.class_name)
            slot = next((s for s in cls.slots if s.name == body.slot), None)
            if slot is None:
                raise HTTPException(
                    404, f"Slot {body.slot!r} not on class {cls.name!r}"
                )
            value = _validate_property_value(slot, body.value)
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_property_correction(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
                slot_name=body.slot,
                value=value,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="property",
                applied_revision=spec_revision,
            )

        if isinstance(body, Merge):
            cls = _resolve_class(spec, body.class_name)
            if not body.merge_canonical_ids:
                raise HTTPException(400, "merge_canonical_ids must be non-empty")
            if body.keep_canonical_id in body.merge_canonical_ids:
                raise HTTPException(
                    400,
                    "keep_canonical_id must not appear in merge_canonical_ids",
                )
            # Dedupe while preserving order.
            seen: set[str] = set()
            deduped: list[str] = []
            for cid in body.merge_canonical_ids:
                if cid not in seen:
                    seen.add(cid)
                    deduped.append(cid)
            if not graph_store.canonical_id_exists(
                conn, cls=cls, canonical_id=body.keep_canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"keep_canonical_id {body.keep_canonical_id!r} has no "
                    f"contributions for class {cls.name!r}",
                )
            for cid in deduped:
                if not graph_store.canonical_id_exists(
                    conn, cls=cls, canonical_id=cid,
                ):
                    raise HTTPException(
                        404,
                        f"merge_canonical_id {cid!r} has no contributions "
                        f"for class {cls.name!r}",
                    )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_merge(
                conn,
                cls=cls,
                keep_canonical_id=body.keep_canonical_id,
                merge_canonical_ids=deduped,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="merge",
                applied_revision=spec_revision,
            )

        if isinstance(body, Split):
            cls = _resolve_class(spec, body.class_name)
            if len(body.partitions) < 2:
                raise HTTPException(400, "split requires at least 2 partitions")
            if not body.partitions:
                raise HTTPException(400, "partitions must be non-empty")
            if not graph_store.canonical_id_exists(
                conn, cls=cls, canonical_id=body.source_canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"source_canonical_id {body.source_canonical_id!r} has no "
                    f"current contributions for class {cls.name!r}",
                )
            for new_cid in body.partitions:
                if graph_store.canonical_id_exists(conn, cls=cls, canonical_id=new_cid):
                    raise HTTPException(
                        409,
                        f"new_canonical_id {new_cid!r} already exists for "
                        f"class {cls.name!r}",
                    )
            # Validate all (source, source_row_id) appear exactly once.
            all_members: list[tuple[str, str]] = []
            for members in body.partitions.values():
                all_members.extend(members)
            if len(all_members) != len(set(all_members)):
                raise HTTPException(
                    400, "each (source, source_row_id) must appear in exactly one partition"
                )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_split(
                conn,
                cls=cls,
                source_canonical_id=body.source_canonical_id,
                partitions=body.partitions,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="split",
                applied_revision=spec_revision,
            )

        if isinstance(body, Add):
            cls = _resolve_class(spec, body.class_name)
            if graph_store.canonical_id_exists(
                conn, cls=cls, canonical_id=body.new_canonical_id,
            ):
                raise HTTPException(
                    409,
                    f"new_canonical_id {body.new_canonical_id!r} already exists "
                    f"for class {cls.name!r}",
                )
            # Validate slot values against the class model.
            SyntheticRowModel = build_row_model_for_class(cls)
            try:
                validated_values = SyntheticRowModel.model_validate(body.values).model_dump(
                    exclude_none=True
                )
            except Exception as exc:
                raise HTTPException(422, detail=str(exc))
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_add(
                conn,
                cls=cls,
                new_canonical_id=body.new_canonical_id,
                values=validated_values,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="add",
                applied_revision=spec_revision,
            )

        if isinstance(body, Tombstone):
            cls = _resolve_class(spec, body.class_name)
            if not graph_store.canonical_id_exists(
                conn, cls=cls, canonical_id=body.canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"canonical_id {body.canonical_id!r} has no current "
                    f"contributions for class {cls.name!r}",
                )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_tombstone(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
                reason=body.reason,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="tombstone",
                applied_revision=spec_revision,
            )

        if isinstance(body, RejectContribution):
            cls = _resolve_class(spec, body.class_name)
            if not graph_store.canonical_id_exists(
                conn, cls=cls, canonical_id=body.canonical_id,
            ):
                raise HTTPException(
                    404,
                    f"canonical_id {body.canonical_id!r} has no current "
                    f"contributions for class {cls.name!r}",
                )
            spec_revision = spec_store.get_published_revision(conn)
            correction_id = graph_corrections.apply_reject_contribution(
                conn,
                cls=cls,
                canonical_id=body.canonical_id,
                source=body.source,
                spec_revision=spec_revision,
                applied_by=principal.username,
                payload_for_log=body.model_dump(),
            )
            return CorrectionResponse(
                id=correction_id,
                correction_type="reject_contribution",
                applied_revision=spec_revision,
            )

        # Unreachable — all union members are handled above.
        raise HTTPException(
            501,
            f"Correction type {body.type!r} not implemented yet (open).",
        )


@router.get("/corrections")
def list_corrections(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    with db.connect() as conn:
        return db.corrections.list_audit_log(conn, limit=limit)


# ─── GraphQL query endpoint ───────────────────────────────────────────────────


class GraphQLBody(_StrictBase):
    query: str
    variables: dict[str, Any] | None = None
    operation_name: str | None = None


@router.post(
    "/query",
    dependencies=[Depends(require_user)],
    tags=["graph"],
)
def graphql_query(body: GraphQLBody) -> dict[str, Any]:
    """Execute a GraphQL query against the published graph.

    Schema is derived from the currently-published spec.  Each OntologyClass
    is queryable with optional ``where``, ``limit``, ``offset``, and
    ``as_of`` arguments.  Returns ``{data: ..., errors: ...}`` in the
    standard GraphQL response envelope.

    Traversal (relation joins) and projection (field selection in SQL) are
    out of scope for this slice; full rows are returned for matching entities.
    """
    from knot.api.graphql_schema import get_or_build_schema
    from knot.db import spec_store

    with db.connect() as conn:
        spec = _published_or_404(conn)
        content_hash = spec_store.get_published_content_hash(conn) or ""

    schema = get_or_build_schema(spec, content_hash)
    result = schema.execute_sync(
        body.query,
        variable_values=body.variables,
        operation_name=body.operation_name,
    )
    response: dict[str, Any] = {}
    if result.data is not None:
        response["data"] = result.data
    if result.errors:
        response["errors"] = [
            {"message": str(e), "locations": getattr(e, "locations", None)}
            for e in result.errors
        ]
    return response


# ─── Constraint check ────────────────────────────────────────────────────────


class ViolationRow(_StrictBase):
    rule_id: str
    class_name: str
    slot_name: str | None
    offending_pk: str
    detail: str


class ConstraintCheckResponse(_StrictBase):
    violations: list[ViolationRow]


@router.post(
    "/constraints/check",
    response_model=ConstraintCheckResponse,
    dependencies=[Depends(require_user)],
)
def check_constraints() -> ConstraintCheckResponse:
    """Run every published constraint against the current data plane.

    Returns the union of offending rows across all constraints in the
    uniform violation shape: (rule_id, class_name, slot_name, offending_pk,
    detail).  An empty ``violations`` list means all constraints pass.

    Requires an authenticated user (admin or any user).
    """
    from knot.db.sql_compiler import compile_constraint

    violations: list[ViolationRow] = []

    with db.connect() as conn:
        spec = _published_or_404(conn)
        classes_by_name = {c.name: c for c in spec.classes}

        for constraint in spec.constraints:
            cls = classes_by_name.get(constraint.primary.name)
            if cls is None or cls.abstract:
                continue
            stmt, params = compile_constraint(constraint, cls)
            try:
                rows = conn.execute(stmt, params).fetchall()
            except Exception:
                # Table may not exist yet (e.g., abstract class or migration
                # lag); skip rather than crash the whole check.
                continue
            for row in rows:
                violations.append(ViolationRow(
                    rule_id=row[0],
                    class_name=row[1],
                    slot_name=row[2],
                    offending_pk=str(row[3]),
                    detail=row[4] or "",
                ))

    return ConstraintCheckResponse(violations=violations)
