"""Predicate handler registrations for the SQL compiler.

Each handler registers against ``compile_predicate`` (from ``_dispatch``) for
a concrete expression-tree node type.  Handlers cover the full within-class
predicate subset; relation-traversal nodes are stubbed in ``_relation.py``.

SQL identifiers always go through ``psycopg.sql.Identifier``; literals always
go through ``sql.Placeholder()`` with the value pushed onto ``ctx.params``.
No f-string interpolation of user-controlled names.
"""

from __future__ import annotations

from psycopg import sql

from knot.ontology.metaschema import (
    Between,
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Literal_,
    Matches,
    SlotPath,
    Within,
)

from knot.db.sql_compiler._context import CompileContext
from knot.db.sql_compiler._dispatch import CompilerError, compile_predicate


# ---------------------------------------------------------------------------
# CompareOp → SQL operator fragment (binary)
# ---------------------------------------------------------------------------

_BINARY_OP_SQL: dict[CompareOp, str] = {
    CompareOp.EQ:      "=",
    CompareOp.NEQ:     "<>",
    CompareOp.GT:      ">",
    CompareOp.GTE:     ">=",
    CompareOp.LT:      "<",
    CompareOp.LTE:     "<=",
    CompareOp.IN:      "= ANY(%s)",   # handled specially
    CompareOp.NOT_IN:  "!= ALL(%s)",  # handled specially
}

_UNARY_OP_SQL: dict[CompareOp, str] = {
    CompareOp.IS_NULL:     "IS NULL",
    CompareOp.IS_NOT_NULL: "IS NOT NULL",
}


# ---------------------------------------------------------------------------
# Literal_
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_literal(node: Literal_, ctx: CompileContext) -> sql.Composable:
    ctx.params.append(node.value)
    return sql.Placeholder()


# ---------------------------------------------------------------------------
# SlotPath  (single-slot, within-primary-class only)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_slot_path(node: SlotPath, ctx: CompileContext) -> sql.Composable:
    if len(node.slots) != 1:
        raise CompilerError(
            f"Multi-slot SlotPath compilation (len={len(node.slots)}) is not "
            "supported in the predicate compiler; relation traversal lands with "
            "/graph/query (RelationAll / RelationAny)."
        )
    slot = node.slots[0]
    # Validate slot is on the primary class (by identity walk).
    primary_slot_ids = {id(s) for s in ctx.primary_class.slots}
    if id(slot) not in primary_slot_ids:
        raise CompilerError(
            f"SlotPath references slot {slot.name!r} which is not on the "
            f"primary class {ctx.primary_class.name!r}.  Cross-class slot "
            "paths require relation traversal (not implemented in this slice)."
        )
    return sql.SQL("{alias}.{col}").format(
        alias=sql.Identifier(ctx.alias),
        col=sql.Identifier(slot.name),
    )


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_compare(node: Compare, ctx: CompileContext) -> sql.Composable:
    left_sql = compile_predicate(node.left, ctx)

    # Unary operators (IS NULL / IS NOT NULL) — no right operand.
    if node.op in _UNARY_OP_SQL:
        return sql.SQL("({left}) {op}").format(
            left=left_sql,
            op=sql.SQL(_UNARY_OP_SQL[node.op]),
        )

    # IN / NOT_IN: right operand is a list literal → = ANY(%s) / != ALL(%s).
    if node.op in (CompareOp.IN, CompareOp.NOT_IN):
        if node.right is None:
            raise CompilerError(
                f"Compare(op={node.op!r}) requires a right operand."
            )
        op_fragment = "= ANY(" if node.op == CompareOp.IN else "!= ALL("
        # Right should be a Literal_ holding a list; push the list as one param.
        if isinstance(node.right, Literal_):
            ctx.params.append(node.right.value)
            return (
                sql.SQL("({left}) ").format(left=left_sql)
                + sql.SQL(op_fragment)
                + sql.Placeholder()
                + sql.SQL(")")
            )
        # SlotPath on the right of IN/NOT_IN: compile it (unusual but supported).
        right_sql = compile_predicate(node.right, ctx)
        return sql.SQL("({left}) {op}{right})").format(
            left=left_sql,
            op=sql.SQL(op_fragment),
            right=right_sql,
        )

    # Binary scalar operators.
    if node.right is None:
        raise CompilerError(
            f"Compare(op={node.op!r}) requires a right operand."
        )
    right_sql = compile_predicate(node.right, ctx)
    op_str = _BINARY_OP_SQL.get(node.op)
    if op_str is None:
        raise CompilerError(f"Unhandled CompareOp: {node.op!r}")

    return sql.SQL("({left}) {op} ({right})").format(
        left=left_sql,
        op=sql.SQL(op_str),
        right=right_sql,
    )


# ---------------------------------------------------------------------------
# BoolExpr  (AND / OR / NOT)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_bool_expr(node: BoolExpr, ctx: CompileContext) -> sql.Composable:
    if node.op == BoolOpKind.NOT:
        if len(node.operands) != 1:
            raise CompilerError(
                f"BoolExpr(NOT) must have exactly 1 operand, "
                f"got {len(node.operands)}."
            )
        inner = compile_predicate(node.operands[0], ctx)
        return sql.SQL("NOT ({inner})").format(inner=inner)

    if not node.operands:
        raise CompilerError(
            f"BoolExpr({node.op!r}) must have at least one operand."
        )

    sep = sql.SQL(" AND ") if node.op == BoolOpKind.AND else sql.SQL(" OR ")
    parts = [compile_predicate(operand, ctx) for operand in node.operands]
    joined = sep.join(sql.SQL("({part})").format(part=p) for p in parts)
    return sql.SQL("({joined})").format(joined=joined)


# ---------------------------------------------------------------------------
# Within  (s.<col> = ANY(%s))
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_within(node: Within, ctx: CompileContext) -> sql.Composable:
    col_sql = compile_predicate(node.left, ctx)
    values = [lit.value for lit in node.values]
    ctx.params.append(values)
    return sql.SQL("({col}) = ANY(").format(col=col_sql) + sql.Placeholder() + sql.SQL(")")


# ---------------------------------------------------------------------------
# Between  (s.<col> BETWEEN %s AND %s)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_between(node: Between, ctx: CompileContext) -> sql.Composable:
    # Compile left first (typically a SlotPath — no params pushed).
    # low/high are typically Literal_ nodes that push params.
    col_sql = compile_predicate(node.left, ctx)
    low_sql = compile_predicate(node.lower, ctx)
    high_sql = compile_predicate(node.upper, ctx)
    if node.inclusive:
        return sql.SQL("({col}) BETWEEN ({low}) AND ({high})").format(
            col=col_sql, low=low_sql, high=high_sql,
        )
    # Non-inclusive: col > low AND col < high  (two separate comparisons)
    gt_part = sql.SQL("({col}) > ({low})").format(col=col_sql, low=low_sql)
    lt_part = sql.SQL("({col}) < ({high})").format(col=col_sql, high=high_sql)
    return sql.SQL("({gt} AND {lt})").format(gt=gt_part, lt=lt_part)


# ---------------------------------------------------------------------------
# Matches  (s.<col> LIKE %s)
# ---------------------------------------------------------------------------

@compile_predicate.register
def _compile_matches(node: Matches, ctx: CompileContext) -> sql.Composable:
    col_sql = compile_predicate(node.left, ctx)
    ctx.params.append(node.pattern)
    return sql.SQL("({col}) LIKE ").format(col=col_sql) + sql.Placeholder()
