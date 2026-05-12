"""POST /graph/ingest/{source} — push a batch of source rows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from knot import db
from knot.api.auth.security import require_user
from knot.api.graph._common import StrictBase, published_or_409
from knot.api.middleware import get_request_id
from knot.db import spec_store
from knot.graph import ingest as graph_ingest
from knot.graph.ingest import ConstraintViolations

router = APIRouter()


class IngestBatch(StrictBase):
    rows: list[dict[str, Any]] = Field(..., max_length=10_000)


class IngestResponse(StrictBase):
    accepted: int
    source: str
    entity_class: str
    spec_revision: int


@router.post(
    "/ingest/{source_name}",
    response_model=IngestResponse,
    dependencies=[Depends(require_user)],
)
async def ingest(
    source_name: str,
    body: IngestBatch,
    class_name: str | None = Query(
        None,
        description=(
            "Target class name. Required when a source has bindings to multiple "
            "classes; optional when only one binding exists for the source."
        ),
    ),
    validate_constraints: bool = Query(
        False,
        description=(
            "When true, run all published ERROR-severity constraints whose "
            "primary_class matches the source's class after INSERTs. If any "
            "violation is found the entire batch is rolled back and a 422 is "
            "returned with the violation list."
        ),
    ),
) -> IngestResponse:
    """Push a batch of rows attributed to a known source binding.

    Validation:
      - 409 if no spec is published yet.
      - 404 if no SourceBinding matches (source_name, class_name).
      - 422 with FastAPI-shaped error detail if any row fails Pydantic
        validation against the binding's class slot shape.

    When ``validate_constraints=true``, post-INSERT constraint check runs
    inside a transaction; violations roll back the batch.
    """
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        try:
            binding = graph_ingest.find_source_binding(spec, source_name, class_name)
        except graph_ingest.SourceBindingNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc

        revision = await spec_store.get_published_revision(conn)
        try:
            count = await graph_ingest.ingest_rows(
                conn,
                binding=binding,
                spec=spec,
                spec_revision=revision,
                rows=body.rows,
                batch_id=get_request_id(),
                validate_constraints=validate_constraints,
            )
        except graph_ingest.IngestValidationError as exc:
            raise HTTPException(422, detail=exc.errors) from exc
        except ConstraintViolations as exc:
            raise HTTPException(422, detail={"violations": exc.violations}) from exc

    return IngestResponse(
        accepted=count,
        source=source_name,
        entity_class=binding.class_.name,
        spec_revision=revision,
    )
