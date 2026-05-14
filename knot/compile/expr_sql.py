"""Postgres SQL compilation for the ``Expr`` tree.

Single-dispatch over the node type — one ``@compile_sql.register``
per node. Adding a second compilation target (cypher, in-process
evaluator, typed IR) is a new dispatch table in a sibling module; the
``Expr`` dataclasses don't need to change.

``target_suffix`` flips the rendered table reference between the
canonical table (``""``), the resolved view (``"_resolved"``), or the
bindings-current view (``"_bindings_current"``) — the same Expr tree
compiles against any of them.
"""

from __future__ import annotations

from functools import singledispatch
from typing import Any

from knot.expr import (
    Between,
    BoolOp,
    Compare,
    CountRel,
    Exists,
    Expr,
    InList,
    IsNull,
    Literal,
    Not,
    Raw,
    Ref,
)


@singledispatch
def compile_sql(node: Expr, *, schema: str, target_suffix: str) -> str:
    """Render ``node`` as a postgres SQL fragment."""
    raise NotImplementedError(f"no SQL compiler registered for {type(node).__name__}")


@compile_sql.register
def _(node: Ref, *, schema: str, target_suffix: str) -> str:
    return f"{schema}.{node.class_name.lower()}{target_suffix}.{node.slot_name}"


@compile_sql.register
def _(node: Literal, *, schema: str, target_suffix: str) -> str:
    return _sql_literal(node.value)


@compile_sql.register
def _(node: Compare, *, schema: str, target_suffix: str) -> str:
    lhs = compile_sql(node.left, schema=schema, target_suffix=target_suffix)
    rhs = compile_sql(node.right, schema=schema, target_suffix=target_suffix)
    return f"{lhs} {node.op} {rhs}"


@compile_sql.register
def _(node: BoolOp, *, schema: str, target_suffix: str) -> str:
    lhs = compile_sql(node.left, schema=schema, target_suffix=target_suffix)
    rhs = compile_sql(node.right, schema=schema, target_suffix=target_suffix)
    return f"({lhs}) {node.op} ({rhs})"


@compile_sql.register
def _(node: Not, *, schema: str, target_suffix: str) -> str:
    return f"NOT ({compile_sql(node.expr, schema=schema, target_suffix=target_suffix)})"


@compile_sql.register
def _(node: IsNull, *, schema: str, target_suffix: str) -> str:
    op = "IS NOT NULL" if node.negated else "IS NULL"
    return f"{compile_sql(node.expr, schema=schema, target_suffix=target_suffix)} {op}"


@compile_sql.register
def _(node: InList, *, schema: str, target_suffix: str) -> str:
    lhs = compile_sql(node.left, schema=schema, target_suffix=target_suffix)
    vs = ", ".join(_sql_literal(v) for v in node.values)
    op = "NOT IN" if node.negated else "IN"
    return f"{lhs} {op} ({vs})"


@compile_sql.register
def _(node: Between, *, schema: str, target_suffix: str) -> str:
    lhs = compile_sql(node.left, schema=schema, target_suffix=target_suffix)
    return f"{lhs} BETWEEN {_sql_literal(node.low)} AND {_sql_literal(node.high)}"


@compile_sql.register
def _(node: Exists, *, schema: str, target_suffix: str) -> str:
    other_table = f"{schema}.{node.other_class_name.lower()}{target_suffix}"
    primary_table = f"{schema}.{node.primary_class_name.lower()}{target_suffix}"
    clauses = [f"{other_table}.{node.fk_slot_name} = {primary_table}.{node.primary_identifier}"]
    if node.where is not None:
        clauses.append(compile_sql(node.where, schema=schema, target_suffix=target_suffix))
    prefix = "NOT EXISTS" if node.negated else "EXISTS"
    return f"{prefix} (SELECT 1 FROM {other_table} WHERE {' AND '.join(clauses)})"


@compile_sql.register
def _(node: CountRel, *, schema: str, target_suffix: str) -> str:
    other_table = f"{schema}.{node.other_class_name.lower()}{target_suffix}"
    primary_table = f"{schema}.{node.primary_class_name.lower()}{target_suffix}"
    clauses = [f"{other_table}.{node.fk_slot_name} = {primary_table}.{node.primary_identifier}"]
    if node.where is not None:
        clauses.append(compile_sql(node.where, schema=schema, target_suffix=target_suffix))
    return f"(SELECT COUNT(*) FROM {other_table} WHERE {' AND '.join(clauses)})"


@compile_sql.register
def _(node: Raw, *, schema: str, target_suffix: str) -> str:
    return node.sql


def _sql_literal(v: Any) -> str:
    """Postgres SQL literal serialization for primitive values."""
    match v:
        case None:
            return "NULL"
        case True:
            return "TRUE"
        case False:
            return "FALSE"
        case str():
            return "'" + v.replace("'", "''") + "'"
        case int() | float():
            return str(v)
        case list() | tuple():
            return "ARRAY[" + ", ".join(_sql_literal(x) for x in v) + "]"
        case _:
            raise TypeError(f"can't serialize {type(v).__name__} as SQL literal: {v!r}")


__all__ = ["compile_sql"]
