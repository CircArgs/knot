"""Postgres SQL compilation for the read substrate.

Sibling dispatch table to ``compile_sql`` — that one renders Expr
fragments, this one renders Query statements. Both return raw SQL
strings; literal values are inlined at compile time so there is no
positional parameter list to thread through to the driver.

JOIN assembly: a pre-pass walks the query AST (where / order_by /
projection) collecting every ``FkChainRef``. Each unique
``(source_class, fk_slot, target_class)`` step becomes one JOIN.
Aliasing is currently coarse — one JOIN per target class, assuming a
single FK chain per target per query. Multi-hop and same-target-twice
need richer aliasing; deferred until a real query requires it.

Adding a second SQL dialect (Trino / Spark) is a new module
(``query_sql_trino.py``) with its own dispatch table — same
open/closed flip as ``compile_sql``.
"""

from __future__ import annotations

from functools import singledispatch
from typing import Any

from knot.ast.expr import (
    Aggregate,
    Between,
    BoolOp,
    Compare,
    CountRel,
    Exists,
    Expr,
    FkChainRef,
    InList,
    IsNull,
    Not,
)
from knot.ast.select import Query
from knot.compile.expr import compile_sql
from knot.spec import Spec


@singledispatch
def compile_query(node: Any, *, spec: Spec, schema: str) -> str:
    """Render ``node`` as a full postgres SQL statement."""
    raise NotImplementedError(f"no query compiler registered for {type(node).__name__}")


@compile_query.register
def _(node: Query, *, spec: Spec, schema: str) -> str:
    layer = node.layer
    table = f"{schema}.{node.class_name.lower()}{layer}"

    # Projection — None means SELECT *.
    if node.projection is None:
        select_sql = "*"
    else:
        select_sql = ", ".join(
            compile_sql(r, schema=schema, layer=layer, outer_class=node.class_name)
            for r in node.projection
        )

    # Collect FK chains from everywhere a Ref could appear.
    chains: list[FkChainRef] = []
    if node.where_clause is not None:
        _collect_chains(node.where_clause, chains)
    for ob in node.ordering:
        _collect_chains(ob.ref, chains)
    if node.projection is not None:
        for r in node.projection:
            _collect_chains(r, chains)

    # Build JOIN clauses. Each step gets one JOIN, deduplicated by the
    # ``(source_class, fk_slot, target_class)`` triple.
    seen: set[tuple[str, str, str]] = set()
    joins: list[str] = []
    for chain_ref in chains:
        source_class = chain_ref.source_class
        for fk_slot, target_class in chain_ref.chain:
            key = (source_class, fk_slot, target_class)
            if key not in seen:
                seen.add(key)
                target_cls = _lookup_class(spec, target_class)
                target_ident = target_cls.identifier_slot().name
                joins.append(
                    f"JOIN {schema}.{target_class.lower()}{layer} "
                    f"ON {schema}.{target_class.lower()}{layer}.{target_ident} "
                    f"= {schema}.{source_class.lower()}{layer}.{fk_slot}"
                )
            source_class = target_class

    parts = [f"SELECT {select_sql}", f"FROM {table}"]
    parts.extend(joins)

    if node.where_clause is not None:
        # outer_class is the query's primary class so Aggregate
        # sub-predicates can resolve their ``this.X`` refs.
        where_sql = compile_sql(
            node.where_clause,
            schema=schema,
            layer=layer,
            outer_class=node.class_name,
        )
        parts.append(f"WHERE {where_sql}")

    if node.grouping:
        group_parts = [
            compile_sql(
                g, schema=schema, layer=layer, outer_class=node.class_name
            )
            for g in node.grouping
        ]
        parts.append("GROUP BY " + ", ".join(group_parts))

    if node.ordering:
        order_parts = []
        for ob in node.ordering:
            ref_sql = compile_sql(
                ob.ref,
                schema=schema,
                layer=layer,
                outer_class=node.class_name,
            )
            order_parts.append(f"{ref_sql} {ob.direction.upper()}")
        parts.append("ORDER BY " + ", ".join(order_parts))

    if node.limit_value is not None:
        parts.append(f"LIMIT {node.limit_value}")

    if node.offset_value is not None:
        parts.append(f"OFFSET {node.offset_value}")

    if node.lock_mode is not None:
        # Postgres row-lock clauses sit at the tail. SKIP LOCKED is
        # the load-bearing one for ER worker scaling — claim a batch
        # of unresolved bindings without serializing on other workers.
        _LOCK_SQL = {
            "for_update": "FOR UPDATE",
            "for_update_skip_locked": "FOR UPDATE SKIP LOCKED",
            "for_share": "FOR SHARE",
        }
        parts.append(_LOCK_SQL[node.lock_mode])

    return "\n".join(parts) + ";"


def _lookup_class(spec: Spec, name: str) -> Any:
    """Resolve a class name in ``spec``. Raises ``KeyError`` if missing."""
    try:
        return spec.classes[name]
    except KeyError:
        raise KeyError(f"class {name!r} not found in spec") from None


def _collect_chains(node: Expr, out: list[FkChainRef]) -> None:
    """Walk an Expr tree collecting every ``FkChainRef`` reached.
    Deduplication happens at JOIN-emit time on the (source, fk, target)
    triple — not on the FkChainRef itself, so multiple chains that
    share a prefix still produce one JOIN per shared step."""
    if isinstance(node, FkChainRef):
        out.append(node)
        return
    if isinstance(node, (Compare, BoolOp)):
        _collect_chains(node.left, out)
        _collect_chains(node.right, out)
        return
    if isinstance(node, Not):
        _collect_chains(node.expr, out)
        return
    if isinstance(node, IsNull):
        _collect_chains(node.expr, out)
        return
    if isinstance(node, InList):
        _collect_chains(node.left, out)
        return
    if isinstance(node, Between):
        _collect_chains(node.left, out)
        return
    if isinstance(node, Exists):
        if node.where is not None:
            _collect_chains(node.where, out)
        return
    if isinstance(node, CountRel):
        if node.where is not None:
            _collect_chains(node.where, out)
        return
    if isinstance(node, Aggregate):
        # FK chains inside an Aggregate predicate are scoped to the
        # sub-query, not the outer FROM. Skip them at the outer level;
        # nested-JOIN-in-subquery support is a later iteration.
        return
    # Ref / Literal / Raw / FkRef / This have no nested chain children.
