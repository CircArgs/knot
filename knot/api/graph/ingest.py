"""POST /graph/ingest/{source} — push a batch of source rows."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field, ValidationError

from knot import db
from knot.api.graph._common import StrictBase, published_or_409
from knot.api.row_models import build_row_model
from knot.db import dq, graph_store, spec_store
from knot.extensions import dispatch
from knot.extensions.events import IngestResolveCanonical
from knot.api.middleware import get_request_id
from knot.api.auth.security import require_user


router = APIRouter()


class IngestBatch(StrictBase):
    rows: list[dict[str, Any]] = Field(..., max_length=10_000)


class IngestResponse(StrictBase):
    accepted: int
    source: str
    entity_class: str
    spec_revision: int


class _ConstraintViolationError(Exception):
    """Internal sentinel raised inside a transaction to trigger rollback."""

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(f"{len(violations)} constraint violation(s)")


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
            "primary_class matches the source's class after INSERTs. If any "
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
        validation against the source's class slot shape.

    When ``validate_constraints=true``, post-INSERT constraint check runs
    inside a transaction; violations roll back the batch.
    """
    from knot.spec.compile.sql.dialects.postgres import compile_constraint
    from knot.spec.metaschema import Severity

    with db.connect() as conn:
        spec = published_or_409(conn)
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

        cls = source.entity_class
        ev = IngestResolveCanonical(cls=cls, source=source, incoming=validated)
        dispatch.dispatch(ev)
        if ev.canonical_ids is None:
            raise RuntimeError(
                "No handler set canonical_ids — default ER extension not registered"
            )

        if validate_constraints:
            relevant = [
                c for c in spec.constraints
                if c.primary.name == cls.name
                and getattr(c, "severity", Severity.ERROR) == Severity.ERROR
            ]
            try:
                with conn.transaction():
                    count = graph_store.insert_rows(
                        conn, source=source, spec_revision=revision,
                        rows=validated, canonical_ids=ev.canonical_ids,
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
                conn, source=source, spec_revision=revision,
                rows=validated, canonical_ids=ev.canonical_ids,
            )
            dq.record_incremental(
                conn,
                source_name=source.name,
                cls=cls,
                batch_id=get_request_id(),
                rows=validated,
            )

    return IngestResponse(
        accepted=count,
        source=source_name,
        entity_class=source.entity_class.name,
        spec_revision=revision,
    )
