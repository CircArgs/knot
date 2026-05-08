"""Relation-traversal handler implementations.

Implemented nodes
-----------------
RelationAll      — NOT EXISTS (... WHERE NOT predicate)
RelationAny      — EXISTS    (... WHERE predicate)
RelationFirst    — EXISTS (SELECT 1 FROM (... ORDER BY canonical_id LIMIT 1) f WHERE pred)
RelationProject  — (SELECT array_agg(t.<slot>) FROM ... WHERE ...)  → array value
RelationCount    — (SELECT count(*) FROM ... WHERE ...)             → integer value
RelationAggregate— (SELECT <agg>(t.<slot>) FROM ... WHERE ...)     → scalar/array value
FilteredRelation — unwrapped inside value handlers; raises if used standalone

Still-stubbed nodes (raise NotImplementedError)
------------------------------------------------
FilteredRelation  — relation-valued; raises if used as a top-level standalone
RelationRef       — relation-valued; no top-level predicate meaning
ScalarDerivation  — derivation, not a constraint predicate
FormatDerivation  — derivation, not a constraint predicate
RecursiveTraversal — recursive, not yet implemented
"""

from __future__ import annotations

from psycopg import sql

from knot.ontology.metaschema import (
    AggFunc,
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
    ReverseRelation,
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
    """Extract the target OntologyClass from a RelationRef's slot.

    Three cases, in resolution order:
      1. ``slot.range`` is an OntologyClass — direct ranged ref.
      2. ``slot.reference`` is a ``DirectRef`` or ``DiscriminatedRef`` with a
         statically-known ``target_class`` — use it.
      3. Neither — raise CompilerError. (DiscriminatedRef without target_class
         is true row-level polymorphism and isn't supported in this slice.)

    The slot still holds the FK value as a column (``range=string`` typically);
    the JOIN goes through the target's bindings table, the same shape used
    for class-ranged refs.
    """
    slot = ref.slot
    if isinstance(slot.range, OntologyClass):
        return slot.range
    reference = getattr(slot, "reference", None)
    target = getattr(reference, "target_class", None)
    if isinstance(target, OntologyClass):
        return target
    raise CompilerError(
        f"RelationRef slot {slot.name!r} cannot be traversed: its range is "
        f"{type(slot.range).__name__!r} and it has no static "
        f"reference.target_class. Either declare range as an OntologyClass "
        f"or set slot.reference.target_class."
    )


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


def _build_reverse_subquery_body(
    rev: ReverseRelation,
    outer_ctx: CompileContext,
    *,
    row_alias: str,
    bind_alias: str,
    outer_bind_alias: str,
) -> sql.Composable:
    """Emit the FROM/JOIN/WHERE core for EXISTS subqueries over a ReverseRelation.

    Pattern: all rows of ``rev.target_class`` whose ``rev.fk_slot`` value
    matches the outer row's canonical_id (from its bindings table).

        SELECT 1
        FROM knot_data.<target> <row_alias>
        JOIN knot_data.<target>_bindings <bind_alias>
          ON <bind_alias>.knot_row_id = <row_alias>._knot_row_id
         AND <bind_alias>.valid_to IS NULL
        JOIN knot_data.<primary>_bindings <outer_bind_alias>
          ON <outer_bind_alias>.knot_row_id = <outer_alias>._knot_row_id
         AND <outer_bind_alias>.valid_to IS NULL
        WHERE <row_alias>.<fk_slot> = <outer_bind_alias>.canonical_id
    """
    target_cls = rev.target_class
    fk_col = rev.fk_slot.name
    primary_cls = outer_ctx.primary_class

    return sql.SQL(
        "SELECT 1"
        " FROM {tbl} {ra}"
        " JOIN {btbl} {ba}"
        "   ON {ba}.knot_row_id = {ra}._knot_row_id AND {ba}.valid_to IS NULL"
        " JOIN {outer_btbl} {oba}"
        "   ON {oba}.knot_row_id = {outer_alias}._knot_row_id AND {oba}.valid_to IS NULL"
        " WHERE {ra}.{fk_col} = {oba}.canonical_id"
    ).format(
        tbl=table_id(target_cls),
        btbl=bindings_table_id(target_cls),
        ra=sql.Identifier(row_alias),
        ba=sql.Identifier(bind_alias),
        outer_btbl=bindings_table_id(primary_cls),
        oba=sql.Identifier(outer_bind_alias),
        outer_alias=sql.Identifier(outer_ctx.alias),
        fk_col=sql.Identifier(fk_col),
    )


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

    if isinstance(node.relation, ReverseRelation):
        rev = node.relation
        target_cls = rev.target_class
        inner_ctx = ctx.with_subquery_alias(target_cls, "t")
        body_sql = compile_predicate(node.body, inner_ctx)
        core = _build_reverse_subquery_body(
            rev, ctx,
            row_alias="t",
            bind_alias="tb",
            outer_bind_alias="ob",
        )
        return sql.SQL("NOT EXISTS ({core} AND NOT ({body}))").format(
            core=core, body=body_sql,
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
    if isinstance(node.relation, ReverseRelation):
        rev = node.relation
        core = _build_reverse_subquery_body(
            rev, ctx,
            row_alias="t",
            bind_alias="tb",
            outer_bind_alias="ob",
        )
        return sql.SQL("EXISTS ({core})").format(core=core)

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
# Value-projection helpers — shared subquery body builders
# ---------------------------------------------------------------------------

def _resolve_relation_and_filter(
    relation: "RelationRef | FilteredRelation | ReverseRelation",
) -> "tuple[RelationRef | ReverseRelation, FilteredRelation | None]":
    """Unwrap a (possibly filtered) relation into (core, filter_or_None).

    Only one level of FilteredRelation is supported; deeper nesting raises.
    """
    if isinstance(relation, FilteredRelation):
        inner = relation.relation
        if isinstance(inner, FilteredRelation):
            raise CompilerError(
                "Nested FilteredRelation (depth > 1) is not supported."
            )
        return inner, relation
    return relation, None


def _build_forward_value_body(
    ref: RelationRef,
    outer_ctx: CompileContext,
    *,
    row_alias: str,
    bind_alias: str,
    filter_node: "FilteredRelation | None",
    select_expr: sql.Composable,
) -> sql.Composable:
    """Emit a complete scalar/array subquery for a forward-FK RelationRef.

    Returns:
        SELECT <select_expr>
        FROM knot_data.<target> <row_alias>
        JOIN knot_data.<target>_bindings <bind_alias>
          ON <bind_alias>.knot_row_id = <row_alias>._knot_row_id
         AND <bind_alias>.valid_to IS NULL
        WHERE <bind_alias>.canonical_id = <outer>.<fk_col>
          [AND <filter_predicate>]
    """
    target_cls = _target_class(ref)
    fk_col = ref.slot.name

    body = sql.SQL(
        "SELECT {expr}"
        " FROM {tbl} {ra}"
        " JOIN {btbl} {ba}"
        "   ON {ba}.knot_row_id = {ra}._knot_row_id AND {ba}.valid_to IS NULL"
        " WHERE {ba}.canonical_id = {outer_alias}.{fk_col}"
    ).format(
        expr=select_expr,
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
        body = body + sql.SQL(" AND ({f})").format(f=filter_sql)

    return body


def _build_reverse_value_body(
    rev: ReverseRelation,
    outer_ctx: CompileContext,
    *,
    row_alias: str,
    bind_alias: str,
    outer_bind_alias: str,
    filter_node: "FilteredRelation | None",
    select_expr: sql.Composable,
) -> sql.Composable:
    """Emit a complete scalar/array subquery for a ReverseRelation.

    Pattern: rows of rev.target_class whose rev.fk_slot matches outer canonical_id.

        SELECT <select_expr>
        FROM knot_data.<target> <row_alias>
        JOIN knot_data.<target>_bindings <bind_alias>
          ON <bind_alias>.knot_row_id = <row_alias>._knot_row_id
         AND <bind_alias>.valid_to IS NULL
        JOIN knot_data.<primary>_bindings <outer_bind_alias>
          ON <outer_bind_alias>.knot_row_id = <outer_alias>._knot_row_id
         AND <outer_bind_alias>.valid_to IS NULL
        WHERE <row_alias>.<fk_slot> = <outer_bind_alias>.canonical_id
          [AND <filter_predicate>]
    """
    target_cls = rev.target_class
    fk_col = rev.fk_slot.name
    primary_cls = outer_ctx.primary_class

    body = sql.SQL(
        "SELECT {expr}"
        " FROM {tbl} {ra}"
        " JOIN {btbl} {ba}"
        "   ON {ba}.knot_row_id = {ra}._knot_row_id AND {ba}.valid_to IS NULL"
        " JOIN {outer_btbl} {oba}"
        "   ON {oba}.knot_row_id = {outer_alias}._knot_row_id AND {oba}.valid_to IS NULL"
        " WHERE {ra}.{fk_col} = {oba}.canonical_id"
    ).format(
        expr=select_expr,
        tbl=table_id(target_cls),
        btbl=bindings_table_id(target_cls),
        ra=sql.Identifier(row_alias),
        ba=sql.Identifier(bind_alias),
        outer_btbl=bindings_table_id(primary_cls),
        oba=sql.Identifier(outer_bind_alias),
        outer_alias=sql.Identifier(outer_ctx.alias),
        fk_col=sql.Identifier(fk_col),
    )

    if filter_node is not None:
        filter_ctx = outer_ctx.with_subquery_alias(target_cls, row_alias)
        filter_sql = compile_predicate(filter_node.filter, filter_ctx)
        body = body + sql.SQL(" AND ({f})").format(f=filter_sql)

    return body


# ---------------------------------------------------------------------------
# RelationProject — (SELECT array_agg(t.<slot>) FROM ... WHERE ...)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_relation_project(
    node: RelationProject,
    ctx: CompileContext,
) -> sql.Composable:
    """Emit a subquery expression that returns an array of projected slot values.

    The ``project`` SlotPath must be a single slot on the target class.
    Returns a correlated subquery:
        (SELECT array_agg(t.<slot_name>) FROM ... WHERE ...)
    """
    core, filter_node = _resolve_relation_and_filter(node.relation)

    if len(node.project.slots) != 1:
        raise CompilerError(
            "RelationProject.project must be a single-slot SlotPath; "
            "multi-hop projection is not supported in this slice."
        )
    proj_slot = node.project.slots[0]
    select_expr = sql.SQL("array_agg({ra}.{col})").format(
        ra=sql.Identifier("t"),
        col=sql.Identifier(proj_slot.name),
    )

    if isinstance(core, ReverseRelation):
        inner = _build_reverse_value_body(
            core, ctx,
            row_alias="t",
            bind_alias="tb",
            outer_bind_alias="ob",
            filter_node=filter_node,
            select_expr=select_expr,
        )
    else:
        inner = _build_forward_value_body(
            core, ctx,
            row_alias="t",
            bind_alias="tb",
            filter_node=filter_node,
            select_expr=select_expr,
        )

    return sql.SQL("({inner})").format(inner=inner)


# ---------------------------------------------------------------------------
# RelationCount — (SELECT count(*) FROM ... WHERE ...)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_relation_count(
    node: RelationCount,
    ctx: CompileContext,
) -> sql.Composable:
    """Emit a subquery expression that returns the count of matching rows."""
    core, filter_node = _resolve_relation_and_filter(node.relation)

    if node.distinct:
        select_expr = sql.SQL("count(DISTINCT tb.canonical_id)")
    else:
        select_expr = sql.SQL("count(*)")

    if isinstance(core, ReverseRelation):
        inner = _build_reverse_value_body(
            core, ctx,
            row_alias="t",
            bind_alias="tb",
            outer_bind_alias="ob",
            filter_node=filter_node,
            select_expr=select_expr,
        )
    else:
        inner = _build_forward_value_body(
            core, ctx,
            row_alias="t",
            bind_alias="tb",
            filter_node=filter_node,
            select_expr=select_expr,
        )

    return sql.SQL("({inner})").format(inner=inner)


# ---------------------------------------------------------------------------
# RelationAggregate — (SELECT <agg>(t.<slot>) FROM ... WHERE ...)
# ---------------------------------------------------------------------------

_AGG_FUNC_SQL: dict[AggFunc, str] = {
    AggFunc.COUNT:   "count",
    AggFunc.SUM:     "sum",
    AggFunc.AVG:     "avg",
    AggFunc.MIN:     "min",
    AggFunc.MAX:     "max",
    AggFunc.COLLECT: "array_agg",   # COLLECT → array_agg
    AggFunc.FIRST:   None,           # handled specially
}


@compile_predicate.register
def _compile_relation_aggregate(
    node: RelationAggregate,
    ctx: CompileContext,
) -> sql.Composable:
    """Emit a subquery expression that aggregates a slot over the relation.

    AggFunc.FIRST emits a LIMIT 1 subquery ordered by canonical_id.
    All other funcs emit a standard aggregate call.
    """
    core, filter_node = _resolve_relation_and_filter(node.relation)

    if node.func == AggFunc.FIRST:
        # FIRST: ORDER BY tb.canonical_id LIMIT 1 with optional operand slot.
        if node.operand is None or len(node.operand.slots) != 1:
            raise CompilerError(
                "RelationAggregate(FIRST) requires a single-slot operand SlotPath."
            )
        proj_slot = node.operand.slots[0]

        if isinstance(core, ReverseRelation):
            target_cls = core.target_class
            fk_col = core.fk_slot.name
            primary_cls = ctx.primary_class

            filter_clause = sql.SQL("")
            if filter_node is not None:
                filter_ctx = ctx.with_subquery_alias(target_cls, "t")
                filter_sql = compile_predicate(filter_node.filter, filter_ctx)
                filter_clause = sql.SQL(" AND ({f})").format(f=filter_sql)

            inner = sql.SQL(
                "SELECT t.{col}"
                " FROM {tbl} t"
                " JOIN {btbl} tb"
                "   ON tb.knot_row_id = t._knot_row_id AND tb.valid_to IS NULL"
                " JOIN {outer_btbl} ob"
                "   ON ob.knot_row_id = {outer_alias}._knot_row_id AND ob.valid_to IS NULL"
                " WHERE t.{fk_col} = ob.canonical_id{filter_clause}"
                " ORDER BY tb.canonical_id LIMIT 1"
            ).format(
                col=sql.Identifier(proj_slot.name),
                tbl=table_id(target_cls),
                btbl=bindings_table_id(target_cls),
                outer_btbl=bindings_table_id(primary_cls),
                outer_alias=sql.Identifier(ctx.alias),
                fk_col=sql.Identifier(fk_col),
                filter_clause=filter_clause,
            )
        else:
            target_cls = _target_class(core)
            fk_col = core.slot.name

            filter_clause = sql.SQL("")
            if filter_node is not None:
                filter_ctx = ctx.with_subquery_alias(target_cls, "t")
                filter_sql = compile_predicate(filter_node.filter, filter_ctx)
                filter_clause = sql.SQL(" AND ({f})").format(f=filter_sql)

            inner = sql.SQL(
                "SELECT t.{col}"
                " FROM {tbl} t"
                " JOIN {btbl} tb"
                "   ON tb.knot_row_id = t._knot_row_id AND tb.valid_to IS NULL"
                " WHERE tb.canonical_id = {outer_alias}.{fk_col}{filter_clause}"
                " ORDER BY tb.canonical_id LIMIT 1"
            ).format(
                col=sql.Identifier(proj_slot.name),
                tbl=table_id(target_cls),
                btbl=bindings_table_id(target_cls),
                outer_alias=sql.Identifier(ctx.alias),
                fk_col=sql.Identifier(fk_col),
                filter_clause=filter_clause,
            )

        return sql.SQL("({inner})").format(inner=inner)

    # Standard aggregate functions.
    agg_name = _AGG_FUNC_SQL.get(node.func)
    if agg_name is None:
        raise CompilerError(f"Unhandled AggFunc: {node.func!r}")

    if node.func == AggFunc.COUNT:
        # COUNT(*) — no operand needed.
        if node.distinct:
            select_expr = sql.SQL("count(DISTINCT tb.canonical_id)")
        else:
            select_expr = sql.SQL("count(*)")
    else:
        if node.operand is None or len(node.operand.slots) != 1:
            raise CompilerError(
                f"RelationAggregate(func={node.func!r}) requires a single-slot operand SlotPath."
            )
        proj_slot = node.operand.slots[0]
        if node.distinct:
            select_expr = sql.SQL("{agg}(DISTINCT {ra}.{col})").format(
                agg=sql.SQL(agg_name),
                ra=sql.Identifier("t"),
                col=sql.Identifier(proj_slot.name),
            )
        else:
            select_expr = sql.SQL("{agg}({ra}.{col})").format(
                agg=sql.SQL(agg_name),
                ra=sql.Identifier("t"),
                col=sql.Identifier(proj_slot.name),
            )

    if isinstance(core, ReverseRelation):
        inner = _build_reverse_value_body(
            core, ctx,
            row_alias="t",
            bind_alias="tb",
            outer_bind_alias="ob",
            filter_node=filter_node,
            select_expr=select_expr,
        )
    else:
        inner = _build_forward_value_body(
            core, ctx,
            row_alias="t",
            bind_alias="tb",
            filter_node=filter_node,
            select_expr=select_expr,
        )

    return sql.SQL("({inner})").format(inner=inner)


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
        "Wrap it in RelationAll, RelationAny, RelationProject, RelationCount, "
        "or RelationAggregate to use it.",
    )
)
compile_predicate.register(ReverseRelation)(
    _stub(
        "ReverseRelation",
        "ReverseRelation is a relation-valued node, not a boolean predicate.  "
        "Wrap it in RelationAll or RelationAny to use it as a class definition body.",
    )
)
compile_predicate.register(RelationRef)(
    _stub(
        "RelationRef",
        "RelationRef is a relation-valued node, not a boolean predicate.  "
        "Wrap it in RelationAll or RelationAny to use it as a constraint body.",
    )
)
compile_predicate.register(ScalarDerivation)(_stub("ScalarDerivation"))
compile_predicate.register(FormatDerivation)(_stub("FormatDerivation"))
compile_predicate.register(RecursiveTraversal)(_stub("RecursiveTraversal"))
