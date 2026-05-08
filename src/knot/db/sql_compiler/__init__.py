"""SQL compiler for knot's expression tree.

Public API
----------
compile_predicate(node, ctx) -> sql.Composable
    Compile one expression-tree node.  Side-effects: pushes positional params
    onto ``ctx.params``.  Read ctx.params after the call for the full list.

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

from knot.db._naming import bindings_table_id, table_id
from knot.db.sql_compiler._context import CompileContext
from knot.db.sql_compiler._dispatch import CompilerError, compile_predicate
from knot.ontology.metaschema import Constraint, OntologyClass

# Register all handlers by importing the handler modules.
import knot.db.sql_compiler._predicate  # noqa: F401
import knot.db.sql_compiler._relation   # noqa: F401


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


__all__ = [
    "CompileContext",
    "CompilerError",
    "compile_predicate",
    "compile_constraint",
]
