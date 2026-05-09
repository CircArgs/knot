"""SQL compiler for knot's expression tree.

Public API
----------
compile_predicate(node, ctx) -> sql.Composable
    Compile one expression-tree node.  Side-effects: pushes positional params
    onto ``ctx.params``.  Read ctx.params after the call for the full list.

compile_value(node, ctx) -> sql.Composable
    Alias for compile_predicate; clarifies that the result is a value
    expression (scalar or array), not a boolean predicate.  Used when
    compiling Slot.derivation nodes (RelationProject / RelationCount /
    RelationAggregate) as computed columns in a SELECT.

compile_constraint(constraint, cls) -> (sql.Composable, list[Any])
    Emit a SELECT returning offending rows in the uniform violation shape:
        (rule_id, class_name, slot_name, offending_pk, detail)

CompileContext
    Mutable dataclass threaded through a compilation pass.

CompilerError
    Raised on invalid or unsupported expression-tree nodes.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

# Register all handlers by importing the handler modules.
import knot.spec.compile.postgres._predicate  # noqa: F401
import knot.spec.compile.postgres._relation  # noqa: F401
from ._naming import bindings_table_id, table_id
from knot.spec.compile.postgres._context import CompileContext
from knot.spec.compile.postgres._dispatch import CompilerError, compile_predicate
from knot.spec.metaschema import Constraint, OntologyClass


def compile_order_by(
    order_terms: list[tuple[str, str]],
    alias: str = "s",
) -> sql.Composable:
    """Compile a list of (field_name, direction) pairs into a SQL ORDER BY
    fragment (without the ORDER BY keyword).

    ``field_name`` must be a stored slot name or a system column
    (e.g. ``_canonical_id``).  ``direction`` must be ``'ASC'`` or ``'DESC'``
    (case-insensitive); any other value raises CompilerError.

    The stable secondary sort (``b.canonical_id ASC, s._source ASC``) is
    appended by ``graph_store.query_rows`` — this function returns only the
    caller-specified terms.
    """
    if not order_terms:
        raise CompilerError("compile_order_by called with empty order_terms.")
    parts: list[sql.Composable] = []
    for field_name, direction in order_terms:
        dir_upper = direction.upper()
        if dir_upper not in ("ASC", "DESC"):
            raise CompilerError(f"Invalid ORDER BY direction {direction!r}; expected ASC or DESC.")
        parts.append(
            sql.SQL("{alias}.{col} {dir}").format(
                alias=sql.Identifier(alias),
                col=sql.Identifier(field_name),
                dir=sql.SQL(dir_upper),
            )
        )
    return sql.SQL(", ").join(parts)


def compile_constraint(
    constraint: Constraint,
    cls: OntologyClass,
) -> tuple[sql.Composable, list[Any]]:
    """Emit a SELECT returning offending rows in the uniform violation shape.

    Generated SQL (parameterized):
        SELECT
            %s AS rule_id,
            %s AS class_name,
            NULL::text AS slot_name,
            s._canonical_id AS offending_pk,
            row_to_json(s)::text AS detail
        FROM knot_data.<class> s
        JOIN knot_data.<class>_bindings b
            ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL
        WHERE NOT ( <compiled body> )

    Returns ``(composable, params)`` where params is a flat list of positional
    values ready to pass to ``conn.execute(composable, params)``.

    The compiled body is evaluated against the source-row alias ``s``.
    """
    ctx = CompileContext(primary_class=cls, alias="s")
    body_sql = compile_predicate(constraint.body, ctx)

    # Prepend the two scalar params (rule_id, class_name) that appear before
    # any body params in the SELECT list.
    params: list[Any] = [constraint.name, cls.name] + ctx.params

    stmt = sql.SQL(
        "SELECT"
        " %s AS rule_id,"
        " %s AS class_name,"
        " NULL::text AS slot_name,"
        " b.canonical_id AS offending_pk,"
        " row_to_json(s)::text AS detail"
        " FROM {src_table} s"
        " JOIN {bind_table} b"
        "   ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
        " WHERE NOT ({body})"
    ).format(
        src_table=table_id(cls),
        bind_table=bindings_table_id(cls),
        body=body_sql,
    )

    return stmt, params


# compile_value is an alias for compile_predicate in value-expression contexts.
# The same dispatch works for both boolean predicates and scalar/array values.
compile_value = compile_predicate


__all__ = [
    "CompileContext",
    "CompilerError",
    "compile_predicate",
    "compile_value",
    "compile_constraint",
    "compile_order_by",
]
