"""Postgres SQL compilation for the read substrate.

Sibling dispatch table to ``compile_sql`` — that one renders Expr
fragments, this one renders Query statements. Both return raw SQL
strings; literal values are inlined at compile time so there is no
positional parameter list to thread through to the driver.

JOIN assembly: a pre-pass walks the query AST (where / order_by /
projection) collecting every ``FkChainRef``. Each unique chain prefix
(source_class + FK slot path) gets one aliased JOIN. Two FK slots that
point at the same target class get distinct aliases — e.g.
``movie_director`` vs ``movie_writer`` — so postgres never sees a
duplicate table reference. The alias scheme is defined in
``knot.compile._aliases.chain_alias`` and used identically here and
in the ``FkChainRef`` expr compiler so column references always match
the alias assigned to their JOIN.

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
from knot.compile._aliases import chain_alias
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
        collect_fk_chains(node.where_clause, chains)
    for ob in node.ordering:
        collect_fk_chains(ob.ref, chains)
    if node.projection is not None:
        for r in node.projection:
            collect_fk_chains(r, chains)

    joins = emit_fk_joins(spec, chains, schema=schema, layer=layer)

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
            compile_sql(g, schema=schema, layer=layer, outer_class=node.class_name)
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


def collect_fk_chains(node: Expr, out: list[FkChainRef]) -> None:
    """Walk an Expr tree collecting every ``FkChainRef`` reached.
    Deduplication happens at JOIN-emit time on the (source, fk, target)
    triple — not on the FkChainRef itself, so multiple chains that
    share a prefix still produce one JOIN per shared step.

    Public because the constraint emitter
    (``knot.compile.constraints.emit_validation``) needs to walk
    constraint bodies for the same JOINs the query compiler builds."""
    if isinstance(node, FkChainRef):
        out.append(node)
        return
    if isinstance(node, (Compare, BoolOp)):
        collect_fk_chains(node.left, out)
        collect_fk_chains(node.right, out)
        return
    if isinstance(node, Not):
        collect_fk_chains(node.expr, out)
        return
    if isinstance(node, IsNull):
        collect_fk_chains(node.expr, out)
        return
    if isinstance(node, InList):
        collect_fk_chains(node.left, out)
        return
    if isinstance(node, Between):
        collect_fk_chains(node.left, out)
        return
    if isinstance(node, Exists):
        if node.where is not None:
            collect_fk_chains(node.where, out)
        return
    if isinstance(node, CountRel):
        if node.where is not None:
            collect_fk_chains(node.where, out)
        return
    if isinstance(node, Aggregate):
        # FK chains inside an Aggregate predicate are scoped to the
        # sub-query, not the outer FROM. Skip them at the outer level;
        # nested-JOIN-in-subquery support is a later iteration.
        return
    # Ref / Literal / Raw / FkRef / This have no nested chain children.


def emit_fk_joins(
    spec: Spec,
    chains: list[FkChainRef],
    *,
    schema: str,
    layer: str,
    join_kind: str = "JOIN",
) -> list[str]:
    """Compile a list of FkChainRef nodes to JOIN clauses.

    Each chain prefix (source_class + FK slot path) gets one aliased
    JOIN, deduplicated by alias. Two FK slots that point at the same
    target class get distinct aliases so postgres never sees a
    duplicate table reference. ``join_kind`` controls inner vs outer:
    query reads want INNER (matched rows only); the constraint
    emitter wants ``LEFT JOIN`` so a row with a NULL FK still
    surfaces in WHERE-NOT evaluation."""
    seen: set[str] = set()
    joins: list[str] = []
    for chain_ref in chains:
        # ``lhs`` tracks the left-hand side of the ON clause: the
        # schema-qualified primary table for the first hop, then the
        # alias of the previous hop for every subsequent hop.
        lhs = f"{schema}.{chain_ref.source_class.lower()}{layer}"
        for i, (fk_slot, target_class) in enumerate(chain_ref.chain):
            alias = chain_alias(chain_ref.source_class, chain_ref.chain[: i + 1])
            if alias not in seen:
                seen.add(alias)
                target_cls = _lookup_class(spec, target_class)
                target_ident = target_cls.identifier_slot().name
                joins.append(
                    f"{join_kind} {schema}.{target_class.lower()}{layer} AS {alias} "
                    f"ON {alias}.{target_ident} = {lhs}.{fk_slot}"
                )
            lhs = alias
    return joins
