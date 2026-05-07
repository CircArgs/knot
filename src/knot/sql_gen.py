"""SQL emitter for knot expression trees (per `sql-generation.md`).

Single-dispatch visitor over the metaschema's expression tree, emitting
sqlglot AST nodes that render to dialect-specific SQL (DuckDB, Trino, Spark).

Hard rules:
- Real sqlglot AST throughout — no raw SQL string assembly outside the
  per-dialect emitters.
- `RecursiveTraversal` raises `UnsupportedDerivationError` at SQL-gen time
  (recursive CTEs rejected per the expressivity bound).
- The class context for `SlotPath.from_class == _sentinel_class` is left
  unqualified; the surrounding query (DataContext.primary or
  Constraint.primary) qualifies the columns at fulfill time.
"""

from __future__ import annotations

import re
from functools import singledispatch
from typing import Any

from sqlglot import exp

from knot.metaschema import (
    AggFunc,
    Between,
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
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
    SlotPath,
    Within,
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class UnsupportedDerivationError(Exception):
    """Raised when an expression-tree node cannot be lowered to SQL."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def emit_sql(node: Any, *, dialect: str = "duckdb") -> str:
    """Render an expression-tree node as SQL for the given dialect."""
    return to_sqlglot(node).sql(dialect=dialect)


def emit_validation_query(constraint: Constraint, *, dialect: str = "duckdb") -> str:
    """Compile a `Constraint` to an offender-row SELECT in the uniform shape:
    `(rule_id, class_name, slot_name, offending_pk, detail)`.

    Empty result = pass.  See `sql-generation.md` § "Validation SQL shape".
    """
    body_sql = to_sqlglot(constraint.body)
    rule_id    = exp.alias_(exp.Literal.string(constraint.name),       "rule_id")
    class_name = exp.alias_(exp.Literal.string(constraint.primary.name), "class_name")
    slot_name  = exp.alias_(exp.Null(),                                  "slot_name")
    pk         = exp.alias_(exp.column("canonical_id"),                  "offending_pk")
    detail     = exp.alias_(exp.Literal.string(constraint.message or ""),"detail")

    table = constraint.primary.name.lower()
    query = (
        exp.select(rule_id, class_name, slot_name, pk, detail)
           .from_(table)
           .where(exp.Not(this=body_sql))
    )
    return query.sql(dialect=dialect)


def emit_trust_resolved_cte(class_: OntologyClass, *, dialect: str = "duckdb") -> exp.Expression:
    """Build the `WITH __trust_resolved__<Class> AS (...)` CTE that applies
    each slot's `ResolutionPolicy` over `resolved_facts_<Class>`.

    Per `sql-generation.md` § "Trust-resolution placeholder".
    """
    cte_name = f"__trust_resolved__{class_.name}"
    table_name = f"resolved_facts_{class_.name.lower()}"

    select_exprs: list[exp.Expression] = [exp.column("canonical_id")]
    for slot in class_.slots:
        if slot.derivation is not None:
            continue  # derived slots aren't stored in resolved_facts
        policy = slot.resolution_policy
        if isinstance(policy, str):
            policy = ResolutionPolicy(policy)
        reduction = _policy_reduction(slot.name, policy, dialect)
        select_exprs.append(exp.alias_(reduction, slot.name))

    cte_select = (
        exp.select(*select_exprs)
           .from_(table_name)
           .group_by(exp.column("canonical_id"))
    )
    cte = exp.CTE(
        this=cte_select,
        alias=exp.TableAlias(this=exp.to_identifier(cte_name)),
    )
    return exp.With(expressions=[cte])


# ---------------------------------------------------------------------------
# Per-policy trust reduction
# ---------------------------------------------------------------------------

def _argmax(value: exp.Expression, key: exp.Expression, dialect: str) -> exp.Expression:
    """`argmax(value, key)` — Trino/DuckDB; `max_by(value, key)` for Spark."""
    fn = "max_by" if dialect == "spark" else "argmax"
    return exp.Anonymous(this=fn, expressions=[value, key])


def _mode_expr(value: exp.Expression, dialect: str) -> exp.Expression:
    """Modal value of a column."""
    if dialect == "duckdb":
        return exp.Anonymous(this="mode", expressions=[value])
    return exp.WithinGroup(
        this=exp.Anonymous(this="mode", expressions=[]),
        expression=exp.Order(expressions=[exp.Ordered(this=value)]),
    )


def _percentile_cont(value: exp.Expression) -> exp.Expression:
    return exp.WithinGroup(
        this=exp.Anonymous(this="percentile_cont", expressions=[exp.Literal.number("0.5")]),
        expression=exp.Order(expressions=[exp.Ordered(this=value)]),
    )


def _policy_reduction(slot_name: str, policy: ResolutionPolicy, dialect: str) -> exp.Expression:
    """Map a `ResolutionPolicy` to the per-slot SQL reduction.

    Assumes `resolved_facts_<Class>` carries `<slot>_value` and `<slot>_trust`
    columns plus a row-level `asserted_at`.
    """
    val = exp.column(f"{slot_name}_value")
    trust = exp.column(f"{slot_name}_trust")
    at = exp.column("asserted_at")

    if policy == ResolutionPolicy.ARGMAX_TRUST:
        return _argmax(val, trust, dialect)
    if policy == ResolutionPolicy.MODE:
        return _mode_expr(val, dialect)
    if policy == ResolutionPolicy.WEIGHTED_VOTE:
        return _argmax(val, exp.Sum(this=trust), dialect)
    if policy == ResolutionPolicy.MEDIAN_NUMERIC:
        return _percentile_cont(val)
    if policy == ResolutionPolicy.LATEST_WATERMARK:
        return _argmax(val, at, dialect)
    if policy == ResolutionPolicy.UNIQUE_OR_FAIL:
        count_distinct = exp.Count(this=exp.Distinct(expressions=[val]))
        return exp.Case(
            ifs=[exp.If(
                this=exp.GT(this=count_distinct, expression=exp.Literal.number(1)),
                true=exp.Anonymous(
                    this="error",
                    expressions=[exp.Literal.string(f"{slot_name}: disagreement")],
                ),
            )],
            default=exp.Anonymous(this="first", expressions=[val]),
        )
    raise UnsupportedDerivationError(f"Unknown ResolutionPolicy: {policy}")


# ---------------------------------------------------------------------------
# Visitor: to_sqlglot
# ---------------------------------------------------------------------------

@singledispatch
def to_sqlglot(node: Any) -> exp.Expression:
    """Convert any expression-tree node to a sqlglot AST node.

    Unhandled types raise `NotImplementedError` so newly-added metaschema
    nodes are caught immediately (commitment 16: loud failures).
    """
    raise NotImplementedError(
        f"No to_sqlglot handler for {type(node).__name__}. "
        "Add a @to_sqlglot.register handler in knot/sql_gen.py."
    )


# ---------------------------------------------------------------------------
# Leaves
# ---------------------------------------------------------------------------

@to_sqlglot.register
def _(node: Literal_) -> exp.Expression:
    v = node.value
    if v is None:
        return exp.Null()
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float)):
        return exp.Literal.number(v)
    if isinstance(v, list):
        return exp.Tuple(expressions=[
            _scalar_literal(x) for x in v
        ])
    return exp.Literal.string(str(v))


def _scalar_literal(v: Any) -> exp.Expression:
    if v is None:
        return exp.Null()
    if isinstance(v, bool):
        return exp.Boolean(this=v)
    if isinstance(v, (int, float)):
        return exp.Literal.number(v)
    return exp.Literal.string(str(v))


@to_sqlglot.register
def _(node: SlotPath) -> exp.Expression:
    """SlotPath → exp.Column qualified by table when from_class is real.

    Multi-slot paths surface only the terminal column; JOIN chains live at
    the relation level (`RelationRef` / `FilteredRelation`).
    """
    table = None
    if node.from_class.name != "__sentinel__":
        table = node.from_class.name.lower()
    if not node.slots:
        return exp.column("*", table=table) if table else exp.Star()
    return exp.column(node.slots[-1].name, table=table)


# ---------------------------------------------------------------------------
# Predicates
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
    left = to_sqlglot(node.left)
    op = node.op if isinstance(node.op, CompareOp) else CompareOp(node.op)

    if op == CompareOp.IS_NULL:
        return exp.Is(this=left, expression=exp.Null())
    if op == CompareOp.IS_NOT_NULL:
        return exp.Not(this=exp.Is(this=left, expression=exp.Null()))

    right = to_sqlglot(node.right) if node.right is not None else exp.Null()

    if op == CompareOp.IN:
        items = right.expressions if isinstance(right, exp.Tuple) else [right]
        return exp.In(this=left, expressions=items)
    if op == CompareOp.NOT_IN:
        items = right.expressions if isinstance(right, exp.Tuple) else [right]
        return exp.Not(this=exp.In(this=left, expressions=items))

    cls = _COMPARE_OP_MAP.get(op)
    if cls is None:
        raise UnsupportedDerivationError(f"Unhandled CompareOp: {op}")
    return cls(this=left, expression=right)


@to_sqlglot.register
def _(node: BoolExpr) -> exp.Expression:
    op = node.op if isinstance(node.op, BoolOpKind) else BoolOpKind(node.op)
    operands = [to_sqlglot(o) for o in node.operands]

    if op == BoolOpKind.NOT:
        if len(operands) != 1:
            raise UnsupportedDerivationError("BoolExpr NOT must have exactly one operand")
        return exp.Not(this=operands[0])

    join_cls = exp.And if op == BoolOpKind.AND else exp.Or
    result = operands[0]
    for part in operands[1:]:
        result = join_cls(this=result, expression=part)
    return result


@to_sqlglot.register
def _(node: Within) -> exp.Expression:
    return exp.In(
        this=to_sqlglot(node.left),
        expressions=[to_sqlglot(v) for v in node.values],
    )


@to_sqlglot.register
def _(node: Between) -> exp.Expression:
    left = to_sqlglot(node.left)
    low = to_sqlglot(node.lower)
    high = to_sqlglot(node.upper)
    if node.inclusive:
        return exp.Between(this=left, low=low, high=high)
    return exp.And(
        this=exp.GT(this=left, expression=low),
        expression=exp.LT(this=left, expression=high),
    )


_REGEX_META = re.compile(r"[\^$.*+?{}\[\]|()\\]")


@to_sqlglot.register
def _(node: Matches) -> exp.Expression:
    """LIKE for SQL wildcards; regex via sqlglot's RegexpLike for regex patterns."""
    left = to_sqlglot(node.left)
    pat = node.pattern
    if _REGEX_META.search(pat):
        return exp.RegexpLike(this=left, expression=exp.Literal.string(pat))
    return exp.Like(this=left, expression=exp.Literal.string(pat))


# ---------------------------------------------------------------------------
# Relation traversal
# ---------------------------------------------------------------------------

@to_sqlglot.register
def _(node: RelationRef) -> exp.Expression:
    """RelationRef → SELECT * FROM <class_table> (lowercase class name)."""
    table = node.from_class.name.lower()
    return exp.select(exp.Star()).from_(table)


@to_sqlglot.register
def _(node: FilteredRelation) -> exp.Expression:
    base = to_sqlglot(node.relation)
    where_expr = to_sqlglot(node.filter)
    return base.where(where_expr)


@to_sqlglot.register
def _(node: RelationProject) -> exp.Expression:
    base = to_sqlglot(node.relation)
    return base.select(to_sqlglot(node.project), append=False)


@to_sqlglot.register
def _(node: RelationCount) -> exp.Expression:
    base = to_sqlglot(node.relation)
    if node.distinct:
        cnt = exp.Count(this=exp.Distinct(expressions=[exp.Star()]))
    else:
        cnt = exp.Count(this=exp.Star())
    return base.select(cnt, append=False)


def _build_agg_expr(func: AggFunc, operand: exp.Expression) -> exp.Expression:
    if func == AggFunc.COUNT:   return exp.Count(this=operand)
    if func == AggFunc.SUM:     return exp.Sum(this=operand)
    if func == AggFunc.AVG:     return exp.Avg(this=operand)
    if func == AggFunc.MIN:     return exp.Min(this=operand)
    if func == AggFunc.MAX:     return exp.Max(this=operand)
    if func == AggFunc.COLLECT: return exp.ArrayAgg(this=operand)
    if func == AggFunc.FIRST:   return exp.Anonymous(this="first", expressions=[operand])
    raise UnsupportedDerivationError(f"Unhandled AggFunc: {func}")


@to_sqlglot.register
def _(node: RelationAggregate) -> exp.Expression:
    base = to_sqlglot(node.relation)
    func = node.func if isinstance(node.func, AggFunc) else AggFunc(node.func)

    if node.operand is not None:
        operand = to_sqlglot(node.operand)
        if node.distinct:
            operand = exp.Distinct(expressions=[operand])
    else:
        operand = exp.Star()

    agg = _build_agg_expr(func, operand)
    query = base.select(agg, append=False)
    if node.group_by == GroupByMode.SOURCE:
        query = query.group_by(exp.column("source"))
    return query


@to_sqlglot.register
def _(node: RelationAny) -> exp.Expression:
    return exp.Exists(this=to_sqlglot(node.relation))


@to_sqlglot.register
def _(node: RelationAll) -> exp.Expression:
    """`forall p` → `NOT EXISTS (NOT p)` — every row satisfies p iff none violates."""
    base = to_sqlglot(node.relation)
    if node.body is not None:
        base = base.where(exp.Not(this=to_sqlglot(node.body)))
    return exp.Not(this=exp.Exists(this=base))


@to_sqlglot.register
def _(node: RelationFirst) -> exp.Expression:
    base = to_sqlglot(node.relation)
    query = base.select(to_sqlglot(node.project), append=False)
    if node.order_by:
        query = query.order_by(*[exp.Ordered(this=to_sqlglot(ob)) for ob in node.order_by])
    return query.limit(1)


@to_sqlglot.register
def _(node: RecursiveTraversal) -> exp.Expression:
    """Recursive CTEs are rejected per `sql-generation.md` § "Expressivity bound"."""
    raise UnsupportedDerivationError(
        "RecursiveTraversal cannot be lowered to SQL: recursive CTEs rejected. "
        "Resolve traversal at the spec layer (or in a Translator impl for non-lake targets)."
    )


# ---------------------------------------------------------------------------
# Derivation top-level nodes
# ---------------------------------------------------------------------------

@to_sqlglot.register
def _(node: ScalarDerivation) -> exp.Expression:
    return to_sqlglot(node.expression)


@to_sqlglot.register
def _(node: FormatDerivation) -> exp.Expression:
    """Template `'{last}, {first}'` + slots → `CONCAT(last, ', ', first)`."""
    parts: list[exp.Expression] = []
    segments = re.split(r"\{[^}]*\}", node.template)
    n = len(node.slots)
    for i, seg in enumerate(segments):
        if seg:
            parts.append(exp.Literal.string(seg))
        if i < n:
            parts.append(to_sqlglot(node.slots[i]))
    if not parts:
        return exp.Literal.string("")
    if len(parts) == 1:
        return parts[0]
    return exp.Concat(expressions=parts)
