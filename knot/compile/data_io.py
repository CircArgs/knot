"""Write-data path — batched SCD2 binding writes.

Single primitive: ``emit_batch_write`` takes a list of ``ClassWrites``
(one per class participating in the batch) and returns a single
transactional SQL script the host runs in one ``execute(sql, params)``
call. Single-row writes are just a one-element list with one row.

For each ``ClassWrites`` the emitter produces:

  1. **bulk close-out** — ``UPDATE … FROM jsonb_array_elements(…)`` that
     sets ``valid_to = now()`` on every prior currently-open binding for
     the ``(identifier, source_name, source_identifier)`` triples in the
     batch.
  2. **bulk insert** — ``INSERT … SELECT FROM jsonb_array_elements(…)``.
     When ``use_mappings=True`` the binding's per-slot SQL projections
     are applied server-side over the raw source fields; otherwise the
     row dicts are interpreted as direct slot values.

When ``enforce=True`` the script ends with a PL/pgSQL ``DO`` block that
runs every validation SELECT whose primary is one of the affected
classes (error severity only) and ``RAISE EXCEPTION`` on any violation;
the surrounding transaction rolls back automatically.

Row payloads are passed as one ``jsonb`` parameter per class, named
``<class_lowercase>_rows``. Postgres' ``jsonb_array_elements`` iterates
each element as ``r`` so mapping expressions reference its keys as bare
columns through the ``raw`` subquery alias (no rewriting of the
mappings is required).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from knot.compile.constraints import emit_validation
from knot.spec import (
    Array,
    ClassKind,
    ClassRef,
    OntologyClass,
    Primitive,
    Slot,
    SourceBinding,
    Spec,
    TypeExpression,
)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassWrites:
    """One class's contribution to a batch write.

    ``binding`` carries the (source, class) identity and, when
    ``use_mappings=True``, the per-slot SQL projections applied
    server-side. ``rows`` is a list of dicts — keyed by **raw source
    field names** when ``use_mappings=True``, or by **class slot names**
    otherwise. Every dict must include the binding's identifier slot
    and ``source_identifier``.
    """

    binding: SourceBinding
    rows: list[dict[str, Any]]
    use_mappings: bool = True


@dataclass(frozen=True)
class BatchWrite:
    """The transactional SQL script + jsonb-array params for a batch."""

    sql: str
    params: dict[str, Any]
    affected_classes: tuple[str, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_concrete(cls: OntologyClass) -> None:
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; only concrete classes "
            f"have bindings tables"
        )


def _bindings_id(cls: OntologyClass, *, schema: str, suffix: str) -> str:
    return f"{schema}.{cls.name.lower()}{suffix}"


def _sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def _jsonb_cast(t: TypeExpression) -> str:
    """Return a postgres cast suffix that pulls a typed value out of a
    jsonb-element row. Handles primitive, array, and class-ref slots."""
    if isinstance(t, Primitive):
        return {
            Primitive.TEXT: "::text",
            Primitive.INTEGER: "::integer",
            Primitive.FLOAT: "::double precision",
            Primitive.BOOLEAN: "::boolean",
            Primitive.DATE: "::date",
            Primitive.TIMESTAMP: "::timestamptz",
        }[t]
    if isinstance(t, Array):
        inner = _jsonb_cast(t.of).removeprefix("::")
        # ARRAY-coerce a jsonb array of scalars into a postgres array.
        return f"::{inner}[]"
    if isinstance(t, ClassRef):
        return "::text"
    raise TypeError(f"unhandled type: {type(t).__name__}")


def _jsonb_extract(slot: Slot, raw_field: str | None = None) -> str:
    """SQL fragment that pulls ``raw_field`` (or ``slot.name`` if not
    given) out of the ``r`` jsonb element with the right cast."""
    field = raw_field or slot.name
    cast = _jsonb_cast(slot.type)
    if isinstance(slot.type, Array):
        # jsonb arrays must round-trip through unnest+ARRAY because
        # direct ``::text[]`` on a jsonb array doesn't work.
        inner = cast.removeprefix("::").removesuffix("[]")
        return (
            f"(SELECT ARRAY(SELECT (value #>> '{{}}')::{inner} "
            f"FROM jsonb_array_elements(r->'{field}') AS value))"
        )
    return f"(r->>'{field}'){cast}"


# ---------------------------------------------------------------------------
# Per-class emitters
# ---------------------------------------------------------------------------


def _emit_raw_subquery(
    binding: SourceBinding,
    rows_param: str,
) -> tuple[str, list[str]]:
    """Build the ``FROM (...) AS raw`` subquery for a mapped binding.

    Returns the SQL and the ordered list of raw field names the host
    must populate in each row dict. Field names = identifier slot,
    ``source_identifier``, plus every raw field declared in any
    mapping's ``SourceMap.uses``."""
    ident = binding.class_.identifier_slot()
    raw_fields: list[str] = [ident.name, "source_identifier"]
    seen = set(raw_fields)
    for source_map in binding.mappings.values():
        for raw_field in source_map.uses:
            if raw_field not in seen:
                raw_fields.append(raw_field)
                seen.add(raw_field)

    # The raw subquery extracts each raw field as text; mapping
    # expressions cast where they care to. The identifier and
    # source_identifier are always text on the bindings table.
    aliases = ",\n".join(
        f"        (r->>'{f}') AS {f}" for f in raw_fields
    )
    sql = (
        "FROM (\n"
        "    SELECT\n"
        f"{aliases}\n"
        f"    FROM jsonb_array_elements(%({rows_param})s::jsonb) AS r\n"
        ") AS raw"
    )
    return sql, raw_fields


def _emit_class_insert(
    cw: ClassWrites,
    *,
    schema: str,
    bindings_suffix: str,
    rows_param: str,
) -> str:
    cls = cw.binding.class_
    _check_concrete(cls)
    ident = cls.identifier_slot()
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(cw.binding.source.name)
    eff_slots = cls.effective_slots()
    insert_columns = ["source_name", "source_identifier"] + [s.name for s in eff_slots]
    columns_csv = ", ".join(insert_columns)

    if cw.use_mappings:
        raw_subquery, _ = _emit_raw_subquery(cw.binding, rows_param)
        select_lines: list[str] = [
            f"    {source_literal}",
            "    raw.source_identifier",
        ]
        for slot in eff_slots:
            if slot.name == ident.name:
                select_lines.append(f"    raw.{ident.name}")
            elif slot.name in cw.binding.mappings:
                select_lines.append(f"    {cw.binding.mappings[slot.name].sql}")
            else:
                select_lines.append("    NULL")
        return (
            f"INSERT INTO {table} ({columns_csv})\n"
            "SELECT\n"
            + ",\n".join(select_lines)
            + "\n"
            + raw_subquery
            + ";"
        )
    else:
        # Direct slot values — each row dict has keys matching slot names.
        select_lines = [
            f"    {source_literal}",
            "    (r->>'source_identifier')::text",
        ]
        for slot in eff_slots:
            select_lines.append(f"    {_jsonb_extract(slot)}")
        return (
            f"INSERT INTO {table} ({columns_csv})\n"
            "SELECT\n"
            + ",\n".join(select_lines)
            + "\n"
            f"FROM jsonb_array_elements(%({rows_param})s::jsonb) AS r;"
        )


def _emit_class_close_out(
    cw: ClassWrites,
    *,
    schema: str,
    bindings_suffix: str,
    rows_param: str,
) -> str:
    cls = cw.binding.class_
    _check_concrete(cls)
    ident = cls.identifier_slot()
    table = _bindings_id(cls, schema=schema, suffix=bindings_suffix)
    source_literal = _sql_literal(cw.binding.source.name)
    # In mapped mode the identifier lives under raw.<ident_name>; in
    # direct-slot mode it's under r->>'<ident_name>'. We use jsonb
    # extraction in both cases for the close-out — simpler.
    return (
        f"UPDATE {table} AS b\n"
        f"SET valid_to = now()\n"
        f"FROM (\n"
        f"    SELECT\n"
        f"        (r->>'{ident.name}') AS {ident.name},\n"
        f"        (r->>'source_identifier') AS source_identifier\n"
        f"    FROM jsonb_array_elements(%({rows_param})s::jsonb) AS r\n"
        f") AS keys\n"
        f"WHERE b.{ident.name} = keys.{ident.name}\n"
        f"  AND b.source_name = {source_literal}\n"
        f"  AND b.source_identifier = keys.source_identifier\n"
        f"  AND b.valid_to IS NULL;"
    )


# ---------------------------------------------------------------------------
# Enforcement block — PL/pgSQL DO that RAISEs on error-severity violations
# ---------------------------------------------------------------------------


def _emit_enforcement_block(
    spec: Spec,
    affected_classes: set[str],
    *,
    schema: str,
) -> str | None:
    """Return a PL/pgSQL DO block that runs validation for the affected
    classes' constraints and raises on any error-severity violation, or
    ``None`` if no error-severity constraints touch the batch.

    Note: validation SELECTs target the canonical table (not bindings)
    by emit_validation's current contract. Until the resolver maintains
    the canonical view from bindings, in-batch enforcement only catches
    constraints checkable against canonical state the host writes
    directly. Retargeting validation to the bindings-current view is a
    separate follow-up item.
    """
    relevant: list[tuple[str, str]] = []
    for c in spec.constraints:
        if c.primary.name not in affected_classes:
            continue
        if c.severity.value != "error":
            continue
        relevant.append((c.name, c.body))
    if not relevant:
        return None

    # Reuse emit_validation to get fully-rewritten SELECTs, then filter.
    all_v = dict(emit_validation(spec, schema=schema))
    union_parts = []
    for name, _body in relevant:
        if name not in all_v:
            continue
        # Strip the trailing ';' for UNION ALL composition.
        union_parts.append(all_v[name].rstrip().rstrip(";"))
    if not union_parts:
        return None
    union_sql = "\n  UNION ALL\n".join(union_parts)

    return (
        "DO $$\n"
        "DECLARE v RECORD;\n"
        "BEGIN\n"
        "  FOR v IN\n"
        f"{union_sql}\n"
        "  LOOP\n"
        "    RAISE EXCEPTION "
        "'knot constraint violated: rule=% class=% pk=% message=%',\n"
        "      v.rule_id, v.class_name, v.offending_pk, COALESCE(v.message, '');\n"
        "  END LOOP;\n"
        "END $$;"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def emit_batch_write(
    spec: Spec,
    writes: list[ClassWrites],
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    enforce: bool = True,
) -> BatchWrite:
    """Return one transactional SQL script for a multi-class batch write."""
    if not writes:
        raise ValueError("emit_batch_write: writes list is empty")

    stmts: list[str] = []
    params: dict[str, Any] = {}
    affected: set[str] = set()

    for cw in writes:
        cls = cw.binding.class_
        _check_concrete(cls)
        affected.add(cls.name)
        # Each class gets a stable param key — collisions across classes
        # would mean duplicate writes; reject explicitly.
        rows_param = f"{cls.name.lower()}_rows"
        if rows_param in params:
            raise ValueError(
                f"duplicate ClassWrites for class {cls.name!r} in batch"
            )
        params[rows_param] = json.dumps(cw.rows)
        stmts.append(
            _emit_class_close_out(
                cw, schema=schema, bindings_suffix=bindings_suffix,
                rows_param=rows_param,
            )
        )
        stmts.append(
            _emit_class_insert(
                cw, schema=schema, bindings_suffix=bindings_suffix,
                rows_param=rows_param,
            )
        )

    if enforce:
        block = _emit_enforcement_block(spec, affected, schema=schema)
        if block is not None:
            stmts.append(block)

    return BatchWrite(
        sql="\n\n".join(stmts),
        params=params,
        affected_classes=tuple(sorted(affected)),
    )


__all__ = [
    "ClassWrites",
    "BatchWrite",
    "emit_batch_write",
]
