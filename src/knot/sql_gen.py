"""SQL emitter for knot expression trees.

Translates knot's typed Pydantic expression tree (metaschema.py) into SQL via
sqlglot AST.  Targets: Spark, Trino, DuckDB.

Single-dispatch visitor pattern over the expression tree, returning
sqlglot.exp.Expression nodes.  Mirrors walk.py's @singledispatch shape.

Hard rules (from sql-generation.md):
- Real sqlglot AST throughout — no raw SQL string assembly.
- Dialect-specific constructs go through _dialect_* helpers, not string concat.
- RecursiveTraversal raises UnsupportedDerivationError at compile time.
"""

from __future__ import annotations

import re
from functools import singledispatch
from typing import Any

import sqlglot
from sqlglot import exp

from knot.metaschema import (
    AggFunc,
    Between,
    BoolExpr,
    BoolOp,
    Compare,
    CompareOp,
    FilteredRelation,
    FormatDerivation,
    GroupByMode,
    Literal_,
    Matches,
    OntologyClass,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ResolutionPolicy,
    ScalarDerivation,
    Slot,
    SlotPath,
    Within,
)


# ---------------------------------------------------------------------------
# Public errors
# ---------------------------------------------------------------------------

class UnsupportedDerivationError(Exception):
    """Raised when an expression-tree node cannot be lowered to SQL."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def emit_sql(node: Any, dialect: str = "duckdb") -> str:
    """Render an expression-tree node as SQL for the given dialect."""
    ast = to_sqlglot(node)
    return ast.sql(dialect=dialect)


def emit_forward_chain(slot: Slot, dialect: str = "duckdb") -> str:
    """Forward-chain SQL: INSERT INTO target SELECT ... — for derivation at materialization.

    Emits INSERT INTO <class>_<slot>_edges SELECT ... based on the slot's derivation.
    """
    if slot.derivation is None:
        raise UnsupportedDerivationError(f"Slot {slot.name!r} has no derivation")
    inner = to_sqlglot(slot.derivation)
    table_name = f"{slot.name}_derived"
    insert = exp.insert(inner, table_name, dialect=dialect)
    return insert.sql(dialect=dialect)


def emit_backward_chain(slot: Slot, dialect: str = "duckdb") -> str:
    """Backward-chain SQL: SELECT ... JOIN ... — for translator queries.

    Returns a SELECT statement resolving the slot's derivation backward.
    """
    if slot.derivation is None:
        raise UnsupportedDerivationError(f"Slot {slot.name!r} has no derivation")
    ast = to_sqlglot(slot.derivation)
    return ast.sql(dialect=dialect)


def emit_trust_resolved_cte(class_: OntologyClass, dialect: str = "duckdb") -> exp.Expression:
    """Build the WITH <class>_resolved AS (...) CTE that applies per-slot ResolutionPolicy.

    Uses the trust-resolution placeholder pattern (sql-generation.md §Trust-resolution
    placeholder): CTE name is __trust_resolved__<ClassName>.
    """
    cte_name = f"__trust_resolved__{class_.name}"
    table_name = f"resolved_facts_{class_.name.lower()}"

    # Build SELECT canonical_id, <policy_expr> AS <slot_name>, ...
    select_exprs: list[exp.Expression] = [exp.column("canonical_id")]

    for slot in class_.slots:
        policy = ResolutionPolicy(slot.resolution_policy)
        reduction = _policy_reduction(slot.name, policy, dialect)
        select_exprs.append(exp.alias_(reduction, slot.name))

    cte_select = (
        exp.select(*select_exprs)
        .from_(table_name)
        .group_by(exp.column("canonical_id"))
    )

    cte_alias = exp.TableAlias(this=exp.to_identifier(cte_name))
    cte_node = exp.CTE(this=cte_select, alias=cte_alias)
    return exp.With(expressions=[cte_node])


def emit_validation_query(constraint: Any, dialect: str = "duckdb") -> str:
    """Constraint body → offender-row query.

    Returns a SELECT with the standard 5-column validation shape:
    rule_id, class_name, slot_name, offending_pk, detail.
    Empty result = pass.
    """
    if hasattr(constraint, "filter") and isinstance(constraint.filter, (Compare, BoolExpr)):
        where_expr = to_sqlglot(constraint.filter)
    else:
        where_expr = to_sqlglot(constraint) if not isinstance(constraint, str) else None

    rule_id = exp.alias_(exp.Literal.string(str(getattr(constraint, "name", "constraint"))), "rule_id")
    class_name_col = exp.alias_(exp.Literal.string(""), "class_name")
    slot_name_col = exp.alias_(exp.Null(), "slot_name")
    offending_pk = exp.alias_(exp.column("canonical_id"), "offending_pk")
    detail = exp.alias_(exp.Literal.string(""), "detail")

    query = exp.select(rule_id, class_name_col, slot_name_col, offending_pk, detail)
    if where_expr is not None:
        query = query.where(where_expr)
    return query.sql(dialect=dialect)


# ---------------------------------------------------------------------------
# Internal: per-policy trust reduction
# ---------------------------------------------------------------------------

def _policy_reduction(slot_name: str, policy: ResolutionPolicy, dialect: str) -> exp.Expression:
    """Return the sqlglot expression for the given ResolutionPolicy over a slot's columns.

    Assumes resolved_facts_<Class> has columns: <slot>_value, <slot>_trust, asserted_at.
    """
    val_col = exp.column(f"{slot_name}_value")
    trust_col = exp.column(f"{slot_name}_trust")
    at_col = exp.column("asserted_at")

    if policy == ResolutionPolicy.ARGMAX_TRUST:
        return _argmax(val_col, trust_col, dialect)

    if policy == ResolutionPolicy.MODE:
        return _mode_expr(val_col, dialect)

    if policy == ResolutionPolicy.WEIGHTED_VOTE:
        # argmax(value, sum_trust) over a sub-group-by is complex; we emit a
        # correlated-subquery approximation: argmax over the aggregated trust.
        sum_trust = exp.Sum(this=trust_col)
        return _argmax(val_col, sum_trust, dialect)

    if policy == ResolutionPolicy.MEDIAN_NUMERIC:
        return _percentile_cont(val_col, dialect)

    if policy == ResolutionPolicy.LATEST_WATERMARK:
        return _argmax(val_col, at_col, dialect)

    if policy == ResolutionPolicy.UNIQUE_OR_FAIL:
        count_distinct = exp.Count(this=exp.Distinct(expressions=[val_col]))
        return exp.Case(
            ifs=[
                exp.If(
                    this=exp.GT(this=count_distinct, expression=exp.Literal.number(1)),
                    true=exp.Anonymous(
                        this="error", expressions=[exp.Literal.string("disagreement")]
                    ),
                )
            ],
            default=exp.Anonymous(this="first", expressions=[val_col]),
        )

    raise UnsupportedDerivationError(f"Unknown ResolutionPolicy: {policy}")


def _argmax(value: exp.Expression, key: exp.Expression, dialect: str) -> exp.Expression:
    """argmax(value, key) — Trino/DuckDB; max_by(value, key) for Spark."""
    if dialect == "spark":
        return exp.Anonymous(this="max_by", expressions=[value, key])
    return exp.Anonymous(this="argmax", expressions=[value, key])


def _mode_expr(value: exp.Expression, dialect: str) -> exp.Expression:
    """mode() WITHIN GROUP (ORDER BY value) — Trino; DuckDB uses mode(value)."""
    if dialect == "duckdb":
        # DuckDB supports mode(expr) directly as an aggregate
        return exp.Anonymous(this="mode", expressions=[value])
    # Trino / Spark: WITHIN GROUP syntax
    mode_fn = exp.Anonymous(this="mode", expressions=[])
    return exp.WithinGroup(
        this=mode_fn,
        expression=exp.Order(expressions=[exp.Ordered(this=value)]),
    )


def _percentile_cont(value: exp.Expression, dialect: str) -> exp.Expression:
    """percentile_cont(0.5) WITHIN GROUP (ORDER BY value)."""
    pc = exp.Anonymous(this="percentile_cont", expressions=[exp.Literal.number("0.5")])
    return exp.WithinGroup(
        this=pc,
        expression=exp.Order(expressions=[exp.Ordered(this=value)]),
    )


# ---------------------------------------------------------------------------
# Core singledispatch visitor
# ---------------------------------------------------------------------------

@singledispatch
def to_sqlglot(node: Any) -> exp.Expression:
    """Convert any expression-tree node to sqlglot AST.

    Visitors registered per type.  Unregistered types raise NotImplementedError
    so newly added metaschema nodes are caught immediately.
    """
    raise NotImplementedError(
        f"No to_sqlglot handler for {type(node).__name__}. "
        "Add a @to_sqlglot.register handler in knot/sql_gen.py."
    )


# ---------------------------------------------------------------------------
# Leaf nodes
# ---------------------------------------------------------------------------

@to_sqlglot.register
def _(node: Literal_) -> exp.Expression:
    """Literal_ → exp.Literal (number or string based on Python type)."""
    v = node.value
    if v is None:
        return exp.Null()
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float)):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


@to_sqlglot.register
def _(node: SlotPath) -> exp.Expression:
    """SlotPath → exp.Column (qualified by table alias when from_class is not sentinel).

    For paths with a single slot: table.column.
    Multi-slot paths (traversal): only the terminal column; JOIN chain is
    established at the relation level (RelationRef / FilteredRelation).
    """
    table = None
    if node.from_class.name != "__sentinel__":
        table = node.from_class.name.lower()

    if not node.slots:
        if table:
            return exp.column("*", table=table)
        return exp.Star()

    terminal_slot = node.slots[-1]
    return exp.column(terminal_slot.name, table=table)


# ---------------------------------------------------------------------------
# Predicate nodes
# ---------------------------------------------------------------------------

_COMPARE_OP_MAP = {
    CompareOp.EQ:  exp.EQ,
    CompareOp.NEQ: exp.NEQ,
    CompareOp.GT:  exp.GT,
    CompareOp.GTE: exp.GTE,
    CompareOp.LT:  exp.LT,
    CompareOp.LTE: exp.LTE,
}


@to_sqlglot.register
def _(node: Compare) -> exp.Expression:
    """Compare → EQ / NEQ / GT / GTE / LT / LTE / IS NULL / IS NOT NULL / IN / NOT IN."""
    left = to_sqlglot(node.left)
    op = CompareOp(node.op)

    if op == CompareOp.IS_NULL:
        return exp.Is(this=left, expression=exp.Null())

    if op == CompareOp.IS_NOT_NULL:
        return exp.Not(this=exp.Is(this=left, expression=exp.Null()))

    right = to_sqlglot(node.right) if node.right is not None else exp.Null()

    if op == CompareOp.IN:
        # right should be a list literal — treat right as a single item in a list
        items = right if isinstance(right, list) else [right]
        return exp.In(this=left, expressions=items)

    if op == CompareOp.NOT_IN:
        items = right if isinstance(right, list) else [right]
        return exp.Not(this=exp.In(this=left, expressions=items))

    cls = _COMPARE_OP_MAP.get(op)
    if cls is None:
        raise UnsupportedDerivationError(f"Unhandled CompareOp: {op}")
    return cls(this=left, expression=right)


@to_sqlglot.register
def _(node: BoolExpr) -> exp.Expression:
    """BoolExpr → AND / OR / NOT over converted operands."""
    op = BoolOp(node.op)
    operands = [to_sqlglot(o) for o in node.operands]

    if op == BoolOp.NOT:
        if len(operands) != 1:
            raise UnsupportedDerivationError("BoolExpr NOT must have exactly one operand")
        return exp.Not(this=operands[0])

    if op == BoolOp.AND:
        result = operands[0]
        for part in operands[1:]:
            result = exp.And(this=result, expression=part)
        return result

    if op == BoolOp.OR:
        result = operands[0]
        for part in operands[1:]:
            result = exp.Or(this=result, expression=part)
        return result

    raise UnsupportedDerivationError(f"Unhandled BoolOp: {op}")


@to_sqlglot.register
def _(node: Within) -> exp.Expression:
    """Within → column IN (values)."""
    left = to_sqlglot(node.left)
    values = [to_sqlglot(v) for v in node.values]
    return exp.In(this=left, expressions=values)


@to_sqlglot.register
def _(node: Between) -> exp.Expression:
    """Between → BETWEEN low AND high (inclusive) or compound comparisons (exclusive)."""
    left = to_sqlglot(node.left)
    low = to_sqlglot(node.lower)
    high = to_sqlglot(node.upper)

    if node.inclusive:
        return exp.Between(this=left, low=low, high=high)

    # Exclusive bounds: left > low AND left < high
    return exp.And(
        this=exp.GT(this=left, expression=low),
        expression=exp.LT(this=left, expression=high),
    )


def _is_regex(pattern: str) -> bool:
    """Return True if pattern looks like a regex (contains regex metacharacters)."""
    return bool(re.search(r"[\^$.*+?{}\[\]|()\\]", pattern))


@to_sqlglot.register
def _(node: Matches) -> exp.Expression:
    """Matches → dialect-appropriate LIKE or regexp.

    If the pattern looks like a SQL LIKE pattern (contains % or _) or has no
    regex metacharacters: emit LIKE.  If it contains regex metacharacters: emit
    dialect-appropriate regexp (DuckDB: REGEXP_MATCHES, Trino: REGEXP_LIKE,
    Spark: RLIKE via RegexpLike).

    Note: singledispatch cannot be dialect-aware at registration time.  The
    caller uses emit_sql(node, dialect=...) → to_sqlglot which returns a
    RegexpLike node; sqlglot's dialect emitters handle the function-name
    difference (REGEXP_MATCHES vs REGEXP_LIKE vs RLIKE) automatically.
    """
    left = to_sqlglot(node.left)
    pat = node.pattern

    if _is_regex(pat):
        # sqlglot RegexpLike emits REGEXP_MATCHES (DuckDB), REGEXP_LIKE (Trino),
        # or RLIKE (Spark) — dialect handled by sqlglot's emitters.
        return exp.RegexpLike(this=left, expression=exp.Literal.string(pat))

    # Pure LIKE pattern (%, _ wildcards only)
    return exp.Like(this=left, expression=exp.Literal.string(pat))


# ---------------------------------------------------------------------------
# Relation nodes
# ---------------------------------------------------------------------------

@to_sqlglot.register
def _(node: RelationRef) -> exp.Expression:
    """RelationRef → SELECT * FROM <class_table>.

    The table name is the lower-case class name (snake_case convention).
    """
    table = node.from_class.name.lower()
    return exp.select(exp.Star()).from_(table)


@to_sqlglot.register
def _(node: FilteredRelation) -> exp.Expression:
    """FilteredRelation → SELECT * FROM <relation> WHERE <filter>."""
    base = to_sqlglot(node.relation)
    where_expr = to_sqlglot(node.filter)
    return base.where(where_expr)


@to_sqlglot.register
def _(node: RelationProject) -> exp.Expression:
    """RelationProject → SELECT <projection> FROM <relation>."""
    base = to_sqlglot(node.relation)
    col = to_sqlglot(node.project)
    # Replace the star-select with the projected column
    return base.select(col, append=False)


@to_sqlglot.register
def _(node: RelationCount) -> exp.Expression:
    """RelationCount → SELECT COUNT(*) [DISTINCT] FROM <relation>."""
    base = to_sqlglot(node.relation)
    if node.distinct:
        count_expr = exp.Count(this=exp.Distinct(expressions=[exp.Star()]))
    else:
        count_expr = exp.Count(this=exp.Star())
    return base.select(count_expr, append=False)


@to_sqlglot.register
def _(node: RelationAggregate) -> exp.Expression:
    """RelationAggregate → SELECT <agg_func>(<operand>) FROM <relation> [GROUP BY ...]."""
    base = to_sqlglot(node.relation)
    func = AggFunc(node.func)

    if node.operand is not None:
        operand = to_sqlglot(node.operand)
        if node.distinct:
            operand = exp.Distinct(expressions=[operand])
    else:
        operand = exp.Star()

    agg_expr = _build_agg_expr(func, operand)
    query = base.select(agg_expr, append=False)

    if node.group_by == GroupByMode.SOURCE:
        query = query.group_by(exp.column("source"))

    return query


def _build_agg_expr(func: AggFunc, operand: exp.Expression) -> exp.Expression:
    if func == AggFunc.COUNT:
        return exp.Count(this=operand)
    if func == AggFunc.SUM:
        return exp.Sum(this=operand)
    if func == AggFunc.AVG:
        return exp.Avg(this=operand)
    if func == AggFunc.MIN:
        return exp.Min(this=operand)
    if func == AggFunc.MAX:
        return exp.Max(this=operand)
    if func == AggFunc.COLLECT:
        return exp.ArrayAgg(this=operand)
    if func == AggFunc.FIRST:
        return exp.Anonymous(this="first", expressions=[operand])
    raise UnsupportedDerivationError(f"Unhandled AggFunc: {func}")


@to_sqlglot.register
def _(node: RelationAny) -> exp.Expression:
    """RelationAny → EXISTS (subquery)."""
    sub = to_sqlglot(node.relation)
    return exp.Exists(this=sub)


@to_sqlglot.register
def _(node: RelationAll) -> exp.Expression:
    """RelationAll → NOT EXISTS (subquery with negated body).

    Semantics: every row satisfies the predicate ↔ no row violates it.
    If body is None, just checks that the relation is non-empty (degenerate case).
    """
    base = to_sqlglot(node.relation)
    if node.body is not None:
        negated_body = exp.Not(this=to_sqlglot(node.body))
        base = base.where(negated_body)
    return exp.Not(this=exp.Exists(this=base))


@to_sqlglot.register
def _(node: RelationFirst) -> exp.Expression:
    """RelationFirst → SELECT <project> FROM <relation> [ORDER BY ...] LIMIT 1."""
    base = to_sqlglot(node.relation)
    col = to_sqlglot(node.project)
    query = base.select(col, append=False)

    if node.order_by:
        order_exprs = [exp.Ordered(this=to_sqlglot(ob)) for ob in node.order_by]
        query = query.order_by(*order_exprs)

    return query.limit(1)


@to_sqlglot.register
def _(node: RecursiveTraversal) -> exp.Expression:
    """RecursiveTraversal → raises UnsupportedDerivationError.

    Per sql-generation.md §Expressivity bound: 'Recursive CTEs: REJECTED.'
    Rules whose semantics require recursive CTE must be rejected at compile time.
    """
    raise UnsupportedDerivationError(
        "RecursiveTraversal cannot be lowered to SQL: recursive CTEs are rejected "
        "per sql-generation.md expressivity bound. "
        "Resolve the traversal at the spec layer before SQL emission."
    )


# ---------------------------------------------------------------------------
# Derivation nodes
# ---------------------------------------------------------------------------

@to_sqlglot.register
def _(node: ScalarDerivation) -> exp.Expression:
    """ScalarDerivation → the inner expression directly."""
    return to_sqlglot(node.expression)


@to_sqlglot.register
def _(node: FormatDerivation) -> exp.Expression:
    """FormatDerivation → string concatenation via exp.Concat.

    Template '{last}, {first}' with slots [last_path, first_path] is lowered to
    CONCAT(last, ', ', first).  Slot order in the slots list matches the
    template's positional placeholders left-to-right.
    """
    parts: list[exp.Expression] = []
    # Parse template into literal and slot-ref segments.
    # We replace {} placeholders with slot column refs in order.
    segments = re.split(r"\{[^}]*\}", node.template)
    n_slots = len(node.slots)
    for i, seg in enumerate(segments):
        if seg:
            parts.append(exp.Literal.string(seg))
        if i < n_slots:
            parts.append(to_sqlglot(node.slots[i]))

    if not parts:
        return exp.Literal.string("")
    if len(parts) == 1:
        return parts[0]
    return exp.Concat(expressions=parts)
