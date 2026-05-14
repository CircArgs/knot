"""Semantic expression builder for constraint bodies and virtual-class
predicates. The builder is the only way to author bodies — no raw SQL
strings cross knot's user surface (the ``raw()`` escape hatch exists
but is deliberately ergonomically ugly).

Every Expr renders to schema-qualified SQL via ``.to_sql(schema=...,
target_suffix=...)`` so the same body can target the canonical table,
the resolved view, or the bindings-current view by flipping suffix.

The spec lives in Python code, not the database — there is no JSON
round-trip for Expr trees. Snapshot testing uses the dataclass-
generated ``repr()`` if structural assertion is wanted.

Construction-time validation: ``OntologyClass.col.year`` raises
``KeyError`` if the slot doesn't exist, so typos surface at spec build
rather than at SQL emission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Expression base
# ---------------------------------------------------------------------------


class Expr:
    """Base class for every node in the expression tree."""

    __slots__ = ()

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        raise NotImplementedError(self)

    # Boolean combinators — every Expr supports these.
    def __and__(self, other: Expr) -> BoolOp:
        return BoolOp(op="AND", left=self, right=_as_expr(other))

    def __or__(self, other: Expr) -> BoolOp:
        return BoolOp(op="OR", left=self, right=_as_expr(other))

    def __invert__(self) -> Not:
        return Not(expr=self)


def _as_expr(v: Any) -> Expr:
    """Coerce a Python value into an Expr (wrap raw values as Literal)."""
    if isinstance(v, Expr):
        return v
    return Literal(value=v)


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


# ---------------------------------------------------------------------------
# _ValueExpr — mixin for nodes that can appear in comparisons
# ---------------------------------------------------------------------------


class _ValueExpr:
    """Mixin for Expr nodes that produce a SQL value (slot ref, literal,
    count-of-relation). Defines comparison + null + range operators that
    each produce a boolean Expr."""

    __slots__ = ()

    def __gt__(self, other: Any) -> Compare:  # type: ignore[misc]
        return Compare(op=">", left=self, right=_as_expr(other))

    def __ge__(self, other: Any) -> Compare:  # type: ignore[misc]
        return Compare(op=">=", left=self, right=_as_expr(other))

    def __lt__(self, other: Any) -> Compare:  # type: ignore[misc]
        return Compare(op="<", left=self, right=_as_expr(other))

    def __le__(self, other: Any) -> Compare:  # type: ignore[misc]
        return Compare(op="<=", left=self, right=_as_expr(other))

    def __eq__(self, other: Any) -> Compare:  # type: ignore[override]
        return Compare(op="=", left=self, right=_as_expr(other))

    def __ne__(self, other: Any) -> Compare:  # type: ignore[override]
        return Compare(op="<>", left=self, right=_as_expr(other))

    def __hash__(self) -> int:  # type: ignore[override]
        return id(self)

    def in_(self, values: list[Any]) -> InList:
        return InList(left=self, values=tuple(values), negated=False)

    def not_in(self, values: list[Any]) -> InList:
        return InList(left=self, values=tuple(values), negated=True)

    def is_null(self) -> IsNull:
        return IsNull(expr=self, negated=False)

    def is_not_null(self) -> IsNull:
        return IsNull(expr=self, negated=True)

    def between(self, low: Any, high: Any) -> Between:
        return Between(left=self, low=low, high=high)


# ---------------------------------------------------------------------------
# Concrete Expr types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False, slots=True)
class Ref(Expr, _ValueExpr):
    """A reference to a slot on a class. Carries names (not Slot objects)
    to keep the Expr tree independent of the OntologyClass instance."""

    class_name: str
    slot_name: str

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return f"{schema}.{self.class_name.lower()}{target_suffix}.{self.slot_name}"


@dataclass(frozen=True, eq=False, slots=True)
class Literal(Expr, _ValueExpr):
    """A constant value, serialized via ``_sql_literal``."""

    value: Any

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return _sql_literal(self.value)


@dataclass(frozen=True, slots=True)
class Compare(Expr):
    """Binary comparison: left <op> right (=, <>, <, <=, >, >=)."""

    op: str
    left: Expr
    right: Expr

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        r = self.right.to_sql(schema=schema, target_suffix=target_suffix)
        return f"{l} {self.op} {r}"


@dataclass(frozen=True, slots=True)
class BoolOp(Expr):
    """Boolean AND/OR of two Exprs. Use ``~`` for NOT (returns ``Not``)."""

    op: str  # "AND" or "OR"
    left: Expr
    right: Expr

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        r = self.right.to_sql(schema=schema, target_suffix=target_suffix)
        return f"({l}) {self.op} ({r})"


@dataclass(frozen=True, slots=True)
class Not(Expr):
    """Boolean negation of an Expr."""

    expr: Expr

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return f"NOT ({self.expr.to_sql(schema=schema, target_suffix=target_suffix)})"


@dataclass(frozen=True, slots=True)
class IsNull(Expr):
    """``<expr> IS NULL`` (or ``IS NOT NULL`` if negated)."""

    expr: Expr
    negated: bool = False

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        op = "IS NOT NULL" if self.negated else "IS NULL"
        return f"{self.expr.to_sql(schema=schema, target_suffix=target_suffix)} {op}"


@dataclass(frozen=True, slots=True)
class InList(Expr):
    """``<expr> IN (...)`` (or ``NOT IN`` if negated)."""

    left: Expr
    values: tuple[Any, ...]
    negated: bool = False

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        vs = ", ".join(_sql_literal(v) for v in self.values)
        op = "NOT IN" if self.negated else "IN"
        return f"{l} {op} ({vs})"


@dataclass(frozen=True, slots=True)
class Between(Expr):
    """``<expr> BETWEEN low AND high``."""

    left: Expr
    low: Any
    high: Any

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        return f"{l} BETWEEN {_sql_literal(self.low)} AND {_sql_literal(self.high)}"


@dataclass(frozen=True, slots=True)
class Exists(Expr):
    """``EXISTS (SELECT 1 FROM other WHERE other.fk = primary.identifier
    [AND extra-where])``. Produced by ``OntologyClass.has_any`` /
    ``has_none``; ``negated=True`` yields ``NOT EXISTS``."""

    other_class_name: str
    fk_slot_name: str
    primary_class_name: str
    primary_identifier: str
    where: Expr | None = None
    negated: bool = False

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        other_table = f"{schema}.{self.other_class_name.lower()}{target_suffix}"
        primary_table = f"{schema}.{self.primary_class_name.lower()}{target_suffix}"
        clauses = [f"{other_table}.{self.fk_slot_name} = {primary_table}.{self.primary_identifier}"]
        if self.where is not None:
            clauses.append(self.where.to_sql(schema=schema, target_suffix=target_suffix))
        prefix = "NOT EXISTS" if self.negated else "EXISTS"
        return f"{prefix} (SELECT 1 FROM {other_table} WHERE {' AND '.join(clauses)})"


@dataclass(frozen=True, eq=False, slots=True)
class CountRel(Expr, _ValueExpr):
    """``(SELECT COUNT(*) FROM other WHERE other.fk = primary.identifier
    [AND extra-where])``. A value-expression — compose with comparison
    operators: ``movie.has_count(credit) >= 1``."""

    other_class_name: str
    fk_slot_name: str
    primary_class_name: str
    primary_identifier: str
    where: Expr | None = None

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        other_table = f"{schema}.{self.other_class_name.lower()}{target_suffix}"
        primary_table = f"{schema}.{self.primary_class_name.lower()}{target_suffix}"
        clauses = [f"{other_table}.{self.fk_slot_name} = {primary_table}.{self.primary_identifier}"]
        if self.where is not None:
            clauses.append(self.where.to_sql(schema=schema, target_suffix=target_suffix))
        return f"(SELECT COUNT(*) FROM {other_table} WHERE {' AND '.join(clauses)})"


@dataclass(frozen=True, slots=True)
class Raw(Expr):
    """Escape hatch — arbitrary SQL fragment. The user owns its
    correctness; knot does not parse, validate, or rewrite it. Use
    sparingly for postgres-specific constructs the builder doesn't
    model (window funcs, CTEs, JSON ops, custom functions)."""

    sql: str

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return self.sql


# ---------------------------------------------------------------------------
# Top-level factories
# ---------------------------------------------------------------------------


def lit(value: Any) -> Literal:
    """Wrap a Python value as an Expr literal."""
    return Literal(value=value)


def raw(sql: str) -> Raw:
    """Escape-hatch SQL fragment. Treats ``sql`` as opaque postgres."""
    return Raw(sql=sql)


__all__ = [
    "Expr",
    "Ref",
    "Literal",
    "Compare",
    "BoolOp",
    "Not",
    "IsNull",
    "InList",
    "Between",
    "Exists",
    "CountRel",
    "Raw",
    "lit",
    "raw",
]
