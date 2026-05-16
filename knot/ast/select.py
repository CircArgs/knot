"""Read substrate — SELECT-shape AST nodes.

Pure data dataclasses describing queries over the Spec. Rendering
lives in ``knot/compile/query_sql.py`` (singledispatch sibling of
``compile_sql``).

The user surface is fluent immutable: every builder method on
``Query`` (``.where()``, ``.order_by()``, ``.limit()``, etc.) returns
a new ``Query`` — the AST is never mutated. ``OntologyClass`` exposes
the same methods as ergonomic entry points: ``Movie.where(...)`` is
sugar for ``Query(class_name="Movie").where(...)``.

The AST carries ``class_name`` as a string (same convention as ``Ref``
in ``knot.ast.expr``) so the node itself is decoupled from
``OntologyClass`` instances. A ``Query`` constructed via the
class-side fluent API also holds a private back-reference to its
owning ``Spec`` so ``q.sql(schema=...)`` compiles itself end-to-end.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from knot.ast.expr import Expr

if TYPE_CHECKING:
    from knot.spec import Spec


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

    ``_spec`` is a back-reference set by the class-side fluent entry
    points (``OntologyClass.where`` / ``.order_by`` / etc.) so
    ``q.sql(schema=...)`` knows which spec to compile against. It's
    intentionally private and excluded from repr/compare so the AST
    still behaves like pure data for tests and equality checks.
    """

    class_name: str
    where_clause: Expr | None = None
    ordering: tuple[OrderBy, ...] = ()
    limit_value: int | None = None
    offset_value: int | None = None
    projection: tuple[Expr, ...] | None = None
    target_suffix: str = "_resolved"
    _spec: Any = field(default=None, repr=False, compare=False)

    def where(self, predicate: Expr) -> Query:
        """AND ``predicate`` into the existing WHERE clause."""
        combined = (
            predicate if self.where_clause is None else self.where_clause & predicate
        )
        return replace(self, where_clause=combined)

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

    # ------------------------------------------------------------------
    # Compile entry point — methods on the entity they're about. The
    # Spec back-reference lets the host call ``q.sql(...)`` directly.
    # ------------------------------------------------------------------

    def sql(
        self,
        *,
        schema: str = "knot_data",
    ) -> tuple[str, list[Any]]:
        """Compile this query to ``(sql, params)`` against its owning
        spec. Validates the spec first; raises ``SpecError`` if
        malformed and ``RuntimeError`` if this Query wasn't built via
        a spec's class (no back-reference)."""
        if self._spec is None:
            raise RuntimeError(
                "Query has no spec back-reference — build it via "
                "spec_class.where(...) / .order_by(...) / etc., or use "
                "knot.compile.query.compile_query(q, spec=spec) directly"
            )
        self._spec.validate()
        from knot.compile.query import compile_query

        return compile_query(self, spec=self._spec, schema=schema)

    def with_spec(self, spec: Spec) -> Query:
        """Return a copy of this query bound to ``spec``. Useful when
        the AST was constructed directly (``Query(class_name=...)``)
        and you want to attach a spec after the fact."""
        return replace(self, _spec=spec)
