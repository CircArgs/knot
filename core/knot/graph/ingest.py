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
from knot.spec import Source, SourceBinding, Spec
from knot.spec.metaschema import NullSemantics


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
    validate_constraints: bool = False,
) -> int:
    """Validate + resolve + INSERT a batch of source rows.

    ``binding`` is the ``SourceBinding`` that describes which source→class
    mapping to use. Slot mappings, defaults, and null_semantics are applied
    before Pydantic validation.

    Returns the number of rows inserted. Raises ``IngestValidationError``
    on Pydantic failure (no rows inserted) and ``ConstraintViolations``
    when ``validate_constraints=True`` and ERROR-severity violations are
    found (transaction rolled back).
    """
    from knot.spec.compile.postgres import compile_constraint
    from knot.spec.metaschema import Severity

    # Convenience alias: the Source object (used for extension events).
    source = binding.source
    cls = binding.class_

    # 0. Apply field mappings (rename source_field → slot_name, apply defaults).
    mapped_rows = [_apply_mappings(binding, r) for r in rows]

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
        # 4. INSERT
        count = await graph_store.insert_rows(
            conn,
            source=source,
            cls=cls,
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
                binding=binding,
                spec=spec,
                rows=pre.rows,
                inserted_count=count,
                canonical_ids=pre.canonical_ids,
            ),
            ctx,
        )

    return count
