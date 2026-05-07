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
  - /graph/query                                         ontology-shaped queries (later)
  - /graph/corrections                                   user corrections (later)

Storage shape (locked, see ``knot.db.migration``): per-class postgres
tables; columns mirror stored slots; primary key
``(_source, _source_row_id)``; each row carries ``_canonical_id``
(initially the source identifier-slot value, refined by ER), ``_ingest_at``,
and ``_spec_revision`` (FK into spec_revisions for audit walk-back).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from knot import db
from knot.db import graph_store, resolve, spec_store, trust_config
from knot.ontology import OntologyClass, Spec
from knot.ontology.row_models import build_row_model


class _StrictBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IngestBatch(_StrictBase):
    rows: list[dict[str, Any]]


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
    as_of: Optional[int] = None


class EntityResponse(_StrictBase):
    entity_class: str
    canonical_id: str
    contributions: list[dict[str, Any]]
    as_of: Optional[int] = None


class ResolvedEntityResponse(_StrictBase):
    entity_class: str
    canonical_id: str
    resolved: dict[str, Any]
    as_of: Optional[int] = None


class TrustScore(_StrictBase):
    source: str
    trust_score: float


class TrustUpdate(_StrictBase):
    trust_score: float = Field(..., ge=0.0, le=1.0)


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


@router.post("/ingest/{source_name}", response_model=IngestResponse)
def ingest(source_name: str, body: IngestBatch) -> IngestResponse:
    """Push a batch of rows attributed to a known source.

    Validation:
      - 409 if no spec is published yet.
      - 404 if ``source_name`` isn't a Source on the published spec.
      - 422 with FastAPI-shaped error detail if any row fails Pydantic
        validation against the source's class slot shape (extra="forbid",
        identifier required, types coerced from slot.range, pattern/min/max
        enforced, permissible_values constrained, multivalued list-shape).

    Successful rows are upserted into the per-class table; system columns
    are set from the source + currently-published revision.
    """
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

        count = graph_store.insert_rows(
            conn,
            source=source,
            spec_revision=revision,
            rows=validated,
        )

    return IngestResponse(
        accepted=count,
        source=source_name,
        entity_class=source.entity_class.name,
        spec_revision=revision,
    )


@router.get("/classes/{class_name}", response_model=ListResponse)
def list_class_rows(
    class_name: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    as_of: Optional[int] = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
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
        )
        total = graph_store.count_rows(conn, cls=cls, as_of=as_of)
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
    as_of: Optional[int] = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
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
    as_of: Optional[int] = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
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


@router.get("/trust", response_model=list[TrustScore])
def list_trust_scores() -> list[TrustScore]:
    """All configured per-source trust scores. Sources without an entry
    use the default (``trust_config.DEFAULT_TRUST``)."""
    with db.connect() as conn:
        scores = trust_config.list_scores(conn)
    return [TrustScore(source=s, trust_score=v) for s, v in scores.items()]


@router.get("/trust/{source_name}", response_model=TrustScore)
def get_trust_score(source_name: str) -> TrustScore:
    with db.connect() as conn:
        spec = _published_or_404(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        score = trust_config.get_score(conn, source_name)
    return TrustScore(source=source_name, trust_score=score)


@router.put("/trust/{source_name}", response_model=TrustScore)
def set_trust_score(source_name: str, body: TrustUpdate) -> TrustScore:
    with db.connect() as conn:
        spec = _published_or_404(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        trust_config.set_score(conn, source_name, body.trust_score)
    return TrustScore(source=source_name, trust_score=body.trust_score)
