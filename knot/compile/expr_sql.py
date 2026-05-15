"""Postgres SQL compilation for the ``Expr`` tree.

Single-dispatch over the node type — one ``@compile_sql.register``
per node. Adding a second compilation target (cypher, in-process
evaluator, typed IR) is a new dispatch table in a sibling module; the
``Expr`` dataclasses don't need to change.

``target_suffix`` flips the rendered table reference between the
canonical table (``""``), the resolved view (``"_resolved"``), or the
bindings-current view (``"_bindings_current"``) — the same Expr tree
compiles against any of them.

``outer_class`` carries the enclosing query's class name through
recursive compilation so that ``This`` references can render against
the outer row. Defaults to ``None`` (top-level compilation); set by
``Aggregate`` for its sub-predicate and by ``compile_query`` for the
top-level WHERE clause of a query that contains aggregates.
"""

from __future__ import annotations

from functools import singledispatch

from knot.expr import (
    Aggregate,
    Between,
    BoolOp,
    Compare,
    CountRel,
    Exists,
    Expr,
    FkChainRef,
    FkRef,
    InList,
    IsNull,
    Literal,
    Not,
    Raw,
    Ref,
    This,
)


@singledispatch
def compile_sql(node, *, schema, target_suffix, outer_class=None):
    """Render ``node`` as a postgres SQL fragment."""
    raise NotImplementedError(f"no SQL compiler registered for {type(node).__name__}")


@compile_sql.register(Ref)
def _(node, *, schema, target_suffix, outer_class=None):
    return f"{schema}.{node.class_name.lower()}{target_suffix}.{node.slot_name}"


@compile_sql.register(FkRef)
def _(node, *, schema, target_suffix, outer_class=None):
    return f"{schema}.{node.class_name.lower()}{target_suffix}.{node.slot_name}"


@compile_sql.register(FkChainRef)
def _(node, *, schema, target_suffix, outer_class=None):
    target_class = node.chain[-1][1]
    return f"{schema}.{target_class.lower()}{target_suffix}.{node.terminal_slot}"


@compile_sql.register(Literal)
def _(node, *, schema, target_suffix, outer_class=None):
    return _sql_literal(node.value)


@compile_sql.register(This)
def _(node, *, schema, target_suffix, outer_class=None):
    if outer_class is None:
        raise ValueError(f"this.{node.class_name} used outside of an Aggregate context")
    if node.class_name != outer_class:
        raise ValueError(
            f"this.{node.class_name} doesn't match the enclosing class "
            f"({outer_class!r}) — outer-scope reference mismatched"
        )
    # The outer row's identity column. knot convention: canonical_id.
    return f"{schema}.{outer_class.lower()}{target_suffix}.canonical_id"


@compile_sql.register(Compare)
def _(node, *, schema, target_suffix, outer_class=None):
    lhs = compile_sql(
        node.left, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    rhs = compile_sql(
        node.right, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    return f"{lhs} {node.op} {rhs}"


@compile_sql.register(BoolOp)
def _(node, *, schema, target_suffix, outer_class=None):
    lhs = compile_sql(
        node.left, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    rhs = compile_sql(
        node.right, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    return f"({lhs}) {node.op} ({rhs})"


@compile_sql.register(Not)
def _(node, *, schema, target_suffix, outer_class=None):
    inner = compile_sql(
        node.expr, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    return f"NOT ({inner})"


@compile_sql.register(IsNull)
def _(node, *, schema, target_suffix, outer_class=None):
    op = "IS NOT NULL" if node.negated else "IS NULL"
    inner = compile_sql(
        node.expr, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    return f"{inner} {op}"


@compile_sql.register(InList)
def _(node, *, schema, target_suffix, outer_class=None):
    lhs = compile_sql(
        node.left, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    vs = ", ".join(_sql_literal(v) for v in node.values)
    op = "NOT IN" if node.negated else "IN"
    return f"{lhs} {op} ({vs})"


@compile_sql.register(Between)
def _(node, *, schema, target_suffix, outer_class=None):
    lhs = compile_sql(
        node.left, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    return f"{lhs} BETWEEN {_sql_literal(node.low)} AND {_sql_literal(node.high)}"


@compile_sql.register(Exists)
def _(node, *, schema, target_suffix, outer_class=None):
    other_table = f"{schema}.{node.other_class_name.lower()}{target_suffix}"
    primary_table = f"{schema}.{node.primary_class_name.lower()}{target_suffix}"
    clauses = [f"{other_table}.{node.fk_slot_name} = {primary_table}.{node.primary_identifier}"]
    if node.where is not None:
        clauses.append(
            compile_sql(
                node.where, schema=schema, target_suffix=target_suffix, outer_class=outer_class
            )
        )
    prefix = "NOT EXISTS" if node.negated else "EXISTS"
    return f"{prefix} (SELECT 1 FROM {other_table} WHERE {' AND '.join(clauses)})"


@compile_sql.register(CountRel)
def _(node, *, schema, target_suffix, outer_class=None):
    other_table = f"{schema}.{node.other_class_name.lower()}{target_suffix}"
    primary_table = f"{schema}.{node.primary_class_name.lower()}{target_suffix}"
    clauses = [f"{other_table}.{node.fk_slot_name} = {primary_table}.{node.primary_identifier}"]
    if node.where is not None:
        clauses.append(
            compile_sql(
                node.where, schema=schema, target_suffix=target_suffix, outer_class=outer_class
            )
        )
    return f"(SELECT COUNT(*) FROM {other_table} WHERE {' AND '.join(clauses)})"


@compile_sql.register(Aggregate)
def _(node, *, schema, target_suffix, outer_class=None):
    # Infer the primary class — the class whose slot refs appear in the
    # predicate (ignoring This refs, which point to outer scope).
    primary = _infer_primary_class(node.predicate)
    if primary is None:
        raise ValueError(
            f"Aggregate.predicate has no class-bound slot refs; cannot "
            f"infer the primary row-set. Predicate: {node.predicate!r}"
        )
    sub_table = f"{schema}.{primary.lower()}{target_suffix}"
    # The aggregate's sub-predicate compiles with outer_class unchanged
    # (it propagates the enclosing scope, since This refs in the predicate
    # bind to the enclosing query, not the subquery itself).
    pred_sql = compile_sql(
        node.predicate, schema=schema, target_suffix=target_suffix, outer_class=outer_class
    )
    if node.kind == "any":
        return f"EXISTS (SELECT 1 FROM {sub_table} WHERE {pred_sql})"
    if node.kind == "none":
        return f"NOT EXISTS (SELECT 1 FROM {sub_table} WHERE {pred_sql})"
    if node.kind == "count":
        return f"(SELECT COUNT(*) FROM {sub_table} WHERE {pred_sql})"
    if node.kind == "all":
        # Universal as "no counter-example": NOT EXISTS (… AND NOT cond).
        cond_sql = compile_sql(
            node.condition, schema=schema, target_suffix=target_suffix, outer_class=outer_class
        )
        return f"NOT EXISTS (SELECT 1 FROM {sub_table} WHERE {pred_sql} AND NOT ({cond_sql}))"
    raise ValueError(f"unknown Aggregate.kind: {node.kind}")


@compile_sql.register(Raw)
def _(node, *, schema, target_suffix, outer_class=None):
    return node.sql


def _infer_primary_class(node):
    """Walk ``node`` and return the class name of the first non-``This``
    slot reference encountered. Used by ``Aggregate`` to pick its
    sub-query's FROM table.

    Returns ``None`` if no class-bound ref is reachable (an aggregate
    over only ``this``/literals — nonsensical, but caller surfaces a
    clear error)."""
    if isinstance(node, This):
        return None
    if isinstance(node, (Ref, FkRef)):
        return node.class_name
    if isinstance(node, FkChainRef):
        return node.source_class
    if isinstance(node, (Compare, BoolOp)):
        return _infer_primary_class(node.left) or _infer_primary_class(node.right)
    if isinstance(node, Not):
        return _infer_primary_class(node.expr)
    if isinstance(node, IsNull):
        return _infer_primary_class(node.expr)
    if isinstance(node, (InList, Between)):
        return _infer_primary_class(node.left)
    if isinstance(node, Aggregate):
        return _infer_primary_class(node.predicate)
    return None


def _sql_literal(v):
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
