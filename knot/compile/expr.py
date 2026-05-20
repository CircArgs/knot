"""Postgres SQL compilation for the ``Expr`` tree.

Single-dispatch over the node type — one ``@compile_sql.register``
per node. Adding a second compilation target (cypher, in-process
evaluator, typed IR) is a new dispatch table in a sibling module; the
``Expr`` dataclasses don't need to change.

``layer`` (a ``Layer`` enum, ``StrEnum``-typed so the value is also
the literal SQL table-name suffix) flips the rendered table reference
between the canonical table, the resolved view, the bindings table,
and the per-source provenance view — the same Expr tree compiles
against any of them.

``outer_class`` carries the enclosing query's class name through
recursive compilation so that ``This`` references can render against
the outer row. Defaults to ``None`` (top-level compilation); set by
``Aggregate`` for its sub-predicate and by ``compile_query`` for the
top-level WHERE clause of a query that contains aggregates.
"""

from __future__ import annotations

from functools import singledispatch
from typing import Any

from knot.ast.expr import (
    AggExpr,
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
    TupleCompare,
    VectorDistance,
    VectorRef,
)
from knot.ast.select import Layer


@singledispatch
def compile_sql(
    node: Expr,
    *,
    schema: str,
    layer: Layer,
    outer_class: str | None = None,
) -> str:
    """Render ``node`` as a postgres SQL fragment."""
    raise NotImplementedError(f"no SQL compiler registered for {type(node).__name__}")


@compile_sql.register
def _(node: Ref, *, schema: str, layer: Layer, outer_class: str | None = None) -> str:
    return f"{schema}.{node.class_name.lower()}{layer}.{node.slot_name}"


@compile_sql.register
def _(node: FkRef, *, schema: str, layer: Layer, outer_class: str | None = None) -> str:
    return f"{schema}.{node.class_name.lower()}{layer}.{node.slot_name}"


@compile_sql.register
def _(
    node: VectorRef, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    return f"{schema}.{node.class_name.lower()}{layer}.{node.slot_name}"


# pgvector distance operators, one per metric. Each matches the
# operator class used by knot's HNSW index emission, so an ``ORDER BY
# slot <op> target LIMIT k`` plan uses the index.
_VECTOR_DISTANCE_OP = {
    "cosine": "<=>",
    "l2": "<->",
    "ip": "<#>",
}


@compile_sql.register
def _(
    node: VectorDistance, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    op = _VECTOR_DISTANCE_OP[node.metric]
    col = f"{schema}.{node.class_name.lower()}{layer}.{node.slot_name}"
    if isinstance(node.target, VectorRef):
        # Cross-row distance: both sides are typed columns — no cast needed.
        other = compile_sql(
            node.target, schema=schema, layer=layer, outer_class=outer_class
        )
        return f"({col} {op} {other})"
    # Literal vector — pgvector's text form: '[0.1, 0.2, ...]'.
    # Query.sql() inlines all literals — there's no parameter list.
    literal = "[" + ", ".join(repr(float(v)) for v in node.target) + "]"
    return f"({col} {op} '{literal}'::vector({node.dim}))"


_AGG_FN = {
    "count": "COUNT",
    "sum": "SUM",
    "avg": "AVG",
    "min": "MIN",
    "max": "MAX",
}


@compile_sql.register
def _(
    node: AggExpr, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    fn = _AGG_FN[node.kind]
    if node.expr is None:
        # Only valid for COUNT — AggExpr.__post_init__ already enforced.
        return f"{fn}(*)"
    inner = compile_sql(node.expr, schema=schema, layer=layer, outer_class=outer_class)
    return f"{fn}({inner})"


@compile_sql.register
def _(
    node: FkChainRef, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    target_class = node.chain[-1][1]
    return f"{schema}.{target_class.lower()}{layer}.{node.terminal_slot}"


@compile_sql.register
def _(
    node: Literal, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    return _sql_literal(node.value)


@compile_sql.register
def _(node: This, *, schema: str, layer: Layer, outer_class: str | None = None) -> str:
    if outer_class is None:
        raise ValueError(f"this.{node.class_name} used outside of an Aggregate context")
    if node.class_name != outer_class:
        raise ValueError(
            f"this.{node.class_name} doesn't match the enclosing class "
            f"({outer_class!r}) — outer-scope reference mismatched"
        )
    # The outer row's identity column. knot convention: canonical_id.
    return f"{schema}.{outer_class.lower()}{layer}.canonical_id"


@compile_sql.register
def _(
    node: Compare, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    lhs = compile_sql(node.left, schema=schema, layer=layer, outer_class=outer_class)
    rhs = compile_sql(node.right, schema=schema, layer=layer, outer_class=outer_class)
    return f"{lhs} {node.op} {rhs}"


@compile_sql.register
def _(
    node: BoolOp, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    lhs = compile_sql(node.left, schema=schema, layer=layer, outer_class=outer_class)
    rhs = compile_sql(node.right, schema=schema, layer=layer, outer_class=outer_class)
    return f"({lhs}) {node.op} ({rhs})"


@compile_sql.register
def _(node: Not, *, schema: str, layer: Layer, outer_class: str | None = None) -> str:
    return f"NOT ({compile_sql(node.expr, schema=schema, layer=layer, outer_class=outer_class)})"


@compile_sql.register
def _(
    node: IsNull, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    op = "IS NOT NULL" if node.negated else "IS NULL"
    return f"{compile_sql(node.expr, schema=schema, layer=layer, outer_class=outer_class)} {op}"


@compile_sql.register
def _(
    node: InList, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    lhs = compile_sql(node.left, schema=schema, layer=layer, outer_class=outer_class)
    vs = ", ".join(_sql_literal(v) for v in node.values)
    op = "NOT IN" if node.negated else "IN"
    return f"{lhs} {op} ({vs})"


@compile_sql.register
def _(
    node: Between, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    lhs = compile_sql(node.left, schema=schema, layer=layer, outer_class=outer_class)
    return f"{lhs} BETWEEN {_sql_literal(node.low)} AND {_sql_literal(node.high)}"


@compile_sql.register
def _(
    node: Exists, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    other_table = f"{schema}.{node.other_class_name.lower()}{layer}"
    primary_table = f"{schema}.{node.primary_class_name.lower()}{layer}"
    clauses = [
        f"{other_table}.{node.fk_slot_name} = {primary_table}.{node.primary_identifier}"
    ]
    if node.where is not None:
        clauses.append(
            compile_sql(
                node.where,
                schema=schema,
                layer=layer,
                outer_class=outer_class,
            )
        )
    prefix = "NOT EXISTS" if node.negated else "EXISTS"
    return f"{prefix} (SELECT 1 FROM {other_table} WHERE {' AND '.join(clauses)})"


@compile_sql.register
def _(
    node: CountRel, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    other_table = f"{schema}.{node.other_class_name.lower()}{layer}"
    primary_table = f"{schema}.{node.primary_class_name.lower()}{layer}"
    clauses = [
        f"{other_table}.{node.fk_slot_name} = {primary_table}.{node.primary_identifier}"
    ]
    if node.where is not None:
        clauses.append(
            compile_sql(
                node.where,
                schema=schema,
                layer=layer,
                outer_class=outer_class,
            )
        )
    return f"(SELECT COUNT(*) FROM {other_table} WHERE {' AND '.join(clauses)})"


@compile_sql.register
def _(
    node: Aggregate, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    # Infer the primary class — the class whose slot refs appear in the
    # predicate (ignoring This refs, which point to outer scope).
    primary = _infer_primary_class(node.predicate)
    if primary is None:
        raise ValueError(
            f"Aggregate.predicate has no class-bound slot refs; cannot "
            f"infer the primary row-set. Predicate: {node.predicate!r}"
        )
    sub_table = f"{schema}.{primary.lower()}{layer}"
    # The aggregate's sub-predicate compiles with outer_class unchanged
    # (it propagates the enclosing scope, since This refs in the predicate
    # bind to the enclosing query, not the subquery itself).
    pred_sql = compile_sql(
        node.predicate,
        schema=schema,
        layer=layer,
        outer_class=outer_class,
    )
    if node.kind == "any":
        return f"EXISTS (SELECT 1 FROM {sub_table} WHERE {pred_sql})"
    if node.kind == "none":
        return f"NOT EXISTS (SELECT 1 FROM {sub_table} WHERE {pred_sql})"
    if node.kind == "count":
        return f"(SELECT COUNT(*) FROM {sub_table} WHERE {pred_sql})"
    if node.kind == "all":
        # Universal as "no counter-example": NOT EXISTS (… AND NOT cond).
        # ``condition`` is required for kind="all" (Aggregate.__post_init__
        # rejects construction otherwise) — assert narrows the type.
        assert node.condition is not None
        cond_sql = compile_sql(
            node.condition,
            schema=schema,
            layer=layer,
            outer_class=outer_class,
        )
        return f"NOT EXISTS (SELECT 1 FROM {sub_table} WHERE {pred_sql} AND NOT ({cond_sql}))"
    raise ValueError(f"unknown Aggregate.kind: {node.kind}")


@compile_sql.register
def _(
    node: TupleCompare, *, schema: str, layer: Layer, outer_class: str | None = None
) -> str:
    lhs = (
        "("
        + ", ".join(
            compile_sql(e, schema=schema, layer=layer, outer_class=outer_class)
            for e in node.lefts
        )
        + ")"
    )
    rhs = (
        "("
        + ", ".join(
            compile_sql(e, schema=schema, layer=layer, outer_class=outer_class)
            for e in node.rights
        )
        + ")"
    )
    return f"{lhs} {node.op} {rhs}"


@compile_sql.register
def _(node: Raw, *, schema: str, layer: Layer, outer_class: str | None = None) -> str:
    return node.sql


def _infer_primary_class(node: Expr) -> str | None:
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
        # Nested aggregate — its own primary class is what its inner
        # predicate resolves to; but for inference at this level we
        # peek into its predicate (the aggregate value-expr lives in
        # an outer context).
        return _infer_primary_class(node.predicate)
    return None


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
