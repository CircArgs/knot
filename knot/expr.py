"""Semantic expression builder for constraint bodies and virtual-class
predicates. The builder is the only way to author bodies — no raw SQL
strings cross knot's user surface (the ``raw()`` escape hatch exists
but is deliberately ergonomically ugly).

Every Expr renders to schema-qualified SQL via ``.to_sql(schema=...,
target_suffix=...)`` so the same body can target the canonical table,
the resolved view, or the bindings-current view by flipping suffix.

Round-trips through ``knot_meta.*`` via ``.to_json()`` /
``Expr.from_json()`` — the body's structural form is preserved, so the
migration emitter sees structural changes rather than text diffs.

Construction-time validation: ``OntologyClass.col.year`` raises
``KeyError`` if the slot doesn't exist, so typos surface at spec build
rather than at SQL emission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Expression base + serialization registry
# ---------------------------------------------------------------------------


class Expr:
    """Base class for every node in the expression tree."""

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        raise NotImplementedError(self)

    def to_json(self) -> dict[str, Any]:
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
    if v is None:
        return "NULL"
    if v is True:
        return "TRUE"
    if v is False:
        return "FALSE"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (list, tuple)):
        return "ARRAY[" + ", ".join(_sql_literal(x) for x in v) + "]"
    raise TypeError(f"can't serialize {type(v).__name__} as SQL literal: {v!r}")


# ---------------------------------------------------------------------------
# _ValueExpr — mixin for nodes that can appear in comparisons
# ---------------------------------------------------------------------------


class _ValueExpr:
    """Mixin for Expr nodes that produce a SQL value (slot ref, literal,
    count-of-relation). Defines comparison + null + range operators that
    each produce a boolean Expr."""

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


@dataclass(frozen=True, eq=False)
class Ref(Expr, _ValueExpr):
    """A reference to a slot on a class. Carries names (not Slot objects)
    to keep the Expr tree independent of the OntologyClass instance."""

    class_name: str
    slot_name: str

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return f"{schema}.{self.class_name.lower()}{target_suffix}.{self.slot_name}"

    def to_json(self) -> dict[str, Any]:
        return {"kind": "ref", "class": self.class_name, "slot": self.slot_name}


@dataclass(frozen=True, eq=False)
class Literal(Expr, _ValueExpr):
    """A constant value, serialized via ``_sql_literal``."""

    value: Any

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return _sql_literal(self.value)

    def to_json(self) -> dict[str, Any]:
        return {"kind": "literal", "value": self.value}


@dataclass(frozen=True)
class Compare(Expr):
    """Binary comparison: left <op> right (=, <>, <, <=, >, >=)."""

    op: str
    left: Expr
    right: Expr

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        r = self.right.to_sql(schema=schema, target_suffix=target_suffix)
        return f"{l} {self.op} {r}"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "compare",
            "op": self.op,
            "left": self.left.to_json(),
            "right": self.right.to_json(),
        }


@dataclass(frozen=True)
class BoolOp(Expr):
    """Boolean AND/OR of two Exprs. Use ``~`` for NOT (returns ``Not``)."""

    op: str  # "AND" or "OR"
    left: Expr
    right: Expr

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        r = self.right.to_sql(schema=schema, target_suffix=target_suffix)
        return f"({l}) {self.op} ({r})"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "bool_op",
            "op": self.op,
            "left": self.left.to_json(),
            "right": self.right.to_json(),
        }


@dataclass(frozen=True)
class Not(Expr):
    """Boolean negation of an Expr."""

    expr: Expr

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return f"NOT ({self.expr.to_sql(schema=schema, target_suffix=target_suffix)})"

    def to_json(self) -> dict[str, Any]:
        return {"kind": "not", "expr": self.expr.to_json()}


@dataclass(frozen=True)
class IsNull(Expr):
    """``<expr> IS NULL`` (or ``IS NOT NULL`` if negated)."""

    expr: Expr
    negated: bool = False

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        op = "IS NOT NULL" if self.negated else "IS NULL"
        return f"{self.expr.to_sql(schema=schema, target_suffix=target_suffix)} {op}"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "is_null",
            "expr": self.expr.to_json(),
            "negated": self.negated,
        }


@dataclass(frozen=True)
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

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "in_list",
            "left": self.left.to_json(),
            "values": list(self.values),
            "negated": self.negated,
        }


@dataclass(frozen=True)
class Between(Expr):
    """``<expr> BETWEEN low AND high``."""

    left: Expr
    low: Any
    high: Any

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        l = self.left.to_sql(schema=schema, target_suffix=target_suffix)
        return f"{l} BETWEEN {_sql_literal(self.low)} AND {_sql_literal(self.high)}"

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "between",
            "left": self.left.to_json(),
            "low": self.low,
            "high": self.high,
        }


@dataclass(frozen=True)
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
        clauses = [
            f"{other_table}.{self.fk_slot_name} = "
            f"{primary_table}.{self.primary_identifier}"
        ]
        if self.where is not None:
            clauses.append(
                self.where.to_sql(schema=schema, target_suffix=target_suffix)
            )
        prefix = "NOT EXISTS" if self.negated else "EXISTS"
        return (
            f"{prefix} (SELECT 1 FROM {other_table} "
            f"WHERE {' AND '.join(clauses)})"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "exists",
            "other": self.other_class_name,
            "fk_slot": self.fk_slot_name,
            "primary": self.primary_class_name,
            "primary_identifier": self.primary_identifier,
            "where": self.where.to_json() if self.where is not None else None,
            "negated": self.negated,
        }


@dataclass(frozen=True, eq=False)
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
        clauses = [
            f"{other_table}.{self.fk_slot_name} = "
            f"{primary_table}.{self.primary_identifier}"
        ]
        if self.where is not None:
            clauses.append(
                self.where.to_sql(schema=schema, target_suffix=target_suffix)
            )
        return (
            f"(SELECT COUNT(*) FROM {other_table} "
            f"WHERE {' AND '.join(clauses)})"
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "kind": "count_rel",
            "other": self.other_class_name,
            "fk_slot": self.fk_slot_name,
            "primary": self.primary_class_name,
            "primary_identifier": self.primary_identifier,
            "where": self.where.to_json() if self.where is not None else None,
        }


@dataclass(frozen=True)
class Raw(Expr):
    """Escape hatch — arbitrary SQL fragment. The user owns its
    correctness; knot does not parse, validate, or rewrite it. Use
    sparingly for postgres-specific constructs the builder doesn't
    model (window funcs, CTEs, JSON ops, custom functions)."""

    sql: str

    def to_sql(self, *, schema: str, target_suffix: str) -> str:
        return self.sql

    def to_json(self) -> dict[str, Any]:
        return {"kind": "raw", "sql": self.sql}


# ---------------------------------------------------------------------------
# Top-level factories
# ---------------------------------------------------------------------------


def lit(value: Any) -> Literal:
    """Wrap a Python value as an Expr literal."""
    return Literal(value=value)


def raw(sql: str) -> Raw:
    """Escape-hatch SQL fragment. Treats ``sql`` as opaque postgres."""
    return Raw(sql=sql)


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------


_FROM_JSON: dict[str, Any] = {}


def _register(kind: str):
    def decorator(fn):
        _FROM_JSON[kind] = fn
        return fn

    return decorator


@_register("ref")
def _ref_from_json(d):
    return Ref(class_name=d["class"], slot_name=d["slot"])


@_register("literal")
def _literal_from_json(d):
    return Literal(value=d["value"])


@_register("compare")
def _compare_from_json(d):
    return Compare(
        op=d["op"],
        left=Expr.from_json(d["left"]),
        right=Expr.from_json(d["right"]),
    )


@_register("bool_op")
def _bool_op_from_json(d):
    return BoolOp(
        op=d["op"],
        left=Expr.from_json(d["left"]),
        right=Expr.from_json(d["right"]),
    )


@_register("not")
def _not_from_json(d):
    return Not(expr=Expr.from_json(d["expr"]))


@_register("is_null")
def _is_null_from_json(d):
    return IsNull(expr=Expr.from_json(d["expr"]), negated=d.get("negated", False))


@_register("in_list")
def _in_list_from_json(d):
    return InList(
        left=Expr.from_json(d["left"]),
        values=tuple(d["values"]),
        negated=d.get("negated", False),
    )


@_register("between")
def _between_from_json(d):
    return Between(left=Expr.from_json(d["left"]), low=d["low"], high=d["high"])


@_register("exists")
def _exists_from_json(d):
    where = Expr.from_json(d["where"]) if d.get("where") is not None else None
    return Exists(
        other_class_name=d["other"],
        fk_slot_name=d["fk_slot"],
        primary_class_name=d["primary"],
        primary_identifier=d["primary_identifier"],
        where=where,
        negated=d.get("negated", False),
    )


@_register("count_rel")
def _count_rel_from_json(d):
    where = Expr.from_json(d["where"]) if d.get("where") is not None else None
    return CountRel(
        other_class_name=d["other"],
        fk_slot_name=d["fk_slot"],
        primary_class_name=d["primary"],
        primary_identifier=d["primary_identifier"],
        where=where,
    )


@_register("raw")
def _raw_from_json(d):
    return Raw(sql=d["sql"])


def _from_json(d: dict[str, Any]) -> Expr:
    kind = d["kind"]
    if kind not in _FROM_JSON:
        raise ValueError(f"unknown Expr kind: {kind!r}")
    return _FROM_JSON[kind](d)


Expr.from_json = staticmethod(_from_json)  # type: ignore[attr-defined]


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
