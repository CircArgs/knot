"""Read substrate — SELECT-shape AST nodes.

Pure data dataclasses describing queries over the Spec. Rendering
lives in ``knot/compile/query_sql.py`` (singledispatch sibling of
``compile_sql``).

The user surface is fluent immutable: every builder method on
``Query`` (``.where()``, ``.order_by()``, ``.limit()``, etc.) returns
a new ``Query`` — the AST is never mutated. ``OntologyClass`` exposes
the same methods as ergonomic entry points: ``Movie.where(...)`` is
sugar for ``Query(class_name="Movie").where(...)``.

The AST stays decoupled from ``OntologyClass`` instances by carrying
``class_name`` strings — same convention as ``Ref`` in ``knot.expr``.
Compilation looks up the class via the ``Spec`` passed to
``compile_query``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from knot.ast.expr import Expr


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
    """

    class_name: str
    where_clause: Expr | None = None
    ordering: tuple[OrderBy, ...] = ()
    limit_value: int | None = None
    offset_value: int | None = None
    projection: tuple[Expr, ...] | None = None
    target_suffix: str = "_resolved"

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
