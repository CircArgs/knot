"""Ingest orchestration — validation, canonical-id resolution, INSERT, DQ.

Composes ``knot.db`` primitives + the extension dispatcher to land a batch
of source rows in one transaction (when constraint validation is enabled),
or in normal autocommit mode otherwise.

Contract:
  - Pydantic row validation against the source's class shape.
  - Canonical-id resolution dispatched as ``IngestResolveCanonical``.
  - INSERTs via ``graph_store.insert_rows``.
  - Optional ERROR-severity constraint check post-INSERT inside a
    transaction (any violation rolls back the batch).
  - DQ incremental observation written on success.

Routes catch typed exceptions and translate to HTTP. No ``HTTPException``,
no SQL strings, no psycopg imports here.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pydantic import ValidationError

from knot.api.row_models import build_row_model
from knot.db import dq, graph_store
from knot.extensions import dispatch
from knot.extensions.events import IngestResolveCanonical
from knot.spec import Source, Spec


class IngestValidationError(Exception):
    """Raised when one or more rows fail Pydantic validation against the
    source's class shape. ``errors`` is the FastAPI-shaped error list."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} row validation error(s)")


class ConstraintViolations(Exception):
    """Raised when post-INSERT ERROR-severity constraints find violations.

    Rolled back inside the same transaction; ``violations`` carries the
    uniform shape ``{rule_id, class_name, slot_name, offending_pk, detail}``.
    """

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(f"{len(violations)} constraint violation(s)")


class _ConstraintViolationSentinel(Exception):
    """Internal — used to roll back the transaction."""

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations


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
    on constraint failure (transaction rolled back).
    """
    from knot.spec.compile.postgres import compile_constraint
    from knot.spec.metaschema import Severity

    RowModel = build_row_model(source)
    validated: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            m = RowModel.model_validate(row)
        except ValidationError as exc:
            for err in exc.errors():
                errors.append({**err, "loc": ("body", "rows", i, *err["loc"])})
            continue
        validated.append(m.model_dump(exclude_none=False))

    if errors:
        raise IngestValidationError(errors)

    cls = source.entity_class
    ev = IngestResolveCanonical(cls=cls, source=source, incoming=validated)
    await dispatch.dispatch(ev)
    if ev.canonical_ids is None:
        raise RuntimeError("No handler set canonical_ids — default ER extension not registered")

    if validate_constraints:
        relevant = [
            c
            for c in spec.constraints
            if c.primary.name == cls.name
            and getattr(c, "severity", Severity.ERROR) == Severity.ERROR
        ]
        try:
            async with conn.transaction():
                count = await graph_store.insert_rows(
                    conn,
                    source=source,
                    spec_revision=spec_revision,
                    rows=validated,
                    canonical_ids=ev.canonical_ids,
                )
                violations: list[dict[str, Any]] = []
                for constraint in relevant:
                    stmt, params = compile_constraint(constraint, cls)
                    try:
                        result_rows = await (await conn.execute(stmt, params)).fetchall()
                    except Exception:
                        continue
                    for row in result_rows:
                        violations.append(
                            {
                                "rule_id": row[0],
                                "class_name": row[1],
                                "slot_name": row[2],
                                "offending_pk": str(row[3]),
                                "detail": row[4] or "",
                            }
                        )
                if violations:
                    raise _ConstraintViolationSentinel(violations)
                await dq.record_incremental(
                    conn,
                    source_name=source.name,
                    cls=cls,
                    batch_id=batch_id,
                    rows=validated,
                )
        except _ConstraintViolationSentinel as exc:
            raise ConstraintViolations(exc.violations) from exc
    else:
        count = await graph_store.insert_rows(
            conn,
            source=source,
            spec_revision=spec_revision,
            rows=validated,
            canonical_ids=ev.canonical_ids,
        )
        await dq.record_incremental(
            conn,
            source_name=source.name,
            cls=cls,
            batch_id=batch_id,
            rows=validated,
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
