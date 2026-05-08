"""Relation-traversal handler implementations.

Implemented nodes
-----------------
RelationAll    — NOT EXISTS (... WHERE NOT predicate)
RelationAny    — EXISTS    (... WHERE predicate)
RelationFirst  — EXISTS (SELECT 1 FROM (... ORDER BY canonical_id LIMIT 1) f WHERE pred)

Still-stubbed nodes (raise NotImplementedError)
------------------------------------------------
FilteredRelation  — relation-valued; raise if used as top-level constraint body
RelationRef       — also relation-valued; no top-level predicate meaning
RelationProject   — projection side, /graph/query slice
RelationCount     — projection side, /graph/query slice
RelationAggregate — projection side, /graph/query slice
ScalarDerivation  — derivation, not a constraint predicate
FormatDerivation  — derivation, not a constraint predicate
RecursiveTraversal — recursive, not yet implemented
"""

from __future__ import annotations

from psycopg import sql

from knot.ontology.metaschema import (
    FilteredRelation,
    FormatDerivation,
    OntologyClass,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ScalarDerivation,
)

from knot.db._naming import bindings_table_id, table_id
from knot.db.sql_compiler._context import CompileContext
from knot.db.sql_compiler._dispatch import CompilerError, compile_predicate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_relation_ref(
    relation: RelationRef | FilteredRelation,
) -> tuple[RelationRef, FilteredRelation | None]:
    """Unwrap a possibly-filtered relation into (RelationRef, filter_or_None)."""
    if isinstance(relation, FilteredRelation):
        inner = relation.relation
        if isinstance(inner, FilteredRelation):
            raise CompilerError(
                "Nested FilteredRelation (depth > 1) is not supported in this slice."
            )
        return inner, relation
    return relation, None


def _target_class(ref: RelationRef) -> OntologyClass:
    """Extract the target OntologyClass from a RelationRef's slot range.

    Raises CompilerError if the slot's range is not an OntologyClass.
    """
    slot = ref.slot
    if not isinstance(slot.range, OntologyClass):
        raise CompilerError(
            f"RelationRef slot {slot.name!r} must have an OntologyClass as its "
            f"range for relation traversal; got {type(slot.range).__name__!r}.  "
            "Only class-ranged slots can be traversed."
        )
    return slot.range


def _build_subquery_body(
    ref: RelationRef,
    outer_ctx: CompileContext,
    *,
    row_alias: str,
    bind_alias: str,
    filter_node: FilteredRelation | None,
) -> sql.Composable:
    """Emit the shared FROM/JOIN/WHERE core for EXISTS subqueries.

    Returns a fragment like:
        SELECT 1
        FROM knot_data.<target> <row_alias>
        JOIN knot_data.<target>_bindings <bind_alias>
          ON <bind_alias>.knot_row_id = <row_alias>._knot_row_id
         AND <bind_alias>.valid_to IS NULL
        WHERE <bind_alias>.canonical_id = <outer_alias>.<fk_col>
          [AND <filter_predicate>]
    """
    target_cls = _target_class(ref)
    fk_col = ref.slot.name

    parts = sql.SQL(
        "SELECT 1"
        " FROM {tbl} {ra}"
        " JOIN {btbl} {ba}"
        "   ON {ba}.knot_row_id = {ra}._knot_row_id AND {ba}.valid_to IS NULL"
        " WHERE {ba}.canonical_id = {outer_alias}.{fk_col}"
    ).format(
        tbl=table_id(target_cls),
        btbl=bindings_table_id(target_cls),
        ra=sql.Identifier(row_alias),
        ba=sql.Identifier(bind_alias),
        outer_alias=sql.Identifier(outer_ctx.alias),
        fk_col=sql.Identifier(fk_col),
    )

    if filter_node is not None:
        filter_ctx = outer_ctx.with_subquery_alias(target_cls, row_alias)
        filter_sql = compile_predicate(filter_node.filter, filter_ctx)
        parts = parts + sql.SQL(" AND ({f})").format(f=filter_sql)

    return parts


# ---------------------------------------------------------------------------
# RelationAll — NOT EXISTS (... WHERE NOT predicate)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_relation_all(node: RelationAll, ctx: CompileContext) -> sql.Composable:
    if node.body is None:
        raise CompilerError(
            "RelationAll.body must be provided; a vacuous (body=None) quantifier "
            "has no SQL translation."
        )

    ref, filter_node = _resolve_relation_ref(node.relation)
    target_cls = _target_class(ref)

    inner_ctx = ctx.with_subquery_alias(target_cls, "t")
    body_sql = compile_predicate(node.body, inner_ctx)

    core = _build_subquery_body(
        ref, ctx,
        row_alias="t",
        bind_alias="tb",
        filter_node=filter_node,
    )

    return sql.SQL("NOT EXISTS ({core} AND NOT ({body}))").format(
        core=core,
        body=body_sql,
    )


# ---------------------------------------------------------------------------
# RelationAny — EXISTS (... WHERE predicate)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_relation_any(node: RelationAny, ctx: CompileContext) -> sql.Composable:
    ref, filter_node = _resolve_relation_ref(node.relation)
    target_cls = _target_class(ref)

    core = _build_subquery_body(
        ref, ctx,
        row_alias="t",
        bind_alias="tb",
        filter_node=filter_node,
    )

    # RelationAny with no additional body predicate: just EXISTS the relation.
    return sql.SQL("EXISTS ({core})").format(core=core)


# ---------------------------------------------------------------------------
# RelationFirst — EXISTS (SELECT * FROM (...LIMIT 1) f WHERE predicate)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_relation_first(node: RelationFirst, ctx: CompileContext) -> sql.Composable:
    ref, filter_node = _resolve_relation_ref(node.relation)
    target_cls = _target_class(ref)

    core = _build_subquery_body(
        ref, ctx,
        row_alias="t",
        bind_alias="tb",
        filter_node=filter_node,
    )

    # Wrap the core in ORDER BY canonical_id LIMIT 1, alias it as "f".
    inner_select = sql.SQL(
        "SELECT t.* FROM {tbl} t"
        " JOIN {btbl} tb"
        "   ON tb.knot_row_id = t._knot_row_id AND tb.valid_to IS NULL"
        " WHERE tb.canonical_id = {outer_alias}.{fk_col}"
        " ORDER BY tb.canonical_id LIMIT 1"
    ).format(
        tbl=table_id(target_cls),
        btbl=bindings_table_id(target_cls),
        outer_alias=sql.Identifier(ctx.alias),
        fk_col=sql.Identifier(ref.slot.name),
    )

    # Compile the project predicate against alias "f".
    proj_ctx = ctx.with_subquery_alias(target_cls, "f")
    proj_sql = compile_predicate(node.project, proj_ctx)

    return sql.SQL(
        "EXISTS (SELECT 1 FROM ({inner}) f WHERE ({proj}))"
    ).format(
        inner=inner_select,
        proj=proj_sql,
    )


# ---------------------------------------------------------------------------
# Stubs for nodes that remain unimplemented
# ---------------------------------------------------------------------------

def _stub(name: str, hint: str = ""):
    default_hint = (
        f"{name} compilation lands with /graph/query (cross-class JOIN "
        "logic not implemented in this slice).  Use Within / Compare / "
        "BoolExpr / Between / Matches for within-class constraint predicates."
    )
    msg = hint or default_hint

    def _handler(node, ctx: CompileContext) -> sql.Composable:
        raise NotImplementedError(msg)
    return _handler


compile_predicate.register(FilteredRelation)(
    _stub(
        "FilteredRelation",
        "FilteredRelation is a relation-valued node, not a boolean predicate.  "
        "Wrap it in RelationAll or RelationAny to use it as a constraint body.",
    )
)
compile_predicate.register(RelationRef)(
    _stub(
        "RelationRef",
        "RelationRef is a relation-valued node, not a boolean predicate.  "
        "Wrap it in RelationAll or RelationAny to use it as a constraint body.",
    )
)
compile_predicate.register(RelationProject)(
    _stub("RelationProject", "RelationProject is a projection node; use /graph/query.")
)
compile_predicate.register(RelationCount)(
    _stub("RelationCount", "RelationCount is a projection node; use /graph/query.")
)
compile_predicate.register(RelationAggregate)(
    _stub("RelationAggregate", "RelationAggregate is a projection node; use /graph/query.")
)
compile_predicate.register(ScalarDerivation)(_stub("ScalarDerivation"))
compile_predicate.register(FormatDerivation)(_stub("FormatDerivation"))
compile_predicate.register(RecursiveTraversal)(_stub("RecursiveTraversal"))
