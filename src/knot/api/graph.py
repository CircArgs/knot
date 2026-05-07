"""Graph router — data plane.

Distinct from the spec router (which authors meaning): this router pushes,
reads, and corrects facts in the graph. No draft lifecycle — every mutation
validates against the *currently published* spec and lands directly.

Endpoint groups under ``/graph``:
  - /graph/ingest/{source_name}    push source rows (data ingest)
  - /graph/{class}/{canonical_id}  per-entity reads (later)
  - /graph/query                   ontology-shaped queries (later)
  - /graph/corrections             user corrections (later)

Storage shape (locked, see ``knot.db.migration``): per-class postgres
tables; columns mirror stored slots; primary key
``(_source, _source_row_id)``; each row carries ``_canonical_id``
(initially the source identifier-slot value, refined by ER), ``_ingest_at``,
and ``_spec_revision`` (FK into spec_revisions for audit walk-back).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from knot import db
from knot.db import graph_store, spec_store
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


router = APIRouter(prefix="/graph", tags=["graph"])


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
        spec = spec_store.get_published(conn)
        if spec is None:
            raise HTTPException(409, "No spec is published yet.")

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
