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

    # Set-aggregate primitives — treat ``self`` as a predicate over an
    # implicit row-set (the class whose slots appear in the predicate)
    # and quantify over it. The actual primary-class inference happens
    # at compile time.
    def any(self) -> Aggregate:
        """``EXISTS (SELECT 1 FROM … WHERE self)`` — at least one row."""
        return Aggregate(kind="any", predicate=self)

    def none(self) -> Aggregate:
        """``NOT EXISTS (…)`` — no row satisfies ``self``."""
        return Aggregate(kind="none", predicate=self)

    def all(self, condition: Expr) -> Aggregate:
        """``NOT EXISTS (… AND NOT condition)`` — every row in the
        implicit set defined by ``self`` also satisfies ``condition``.
        Vacuously true on the empty set (math-correct default)."""
        return Aggregate(kind="all", predicate=self, condition=condition)

    def count(self) -> Aggregate:
        """``(SELECT COUNT(*) FROM … WHERE self)`` — value-expression,
        comparable: ``predicate.count() > 5``."""
        return Aggregate(kind="count", predicate=self)


def _as_expr(v: Any) -> Expr:
    """Coerce a Python value into an Expr (wrap raw values as Literal)."""
    if isinstance(v, Expr):
        return v
    return Literal(value=v)


# ---------------------------------------------------------------------------
# _ValueExpr — mixin for nodes that can appear in comparisons
# ---------------------------------------------------------------------------


# ``_ValueExpr`` is a mixin combined with ``Expr`` in concrete classes
# (Ref, FkRef, FkChainRef, Literal, CountRel, Aggregate). The operator
# methods build ``Compare`` / ``InList`` / ``IsNull`` / ``Between``
# nodes passing ``self`` where an ``Expr`` is expected — at runtime
# ``self`` IS an Expr via the concrete class's MRO, but mypy can't see
# that. The ``arg-type`` ignores are explicit annotations of that gap.


class _ValueExpr:
    """Mixin for Expr nodes that produce a SQL value (slot ref, literal,
    count-of-relation). Defines comparison + null + range operators that
    each produce a boolean Expr."""

    __slots__ = ()

    def __gt__(self, other: Any) -> Compare:
        return Compare(op=">", left=self, right=_as_expr(other))  # type: ignore[arg-type]

    def __ge__(self, other: Any) -> Compare:
        return Compare(op=">=", left=self, right=_as_expr(other))  # type: ignore[arg-type]

    def __lt__(self, other: Any) -> Compare:
        return Compare(op="<", left=self, right=_as_expr(other))  # type: ignore[arg-type]

    def __le__(self, other: Any) -> Compare:
        return Compare(op="<=", left=self, right=_as_expr(other))  # type: ignore[arg-type]

    def __eq__(self, other: Any) -> Compare:  # type: ignore[override]
        return Compare(op="=", left=self, right=_as_expr(other))  # type: ignore[arg-type]

    def __ne__(self, other: Any) -> Compare:  # type: ignore[override]
        return Compare(op="<>", left=self, right=_as_expr(other))  # type: ignore[arg-type]

    def __hash__(self) -> int:
        return id(self)

    def in_(self, values: list[Any]) -> InList:
        return InList(left=self, values=tuple(values), negated=False)  # type: ignore[arg-type]

    def not_in(self, values: list[Any]) -> InList:
        return InList(left=self, values=tuple(values), negated=True)  # type: ignore[arg-type]

    def is_null(self) -> IsNull:
        return IsNull(expr=self, negated=False)  # type: ignore[arg-type]

    def is_not_null(self) -> IsNull:
        return IsNull(expr=self, negated=True)  # type: ignore[arg-type]

    def between(self, low: Any, high: Any) -> Between:
        return Between(left=self, low=low, high=high)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Concrete Expr types — pure data; rendering lives in knot.compile.expr
# ---------------------------------------------------------------------------


@dataclass(frozen=True, eq=False, slots=True)
class Ref(Expr, _ValueExpr):
    """A reference to a slot on a class. Carries names (not Slot objects)
    to keep the Expr tree independent of the OntologyClass instance."""

    class_name: str
    slot_name: str


@dataclass(frozen=True, eq=False, slots=True)
class VectorRef(Expr, _ValueExpr):
    """A reference to a Vector slot. Adds ``.distance_to(target)`` so
    k-NN sort / filter / select composes with every other Query
    builder. Carries the slot's ``metric`` + ``dim`` so the compiler
    picks the right pgvector operator without a spec lookup."""

    class_name: str
    slot_name: str
    metric: str  # "cosine" | "l2" | "ip"
    dim: int

    def distance_to(self, target: Any) -> VectorDistance:
        """Return a float-valued ``VectorDistance`` expression between
        this slot and ``target``. The operator is picked from the
        slot's declared ``metric`` — ``<=>`` for cosine, ``<->`` for
        l2, ``<#>`` for ip — matching the slot's HNSW index, so
        ``order_by(slot.distance_to(v)).limit(k)`` uses the index.
        Compose freely: chain ``.where(...)`` before for filtering,
        ``.select(slot.distance_to(v))`` to surface the distance, etc."""
        return VectorDistance(
            class_name=self.class_name,
            slot_name=self.slot_name,
            target=tuple(target),
            metric=self.metric,
            dim=self.dim,
        )


@dataclass(frozen=True, slots=True)
class VectorDistance(Expr, _ValueExpr):
    """Float-valued distance between a vector slot and a literal
    target vector, rendered with the operator picked by the slot's
    ``metric``. Composes anywhere a float column-level expression
    does — ``.order_by``, ``.select``, comparison (``> 0.3``), etc.
    HNSW index gets used when this expression drives an ``ORDER BY``
    with a ``LIMIT``."""

    class_name: str
    slot_name: str
    target: tuple[float, ...]
    metric: str
    dim: int


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


@dataclass(frozen=True, eq=False, slots=True)
class AggExpr(Expr, _ValueExpr):
    """SELECT-list aggregate — ``COUNT(*)`` / ``COUNT(col)`` / ``SUM(col)``
    / ``AVG(col)`` / ``MIN(col)`` / ``MAX(col)``. A value-expression
    so it composes with arithmetic + comparison and can land in
    ``Query.select(...)`` or ``Query.order_by(...)``. Pair with
    ``Query.group_by(...)`` for per-group aggregates; without
    ``group_by`` the query reduces to one row (scalar aggregate over
    the whole table).

    Use the module-level factories ``count() / sum_() / avg() /
    min_() / max_()`` to build these — trailing-underscore on
    `sum_/min_/max_` avoids shadowing the Python builtins."""

    kind: str  # "count" | "sum" | "avg" | "min" | "max"
    expr: Expr | None = None  # None ⇒ ``COUNT(*)`` (only valid for count)

    def __post_init__(self) -> None:
        valid = {"count", "sum", "avg", "min", "max"}
        if self.kind not in valid:
            raise ValueError(
                f"AggExpr.kind must be one of {sorted(valid)}, got {self.kind!r}"
            )
        if self.expr is None and self.kind != "count":
            raise ValueError(
                f"AggExpr({self.kind!r}) requires an inner Expr; "
                f"only COUNT(*) is built without one"
            )


def count(expr: Expr | None = None) -> AggExpr:
    """``COUNT(*)`` with no arg; ``COUNT(<expr>)`` with one (counts
    non-NULL values)."""
    return AggExpr(kind="count", expr=expr)


def sum_(expr: Expr) -> AggExpr:
    """``SUM(<expr>)`` over the (grouped or full) row set."""
    return AggExpr(kind="sum", expr=expr)


def avg(expr: Expr) -> AggExpr:
    """``AVG(<expr>)`` — postgres returns numeric for integer input."""
    return AggExpr(kind="avg", expr=expr)


def min_(expr: Expr) -> AggExpr:
    """``MIN(<expr>)``."""
    return AggExpr(kind="min", expr=expr)


def max_(expr: Expr) -> AggExpr:
    """``MAX(<expr>)``."""
    return AggExpr(kind="max", expr=expr)


@dataclass(frozen=True, slots=True)
class Exists(Expr):
    """``EXISTS (SELECT 1 FROM other WHERE other.fk = primary.identifier
    [AND extra-where])``. Produced by the correlated-aggregate form
    ``(other.col.fk == this.Primary).any()`` / ``.none()`` —
    ``negated=True`` yields ``NOT EXISTS``."""

    other_class_name: str
    fk_slot_name: str
    primary_class_name: str
    primary_identifier: str
    where: Expr | None = None
    negated: bool = False


@dataclass(frozen=True, eq=False, slots=True)
class CountRel(Expr, _ValueExpr):
    """``(SELECT COUNT(*) FROM other WHERE other.fk = primary.identifier
    [AND extra-where])``. A value-expression — produced by
    ``(other.col.fk == this.Primary).count()`` and composes with
    comparison operators: ``... .count() >= 1``."""

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


@dataclass(frozen=True, eq=False, slots=True)
class This(Expr, _ValueExpr):
    """Outer-scope binding reference. ``this.Person`` (constructed via
    the magic ``this`` object) renders as the canonical_id of the row
    currently being filtered in the enclosing query.

    Only valid inside an Aggregate predicate; ``class_name`` must match
    the enclosing scope's class (compile-time check)."""

    class_name: str


@dataclass(frozen=True, eq=False, slots=True)
class Aggregate(Expr, _ValueExpr):
    """Set-aggregate over an implicit row-set.

    The "implicit row-set" is defined by ``predicate``: rows of the
    class whose slot refs appear in the predicate. ``kind`` picks the
    aggregation:

    - ``"any"``   → ``EXISTS (SELECT 1 …)`` — boolean
    - ``"none"``  → ``NOT EXISTS (…)`` — boolean
    - ``"count"`` → ``(SELECT COUNT(*) …)`` — value (comparable)
    - ``"all"``   → ``NOT EXISTS (… AND NOT condition)`` — boolean,
      vacuously true on empty set; ``condition`` is required.

    Inherits ``_ValueExpr`` so ``count()`` works in comparisons
    (``.count() > 5``, ``.count() == 0``). Using comparison operators
    on a boolean-kind aggregate (``.any() > 5``) is nonsense SQL —
    not enforced at the type level; surfaces as opaque postgres."""

    kind: str  # "any" | "none" | "count" | "all"
    predicate: Expr
    condition: Expr | None = None

    def __post_init__(self) -> None:
        if self.kind not in ("any", "none", "count", "all"):
            raise ValueError(
                f"Aggregate.kind must be one of any/none/count/all, got {self.kind!r}"
            )
        if self.kind == "all" and self.condition is None:
            raise ValueError("Aggregate.all requires a condition")
        if self.kind != "all" and self.condition is not None:
            raise ValueError(
                f"Aggregate.condition only used with kind='all', not {self.kind!r}"
            )


class _ThisAccess:
    """Builder sugar — ``this.Person`` returns ``This(class_name="Person")``.

    Disambiguates nested scopes: an outer ``Person.where(...)`` enclosing
    a predicate over Movie writes ``this.Person`` to bind the outer row;
    if Movie itself was the outer scope, it'd be ``this.Movie``."""

    def __getattr__(self, name: str) -> This:
        if name.startswith("_"):
            raise AttributeError(name)
        return This(class_name=name)


#: Magic accessor for outer-scope row binding. See ``This``.
this = _ThisAccess()


# Resolve Expr's forward ref to Aggregate now that Aggregate exists.
# (The methods ``Expr.any/none/all/count`` return Aggregate; Python
# resolves the annotation lazily because of ``from __future__ import
# annotations``.)


# ---------------------------------------------------------------------------
# Top-level factories
# ---------------------------------------------------------------------------


def lit(value: Any) -> Literal:
    """Wrap a Python value as an Expr literal."""
    return Literal(value=value)


def raw(sql: str) -> Raw:
    """Escape-hatch SQL fragment. Treats ``sql`` as opaque postgres."""
    return Raw(sql=sql)
