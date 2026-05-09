"""Ingest orchestration — validation, canonical-id resolution, INSERT, dispatch.

Composes ``knot.db`` primitives + the extension dispatcher to land a batch
of source rows in one transaction. Per-event side effects (DQ recording,
constraint validation) are handlers on ``RowsIngested``.

Contract:
  - Pydantic row validation against the source's class shape.
  - ``RowsIngesting`` dispatched (handlers may mutate rows + populate
    ``canonical_ids``; default ER fills it from the identifier slot).
  - INSERTs via ``graph_store.insert_rows`` inside a transaction.
  - ``RowsIngested`` dispatched inside the same transaction so handler
    side-effects (DQ, etc.) roll back consistently if a later handler
    raises (e.g. constraint validator raising ``ConstraintViolations``).

Routes catch typed exceptions and translate to HTTP. No ``HTTPException``,
no SQL strings, no psycopg imports here.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pydantic import ValidationError

from knot.api.row_models import build_row_model
from knot.db import graph_store
from knot.extensions import RequestContext, Session, dispatch
from knot.extensions.events import RowsIngested, RowsIngesting
from knot.spec import Source, Spec


class IngestValidationError(Exception):
    """Raised when one or more rows fail Pydantic validation against the
    source's class shape. ``errors`` is the FastAPI-shaped error list."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} row validation error(s)")


async def ingest_rows(
    conn: psycopg.AsyncConnection,
    *,
    source: Source,
    spec: Spec,
    spec_revision: int,
    rows: list[dict[str, Any]],
    batch_id: str,
    validate_constraints: bool = False,
) -> int:
    """Validate + resolve + INSERT a batch of source rows.

    Returns the number of rows inserted. Raises ``IngestValidationError``
    on Pydantic failure (no rows inserted) and ``ConstraintViolations``
    when the constraint-validator extension finds ERROR-severity
    violations (transaction rolled back).
    """
    RowModel = build_row_model(source)
    typed_rows: list[Any] = []
    errors: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            m = RowModel.model_validate(row)
        except ValidationError as exc:
            for err in exc.errors():
                errors.append({**err, "loc": ("body", "rows", i, *err["loc"])})
            continue
        typed_rows.append(m)

    if errors:
        raise IngestValidationError(errors)

    ctx = RequestContext(
        db=Session(conn),
        spec_revision=spec_revision,
        request_id=batch_id,
    )

    pre = RowsIngesting(source=source, spec=spec, rows=typed_rows)
    await dispatch.dispatch(pre, ctx)
    if pre.canonical_ids is None:
        raise RuntimeError("No handler set canonical_ids — default ER extension not registered")

    wire_rows = [r.model_dump(exclude_none=False) for r in pre.rows]
    canonical_ids = pre.canonical_ids

    async with conn.transaction():
        count = await graph_store.insert_rows(
            conn,
            source=source,
            spec_revision=spec_revision,
            rows=wire_rows,
            canonical_ids=canonical_ids,
        )
        await dispatch.dispatch(
            RowsIngested(
                source=source,
                spec=spec,
                rows=pre.rows,
                inserted_count=count,
                canonical_ids=canonical_ids,
                validate_constraints=validate_constraints,
            ),
            ctx,
        )

    return count


class SourceNotOnSpecError(Exception):
    """Raised when ``source_name`` isn't a Source on the published spec."""

    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        super().__init__(f"Source {source_name!r} not on the published spec.")


def find_source(spec: Spec, source_name: str) -> Source:
    """Look up a Source on a Spec; raise ``SourceNotOnSpecError`` if missing."""
    source = next((s for s in spec.sources if s.name == source_name), None)
    if source is None:
        raise SourceNotOnSpecError(source_name)
    return source
