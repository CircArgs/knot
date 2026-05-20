"""Read substrate — SELECT-shape AST nodes.

Pure data dataclasses describing queries over the Spec. Rendering
lives in ``knot/compile/query_sql.py`` (singledispatch sibling of
``compile_sql``).

The user surface is fluent immutable: every builder method on
``Query`` (``.where()``, ``.order_by()``, ``.limit()``, etc.) returns
a new ``Query`` — the AST is never mutated. ``OntologyClass`` exposes
four explicit layer-targeted entry points that *return* a ``Query``:
``cls.resolved`` (argmax view), ``cls.all_sources`` (per-source jsonb
provenance), ``cls.from_source(s)`` (one source's raw bindings), and
``cls.unresolved`` (bindings still waiting on ER, across every
source). There is no default — every read declares its layer.

The AST carries ``class_name`` as a string (same convention as ``Ref``
in ``knot.ast.expr``) so the node itself is decoupled from
``OntologyClass`` instances. A ``Query`` constructed via the class-side
entry points also holds a private back-reference to its owning ``Spec``
so ``q.sql(schema=...)`` compiles itself end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from knot.ast.expr import Expr

if TYPE_CHECKING:
    from knot.spec import Spec


class Layer(StrEnum):
    """Which postgres relation a ``Query`` (or constraint validation, or
    DDL view body) targets. The enum value is the literal table-name
    suffix the compiler appends after ``<schema>.<class>`` — typing the
    layer instead of a bare string moves typos from runtime
    "relation does not exist" errors to construction-time failures."""

    RESOLVED = "_resolved"
    ALL_SOURCES = "_all_sources"
    BINDINGS = "_bindings"
    CANONICAL = ""


@dataclass(frozen=True, slots=True)
class OrderBy:
    """One ORDER BY clause — a ref expression and a direction."""

    ref: Expr
    direction: str = "asc"

    def __post_init__(self) -> None:
        if self.direction not in ("asc", "desc"):
            raise ValueError(
                f"OrderBy direction must be 'asc' or 'desc', got {self.direction!r}"
            )


@dataclass(frozen=True, slots=True)
class Query:
    """A SELECT-shape query over one primary class.

    Fields with trailing underscores avoid name collisions with the
    fluent builder methods (``.where``/``.order_by``/etc.). Frozen +
    fluent: every builder returns a new ``Query`` via ``replace``.

    ``_spec`` is a back-reference set by the class-side entry points
    (``OntologyClass.resolved`` / ``.all_sources`` / ``.from_source`` /
    ``.unresolved``) so ``q.sql(schema=...)`` knows which spec to
    compile against. It's
    intentionally private and excluded from repr/compare so the AST
    still behaves like pure data for tests and equality checks.
    """

    class_name: str
    where_clause: Expr | None = None
    grouping: tuple[Expr, ...] = ()
    ordering: tuple[OrderBy, ...] = ()
    limit_value: int | None = None
    offset_value: int | None = None
    projection: tuple[Expr, ...] | None = None
    layer: Layer = Layer.RESOLVED
    lock_mode: str | None = None  # "for_update" | "for_update_skip_locked" | "for_share"
    _spec: Any = field(default=None, repr=False, compare=False)

    def where(self, predicate: Expr) -> Query:
        """AND ``predicate`` into the existing WHERE clause."""
        combined = (
            predicate if self.where_clause is None else self.where_clause & predicate
        )
        return replace(self, where_clause=combined)

    def group_by(self, *refs: Expr) -> Query:
        """Append GROUP BY clauses. Pair with aggregate projections
        (``count()`` / ``sum_()`` / ``avg()`` / etc. from
        ``knot.ast.expr``). Without ``group_by``, an aggregate in
        ``.select()`` reduces the result to one scalar row."""
        return replace(self, grouping=self.grouping + tuple(refs))

    def order_by(self, ref: Expr, direction: str = "asc") -> Query:
        """Append an ORDER BY clause."""
        return replace(self, ordering=self.ordering + (OrderBy(ref, direction),))

    def limit(self, n: int) -> Query:
        return replace(self, limit_value=n)

    def offset(self, n: int) -> Query:
        return replace(self, offset_value=n)

    def select(self, *refs: Expr) -> Query:
        """Set the projection. ``None`` (the default) means ``SELECT *``."""
        return replace(self, projection=tuple(refs))

    def lock(self, mode: str) -> Query:
        """Append a row-lock clause. Postgres modes:

          - ``"for_update"`` — exclusive row lock
          - ``"for_update_skip_locked"`` — exclusive lock, skip rows
            already locked by another transaction. The canonical ER
            worker shape: claim a batch of ``cls.unresolved`` rows
            atomically without blocking on or re-processing rows
            another worker has already claimed.
          - ``"for_share"`` — shared row lock

        Renders after LIMIT / OFFSET (postgres clause order)."""
        valid = {"for_update", "for_update_skip_locked", "for_share"}
        if mode not in valid:
            raise ValueError(
                f"Query.lock mode must be one of {sorted(valid)}, got {mode!r}"
            )
        return replace(self, lock_mode=mode)

    # ------------------------------------------------------------------
    # Compile entry point — methods on the entity they're about. The
    # Spec back-reference lets the host call ``q.sql(...)`` directly.
    # ------------------------------------------------------------------

    def sql(self) -> str:
        """Compile this query to a postgres SQL string against its owning
        spec — schema name comes from the spec. Literals are inlined;
        no positional-parameter list to bind. No validation (hot path).
        Raises ``RuntimeError`` if this Query wasn't built via a spec's
        class (no back-reference)."""
        if self._spec is None:
            raise RuntimeError(
                "Query has no spec back-reference — build it via "
                "cls.resolved / .all_sources / .from_source(...) / "
                ".unresolved, or use knot.compile.query.compile_query"
                "(q, spec=spec) directly"
            )
        from knot.compile.query import compile_query

        return compile_query(self, spec=self._spec, schema=self._spec.schema)

    def with_spec(self, spec: Spec) -> Query:
        """Return a copy of this query bound to ``spec``. Useful when
        the AST was constructed directly (``Query(class_name=...)``)
        and you want to attach a spec after the fact."""
        return replace(self, _spec=spec)
