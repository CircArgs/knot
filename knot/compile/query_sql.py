"""Postgres SQL compilation for the read substrate.

Sibling dispatch table to ``compile_sql`` — that one renders Expr
fragments (string only); this one renders Query statements and
returns ``(sql, params)``. ``params`` is empty for now because
literals are inlined via ``compile_sql``; parameterized output can
layer on without changing the signature.

Adding a second SQL dialect (Trino / Spark) is a new module
(``query_sql_trino.py``) with its own dispatch table — same
open/closed flip as ``compile_sql``.
"""

from __future__ import annotations

from functools import singledispatch
from typing import Any

from knot.compile.expr_sql import compile_sql
from knot.select import Query
from knot.spec import Spec


@singledispatch
def compile_query(node, *, spec: Spec, schema: str) -> tuple[str, list[Any]]:
    """Render ``node`` as a full postgres SQL statement + parameter list."""
    raise NotImplementedError(f"no query compiler registered for {type(node).__name__}")


@compile_query.register
def _(node: Query, *, spec: Spec, schema: str) -> tuple[str, list[Any]]:
    suffix = node.target_suffix
    table = f"{schema}.{node.class_name.lower()}{suffix}"

    # Projection — None means SELECT *.
    if node.projection is None:
        select_sql = "*"
    else:
        select_sql = ", ".join(
            compile_sql(r, schema=schema, target_suffix=suffix) for r in node.projection
        )

    parts = [f"SELECT {select_sql}", f"FROM {table}"]

    if node.where_clause is not None:
        where_sql = compile_sql(node.where_clause, schema=schema, target_suffix=suffix)
        parts.append(f"WHERE {where_sql}")

    if node.ordering:
        order_parts = []
        for ob in node.ordering:
            ref_sql = compile_sql(ob.ref, schema=schema, target_suffix=suffix)
            order_parts.append(f"{ref_sql} {ob.direction.upper()}")
        parts.append("ORDER BY " + ", ".join(order_parts))

    if node.limit_value is not None:
        parts.append(f"LIMIT {node.limit_value}")

    if node.offset_value is not None:
        parts.append(f"OFFSET {node.offset_value}")

    return "\n".join(parts) + ";", []


__all__ = ["compile_query"]
