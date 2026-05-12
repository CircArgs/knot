"""Ingest orchestration — validation, canonical-id resolution, INSERT,
constraint check, DQ recording. Built-in behaviors run inline; the
extensions seam fires RowsIngesting (pre-INSERT) and RowsIngested
(post-INSERT) so teams can plug in custom ER / DQ / audit / etc.
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from psycopg import sql
from pydantic import ValidationError

from knot.api.row_models import build_row_model
from knot.db import dq, graph_store
from knot.extensions import RequestContext, Session, dispatch
from knot.extensions.events import RowsIngested, RowsIngesting
from knot.spec import Source, SourceBinding, Spec
from knot.spec.metaschema import Array, ClassRef, NullSemantics

_logger = logging.getLogger(__name__)


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


class SourceBindingNotFoundError(Exception):
    """Raised when no SourceBinding matches (source_name, class_name)."""

    def __init__(self, source_name: str, class_name: str | None) -> None:
        self.source_name = source_name
        self.class_name = class_name
        key = f"{source_name}__{class_name}" if class_name else source_name
        super().__init__(f"No SourceBinding found for {key!r} on the published spec.")


def find_source(spec: Spec, source_name: str) -> Source:
    """Look up a Source on a Spec; raise ``SourceNotOnSpecError`` if missing."""
    source = next((s for s in spec.sources if s.name == source_name), None)
    if source is None:
        raise SourceNotOnSpecError(source_name)
    return source


def _resolve_classref_values(
    binding: SourceBinding,
    row: dict[str, Any],
    source_name: str,
) -> dict[str, Any]:
    """For each ClassRef slot in the binding's class, if the row carries a
    raw source-native ID (no ':' prefix), expand it to a canonical_id using
    the same ``{source_name}:{value}`` pattern applied to the row's own
    identifier slot.

    Array[ClassRef] slots are handled element-wise.  Values that already
    contain ':' are assumed to be canonical_ids and are passed through
    unchanged.  None values are skipped.

    Limitation: this assumes the referenced entity comes from the SAME source.
    Cross-source FK references are not supported here.
    """
    from knot.spec.effective_slots import effective_slots

    out = dict(row)
    for slot in effective_slots(binding.class_):
        slot_type = slot.type
        if slot_type is None:
            continue
        val = out.get(slot.name)
        if val is None:
            continue
        if isinstance(slot_type, ClassRef):
            if isinstance(val, str) and ":" not in val:
                out[slot.name] = f"{source_name}:{val}"
        elif isinstance(slot_type, Array) and isinstance(slot_type.of, ClassRef):
            if isinstance(val, list):
                out[slot.name] = [
                    f"{source_name}:{v}" if isinstance(v, str) and ":" not in v else v
                    for v in val
                ]
    return out


def find_source_binding(
    spec: Spec, source_name: str, class_name: str | None = None
) -> SourceBinding:
    """Look up a SourceBinding by source_name (and optionally class_name).

    When ``class_name`` is None and exactly one binding exists for the source,
    it is returned. Raises ``SourceBindingNotFoundError`` otherwise.
    """
    candidates = [b for b in spec.source_bindings if b.source.name == source_name]
    if class_name is not None:
        candidates = [b for b in candidates if b.class_.name == class_name]
    if len(candidates) == 1:
        return candidates[0]
    raise SourceBindingNotFoundError(source_name, class_name)


def _apply_mappings(
    binding: SourceBinding,
    row: dict[str, Any],
) -> dict[str, Any]:
    """Translate a raw source row through the binding's SlotMapping rules.

    For each SlotMapping:
      - Read ``source_field`` from the raw row (fall back to slot name).
      - If value is None: apply ``null_semantics`` — NO_CLAIM leaves None;
        ASSERTED_ABSENT sets to a sentinel that downstream can act on
        (here we pass None, which is correct for SQL NULL).
      - Apply ``default`` when value is absent/None and default is set.

    Returns a new dict keyed by slot names (not source field names).
    """
    if not binding.mappings:
        return row

    out: dict[str, Any] = dict(row)
    for m in binding.mappings:
        source_field = m.source_field
        value = row.get(source_field)
        if value is None:
            if m.default is not None:
                value = m.default
            elif m.null_semantics == NullSemantics.ASSERTED_ABSENT:
                value = None  # explicit NULL — stays None for SQL storage
            # NO_CLAIM: leave None as-is
        slot_name = m.slot.name
        if source_field != slot_name:
            out.pop(source_field, None)
        out[slot_name] = value
    return out


async def ingest_rows(
    conn: psycopg.AsyncConnection,
    *,
    binding: SourceBinding,
    spec: Spec,
    spec_revision: int,
    rows: list[dict[str, Any]],
    batch_id: str,
) -> int:
    """Validate + resolve + INSERT a batch of source rows.

    ``binding`` is the ``SourceBinding`` that describes which source→class
    mapping to use. Slot mappings, defaults, and null_semantics are applied
    before Pydantic validation.

    Returns the number of rows inserted. Raises ``IngestValidationError``
    on Pydantic failure (no rows inserted). Raises ``ConstraintViolations``
    when any ERROR-severity constraint (scoped to the batch) fires; the
    transaction rolls back.  WARNING-severity constraints are logged but
    never block ingest.
    """
    from knot.spec.metaschema import Severity

    # Convenience alias: the Source object (used for extension events).
    source = binding.source
    cls = binding.class_

    # 0. Apply field mappings (rename source_field → slot_name, apply defaults).
    mapped_rows = [_apply_mappings(binding, r) for r in rows]

    # 0b. Resolve raw source-native IDs in ClassRef slots to canonical_ids.
    mapped_rows = [
        _resolve_classref_values(binding, r, source.name) for r in mapped_rows
    ]

    # 1. Pydantic validation
    RowModel = build_row_model(binding)
    typed_rows: list[Any] = []
    errors: list[dict[str, Any]] = []
    for i, row in enumerate(mapped_rows):
        try:
            typed_rows.append(RowModel.model_validate(row))
        except ValidationError as exc:
            for err in exc.errors():
                errors.append({**err, "loc": ("body", "rows", i, *err["loc"])})
    if errors:
        raise IngestValidationError(errors)

    ctx = RequestContext(
        db=Session(conn),
        spec_revision=spec_revision,
        request_id=batch_id,
    )

    # 2. Pre-INSERT extension hook (teams may override canonical_ids,
    #    mutate rows, etc.).
    pre = RowsIngesting(source=source, binding=binding, spec=spec, rows=typed_rows)
    await dispatch.dispatch(pre, ctx)

    # 3. Built-in default canonical_id: {source_name}:{identifier_slot_value}
    if pre.canonical_ids is None:
        id_name = binding.identifier_slot.name
        pre.canonical_ids = [f"{source.name}:{getattr(r, id_name)}" for r in pre.rows]

    wire_rows = [r.model_dump(exclude_none=False) for r in pre.rows]

    async with conn.transaction():
        # 4. INSERT — returns (knot_row_ids, count) for batch-scoped checks.
        inserted_row_ids, count = await graph_store.insert_rows(
            conn,
            source=source,
            cls=cls,
            spec_revision=spec_revision,
            rows=wire_rows,
            canonical_ids=pre.canonical_ids,
        )

        # 5. Built-in: post-INSERT constraint checks, scoped to this batch.
        #    ERROR-severity: always run; violations → 422 + rollback.
        #    WARNING-severity: always run; violations logged, never block.
        #
        #    Batch scope: the compiled constraint SQL is wrapped as a subquery
        #    and filtered to rows whose _knot_row_id is in the batch, so
        #    pre-existing violating rows don't taint the new batch.
        error_violations: list[dict[str, Any]] = []
        from knot.spec.effective_constraints import effective_constraints

        relevant = effective_constraints(cls, spec)
        if relevant:
            from knot.spec.compile.postgres._context import CompileContext
            from knot.spec.compile.postgres._naming import bindings_table_id, table_id
            from knot.spec.sql_validate import compile_to_sql

            for constraint in relevant:
                is_error = getattr(constraint, "severity", Severity.ERROR) == Severity.ERROR
                ctx2 = CompileContext(primary_class=cls, alias="s")
                try:
                    body_sql = compile_to_sql(constraint.body, cls, ctx2)
                except Exception as exc:
                    if is_error:
                        error_violations.append(
                            {
                                "rule_id": constraint.name,
                                "class_name": constraint.primary.name,
                                "slot_name": None,
                                "offending_pk": "*",
                                "detail": f"compile failure: {exc}",
                            }
                        )
                    continue

                # Batch-scoped constraint query: only rows in this batch.
                scoped_stmt = sql.SQL(
                    "SELECT"
                    " {rule_id} AS rule_id,"
                    " {class_name} AS class_name,"
                    " NULL::text AS slot_name,"
                    " b.canonical_id AS offending_pk,"
                    " row_to_json(s)::text AS detail"
                    " FROM {src_table} s"
                    " JOIN {bind_table} b"
                    "   ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
                    " WHERE NOT ({body})"
                    "   AND s._knot_row_id = ANY(%s::uuid[])"
                ).format(
                    rule_id=sql.Literal(constraint.name),
                    class_name=sql.Literal(cls.name),
                    src_table=table_id(cls),
                    bind_table=bindings_table_id(cls),
                    body=body_sql,
                )

                try:
                    result_rows = await (
                        await conn.execute(scoped_stmt, [inserted_row_ids])
                    ).fetchall()
                except Exception as exc:
                    if is_error:
                        error_violations.append(
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
                    entry = {
                        "rule_id": r[0],
                        "class_name": r[1],
                        "slot_name": r[2],
                        "offending_pk": str(r[3]),
                        "detail": r[4] or "",
                    }
                    if is_error:
                        error_violations.append(entry)
                    else:
                        _logger.warning(
                            "Constraint warning %s on %s pk=%s: %s",
                            r[0], r[1], r[3], r[4] or "",
                        )

        if error_violations:
            raise ConstraintViolations(error_violations)

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
                binding=binding,
                spec=spec,
                rows=pre.rows,
                inserted_count=count,
                canonical_ids=pre.canonical_ids,
            ),
            ctx,
        )

    return count
