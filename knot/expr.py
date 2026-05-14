"""Semantic expression substrate. Pure data — the tree describes shape,
not rendering. Compilation (today: postgres SQL; future: cypher,
in-process evaluator, typed IR) lives in dedicated single-dispatch
tables under ``knot.compile``.

The builder is the only way to author bodies — no raw SQL strings cross
knot's user surface (the ``raw()`` escape hatch exists but is
deliberately ergonomically ugly).

Construction-time validation: ``OntologyClass.col.year`` raises
``KeyError`` if the slot doesn't exist, so typos surface at spec build
rather than at compilation.
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
# Concrete Expr types — pure data; rendering lives in knot.compile.expr_sql
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False, slots=True)
class Ref(Expr, _ValueExpr):
    """A reference to a slot on a class. Carries names (not Slot objects)
    to keep the Expr tree independent of the OntologyClass instance."""

    class_name: str
    slot_name: str


@dataclass(frozen=True, eq=False, slots=True)
class FkRef(Expr, _ValueExpr):
    """A reference to a FK slot. Used as a value, it renders as the FK
    column on the source table (e.g., ``Movie.col.director == X``).
    Used as a navigator, ``Movie.col.director.name`` returns a
    ``FkChainRef`` representing the joined ref into the target class.

    ``target_class_name`` is carried so navigation knows where to point
    the chain. Validation of the target slot's existence is deferred to
    compile time (this dataclass stays pure data, no spec access)."""

    class_name: str
    slot_name: str
    target_class_name: str

    def __getattr__(self, attr: str) -> FkChainRef:
        # Only triggered for unknown attrs — dataclass slots short-circuit.
        if attr.startswith("_"):
            raise AttributeError(attr)
        return FkChainRef(
            source_class=self.class_name,
            chain=((self.slot_name, self.target_class_name),),
            terminal_slot=attr,
        )


@dataclass(frozen=True, eq=False, slots=True)
class FkChainRef(Expr, _ValueExpr):
    """A reference reached by walking one or more FK hops to a terminal
    slot on the final class. The compiler emits the necessary JOINs at
    query-render time; this node just records the path.

    Example — ``Movie.col.director.name``::

        source_class  = "Movie"
        chain         = (("director", "Person"),)
        terminal_slot = "name"

    Multi-hop chains carry more steps in ``chain``. Validation of slot
    existence and FK-ness happens at compile time."""

    source_class: str
    chain: tuple[tuple[str, str], ...]  # ((fk_slot_name, target_class_name), ...)
    terminal_slot: str


@dataclass(frozen=True, eq=False, slots=True)
class Literal(Expr, _ValueExpr):
    """A constant Python value (str / int / float / bool / None / list / tuple)."""

    value: Any


@dataclass(frozen=True, slots=True)
class Compare(Expr):
    """Binary comparison: left <op> right (=, <>, <, <=, >, >=)."""

    op: str
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class BoolOp(Expr):
    """Boolean AND/OR of two Exprs. Use ``~`` for NOT (returns ``Not``)."""

    op: str  # "AND" or "OR"
    left: Expr
    right: Expr


@dataclass(frozen=True, slots=True)
class Not(Expr):
    """Boolean negation of an Expr."""

    expr: Expr


@dataclass(frozen=True, slots=True)
class IsNull(Expr):
    """``<expr> IS NULL`` (or ``IS NOT NULL`` if negated)."""

    expr: Expr
    negated: bool = False


@dataclass(frozen=True, slots=True)
class InList(Expr):
    """``<expr> IN (...)`` (or ``NOT IN`` if negated)."""

    left: Expr
    values: tuple[Any, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class Between(Expr):
    """``<expr> BETWEEN low AND high``."""

    left: Expr
    low: Any
    high: Any


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


@dataclass(frozen=True, slots=True)
class Raw(Expr):
    """Escape hatch — arbitrary SQL fragment. The user owns its
    correctness; knot does not parse, validate, or rewrite it. Use
    sparingly for postgres-specific constructs the builder doesn't
    model (window funcs, CTEs, JSON ops, custom functions)."""

    sql: str


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
    "FkRef",
    "FkChainRef",
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
