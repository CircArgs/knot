"""Ingest orchestration — validation, canonical-id resolution, INSERT,
constraint check, DQ recording. Built-in behaviors run inline; the
extensions seam fires RowsIngesting (pre-INSERT) and RowsIngested
(post-INSERT) so teams can plug in custom ER / DQ / audit / etc.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pydantic import ValidationError

from knot.api.row_models import build_row_model
from knot.db import dq, graph_store
from knot.extensions import RequestContext, Session, dispatch
from knot.extensions.events import RowsIngested, RowsIngesting
from knot.spec import Source, Spec


class IngestValidationError(Exception):
    """Pydantic validation failure on one or more rows."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} row validation error(s)")


class ConstraintViolations(Exception):
    """One or more ERROR-severity constraints fired post-INSERT.

    Transaction rolls back. Synthetic compile/execute failures surface
    as violations with ``offending_pk='*'``.
    """

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(f"{len(violations)} constraint violation(s)")


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
    when ``validate_constraints=True`` and ERROR-severity violations are
    found (transaction rolled back).
    """
    from knot.spec.compile.postgres import compile_constraint
    from knot.spec.metaschema import Severity

    # 1. Pydantic validation
    RowModel = build_row_model(source)
    typed_rows: list[Any] = []
    errors: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        try:
            typed_rows.append(RowModel.model_validate(row))
        except ValidationError as exc:
            for err in exc.errors():
                errors.append({**err, "loc": ("body", "rows", i, *err["loc"])})
    if errors:
        raise IngestValidationError(errors)

    cls = source.entity_class
    ctx = RequestContext(
        db=Session(conn),
        spec_revision=spec_revision,
        request_id=batch_id,
    )

    # 2. Pre-INSERT extension hook (teams may override canonical_ids,
    #    mutate rows, etc.).
    pre = RowsIngesting(source=source, spec=spec, rows=typed_rows)
    await dispatch.dispatch(pre, ctx)

    # 3. Built-in default canonical_id: identifier-slot passthrough
    #    (only if no handler set them).
    if pre.canonical_ids is None:
        id_name = source.identifier_slot.name
        pre.canonical_ids = [str(getattr(r, id_name)) for r in pre.rows]

    wire_rows = [r.model_dump(exclude_none=False) for r in pre.rows]

    async with conn.transaction():
        # 4. INSERT
        count = await graph_store.insert_rows(
            conn,
            source=source,
            spec_revision=spec_revision,
            rows=wire_rows,
            canonical_ids=pre.canonical_ids,
        )

        # 5. Built-in: post-INSERT ERROR-severity constraint check (opt-in).
        if validate_constraints:
            violations: list[dict[str, Any]] = []
            relevant = [
                c
                for c in spec.constraints
                if c.primary.name == cls.name
                and getattr(c, "severity", Severity.ERROR) == Severity.ERROR
            ]
            for constraint in relevant:
                try:
                    stmt, params = compile_constraint(constraint, cls)
                except Exception as exc:
                    violations.append(
                        {
                            "rule_id": constraint.name,
                            "class_name": constraint.primary.name,
                            "slot_name": None,
                            "offending_pk": "*",
                            "detail": f"compile failure: {exc}",
                        }
                    )
                    continue
                try:
                    result_rows = await (await conn.execute(stmt, params)).fetchall()
                except Exception as exc:
                    violations.append(
                        {
                            "rule_id": constraint.name,
                            "class_name": constraint.primary.name,
                            "slot_name": None,
                            "offending_pk": "*",
                            "detail": f"execute failure: {exc}",
                        }
                    )
                    continue
                for r in result_rows:
                    violations.append(
                        {
                            "rule_id": r[0],
                            "class_name": r[1],
                            "slot_name": r[2],
                            "offending_pk": str(r[3]),
                            "detail": r[4] or "",
                        }
                    )
            if violations:
                raise ConstraintViolations(violations)

        # 6. Built-in: DQ recording (always).
        await dq.record_incremental(
            conn,
            source_name=source.name,
            cls=cls,
            batch_id=batch_id,
            rows=wire_rows,
        )

        # 7. Post-INSERT extension hook (teams may audit / push / log).
        await dispatch.dispatch(
            RowsIngested(
                source=source,
                spec=spec,
                rows=pre.rows,
                inserted_count=count,
                canonical_ids=pre.canonical_ids,
            ),
            ctx,
        )

    return count
