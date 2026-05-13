"""Write-data path — SQL machinery for writing one source claim (one
row) to a class's bindings table, using SCD2 semantics.

Two variants:

  - ``emit_binding_write`` takes already-projected slot values. The host
    builds a dict keyed by class slot names; the SQL is a plain
    parameterized ``INSERT``. ``SourceBinding.mappings`` is not consulted.
  - ``emit_mapped_binding_write`` takes raw source-row values. The
    SQL is an ``INSERT … SELECT`` that applies the binding's per-slot
    SQL projection (``SourceBinding.mappings``) server-side. The host
    builds a dict keyed by the raw source-field names the mappings
    reference (discovered by sqlglot parse of each mapping expression).

Both emit a paired **close-out** ``UPDATE`` that sets ``valid_to = now()``
on the prior currently-open binding for the same
``(<identifier>, source_name, source_identifier)`` triple. Host runs the
pair in a single transaction.

All statements use named parameters (psycopg ``%(name)s`` style).

Spec-model validation (NOT NULL, types, identifier present) is handled
by postgres against the DDL the bindings table already carries.
Business-rule validation (``Constraint.body`` predicates) is the
separate ``knot.compile.constraints`` emitter; the host composes both
into the same transaction.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import expressions as exp

from knot.spec import ClassKind, OntologyClass, SourceBinding, Spec


@dataclass(frozen=True)
class BindingWrite:
    """Statements + parameter shapes for one SCD2 binding write."""

    close_out_sql: str
    close_out_columns: tuple[str, ...]
    insert_sql: str
    insert_columns: tuple[str, ...]


def _find_concrete(spec: Spec, name: str) -> OntologyClass:
    for c in spec.classes:
        if isinstance(c, OntologyClass) and c.name == name:
            if c.kind != ClassKind.CONCRETE:
                raise ValueError(
                    f"class {name!r} is {c.kind.value!r}; only concrete classes "
                    f"have bindings tables"
                )
            return c
    raise ValueError(f"no concrete class named {name!r} in spec")


def emit_binding_write(
    spec: Spec,
    class_name: str,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> BindingWrite:
    """Return the two-statement SCD2 write machinery for ``class_name``."""
    cls = _find_concrete(spec, class_name)
    ident = cls.identifier_slot()
    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"

    close_out_columns = (ident.name, "source_name", "source_identifier")
    close_out_sql = (
        f"UPDATE {bindings_table}\n"
        f"SET valid_to = now()\n"
        f"WHERE {ident.name} = %({ident.name})s\n"
        f"  AND source_name = %(source_name)s\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND valid_to IS NULL;"
    )

    slot_columns = tuple(sl.name for sl in cls.effective_slots())
    insert_columns = ("source_name", "source_identifier") + slot_columns
    columns_csv = ", ".join(insert_columns)
    placeholders_csv = ", ".join(f"%({c})s" for c in insert_columns)
    insert_sql = (
        f"INSERT INTO {bindings_table} ({columns_csv})\n"
        f"VALUES ({placeholders_csv});"
    )

    return BindingWrite(
        close_out_sql=close_out_sql,
        close_out_columns=close_out_columns,
        insert_sql=insert_sql,
        insert_columns=insert_columns,
    )


@dataclass(frozen=True)
class MappedBindingWrite:
    """Statements + parameter shape for a mapping-aware binding write.

    The host passes raw source-row values keyed by
    ``insert_input_columns`` (the union of the identifier slot,
    ``source_identifier``, and every raw field name referenced by any
    mapping expression). ``source_name`` is baked into the SQL as a
    literal — the SQL is binding-specific because mappings are.
    """

    close_out_sql: str
    close_out_columns: tuple[str, ...]
    insert_sql: str
    insert_input_columns: tuple[str, ...]


def _extract_referenced_columns(sql_expr: str) -> set[str]:
    """Bare column names referenced anywhere in ``sql_expr``."""
    try:
        tree = sqlglot.parse_one(sql_expr, dialect="postgres")
    except sqlglot.errors.ParseError:
        return set()
    return {col.name for col in tree.find_all(exp.Column) if not col.table}


def _sql_literal(s: str) -> str:
    """Single-quoted SQL string literal with embedded quotes escaped."""
    return "'" + s.replace("'", "''") + "'"


def emit_mapped_binding_write(
    spec: Spec,
    binding: SourceBinding,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
) -> MappedBindingWrite:
    """Return SCD2 write machinery that applies ``binding.mappings``
    server-side via ``INSERT … SELECT``.

    Slots without a mapping default to ``NULL`` in the inserted binding
    row (a source making a partial claim says nothing about those slots).
    Slots with a mapping project through the mapping's SQL expression;
    the host provides the raw source-field values that the expression
    references.
    """
    cls = binding.class_
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; only concrete classes "
            f"have bindings tables"
        )
    ident = cls.identifier_slot()
    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"
    source_literal = _sql_literal(binding.source.name)

    # 1. Discover raw fields referenced by any mapping expression
    raw_fields: set[str] = set()
    for expr in binding.mappings.values():
        raw_fields |= _extract_referenced_columns(expr)

    # 2. Always-required raw inputs, then mapping-referenced fields
    input_columns: list[str] = [ident.name, "source_identifier"]
    for f in sorted(raw_fields):
        if f not in input_columns:
            input_columns.append(f)

    # 3. The `raw` subquery aliases each parameter as the matching column
    raw_lines = ",\n".join(
        f"        %({c})s AS {c}" for c in input_columns
    )
    raw_subquery = (
        "FROM (\n"
        "    SELECT\n"
        f"{raw_lines}\n"
        ") AS raw"
    )

    # 4. Outer SELECT: literal source_name, raw.source_identifier,
    #    raw.<identifier>, then per-slot mapping or NULL
    select_lines: list[str] = [
        f"    {source_literal}",
        "    raw.source_identifier",
    ]
    insert_columns: list[str] = ["source_name", "source_identifier"]
    for slot in cls.effective_slots():
        insert_columns.append(slot.name)
        if slot.name == ident.name:
            select_lines.append(f"    raw.{ident.name}")
        elif slot.name in binding.mappings:
            select_lines.append(f"    {binding.mappings[slot.name]}")
        else:
            select_lines.append("    NULL")

    columns_csv = ", ".join(insert_columns)
    insert_sql = (
        f"INSERT INTO {bindings_table} ({columns_csv})\n"
        "SELECT\n"
        + ",\n".join(select_lines)
        + "\n"
        + raw_subquery
        + ";"
    )

    # 5. Close-out filters by literal source_name; host only passes
    #    identifier + source_identifier.
    close_out_columns: tuple[str, ...] = (ident.name, "source_identifier")
    close_out_sql = (
        f"UPDATE {bindings_table}\n"
        f"SET valid_to = now()\n"
        f"WHERE {ident.name} = %({ident.name})s\n"
        f"  AND source_name = {source_literal}\n"
        f"  AND source_identifier = %(source_identifier)s\n"
        f"  AND valid_to IS NULL;"
    )

    return MappedBindingWrite(
        close_out_sql=close_out_sql,
        close_out_columns=close_out_columns,
        insert_sql=insert_sql,
        insert_input_columns=tuple(input_columns),
    )


__all__ = [
    "BindingWrite",
    "emit_binding_write",
    "MappedBindingWrite",
    "emit_mapped_binding_write",
]
