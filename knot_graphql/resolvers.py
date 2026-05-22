"""Universal resolver templates for knot_graphql.

``Resolvers(spec, sql_executor)`` provides four resolver methods that
the host wires into whatever GraphQL server framework it uses
(Ariadne, Strawberry, graphql-core, Graphene, …).

``sql_executor`` is a callable ``(sql: str) -> list[dict]`` — the
host injects psycopg / SQLAlchemy / asyncpg without any import here.
No GraphQL framework dependency; no DB driver dependency.

Projection helper::

    refs = projection_from_selection(["title", "year"], movie)
    # → [movie.col.title, movie.col.year]

Resolver entry points::

    resolvers = Resolvers(spec, executor)
    row  = resolvers.resolve_by_id("Movie", "m_001")
    rows = resolvers.resolve_list("Movie", first=10, after=None)
    fk   = resolvers.resolve_fk_walk(parent_row, "director")
    n    = resolvers.resolve_reverse_count(parent_row, "Movie", "movie", "Credit")
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from knot.ast.expr import Ref
from knot.spec import OntologyClass, Spec, VirtualClass

# ---------------------------------------------------------------------------
# Projection helper (public utility)
# ---------------------------------------------------------------------------


def projection_from_selection(
    field_names: list[str],
    cls: OntologyClass,
) -> list[Ref]:
    """Turn a list of requested GraphQL field names into ``cls.col.<name>``
    Ref objects.  FK slots return an ``FkRef``; vector slots a
    ``VectorRef`` — ``cls.col[name]`` handles the dispatch.

    Field names that are not spec-declared slots (e.g. ``canonical_id``
    as a GraphQL alias for the identifier, or reverse-FK count fields
    like ``creditCount``) are silently skipped — the resolver returns
    the full row and the framework picks what it needs.
    """
    refs: list[Ref] = []
    slot_names = {sl.name for sl in cls.effective_slots()}
    for name in field_names:
        if name in slot_names:
            refs.append(cls.col[name])
    return refs


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


class Resolvers:
    """Universal resolver methods for a knot spec.

    Parameters
    ----------
    spec:
        The compiled knot ``Spec``.
    sql_executor:
        Callable ``(sql: str) -> list[dict]``. The host injects the
        DB adapter — no driver dependency here.
    schema:
        Postgres schema name. Defaults to ``spec.schema``.
    """

    def __init__(
        self,
        spec: Spec,
        sql_executor: Callable[[str], list[dict[str, Any]]],
        *,
        schema: str | None = None,
    ) -> None:
        self._spec = spec
        self._exec = sql_executor
        self._schema = schema if schema is not None else spec.schema

    # ------------------------------------------------------------------
    # By-ID lookup
    # ------------------------------------------------------------------

    def resolve_by_id(
        self,
        class_name: str,
        canonical_id: str,
    ) -> dict[str, Any] | None:
        """Return the resolved row for ``canonical_id``, or ``None``.

        Builds a knot ``Query`` via ``cls.resolved.where(…)`` and runs
        it through the executor. Accepts OntologyClass or VirtualClass;
        both expose ``.resolved`` and the concrete root's ``.col``.
        """
        cls = self._resolvable(class_name)
        id_slot = self._spec.identifier_slot_name
        col = _col(cls)
        q = cls.resolved.where(col[id_slot] == canonical_id).limit(1)
        rows = self._exec(q.sql())
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Paginated list
    # ------------------------------------------------------------------

    def resolve_list(
        self,
        class_name: str,
        *,
        first: int = 20,
        after: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``first`` resolved rows, optionally cursor-paginated.

        ``after`` is the opaque cursor — treated as the ``canonical_id``
        value of the last row seen (keyset pagination). ``filters`` is
        a ``{slot_name: value}`` equality map applied as additional
        WHERE predicates.
        """
        cls = self._resolvable(class_name)
        id_slot = self._spec.identifier_slot_name
        col = _col(cls)
        q = cls.resolved

        if filters:
            for slot_name, value in filters.items():
                q = q.where(col[slot_name] == value)

        if after is not None:
            # Keyset: rows whose canonical_id sorts after the cursor.
            q = q.where(col[id_slot] > after)

        q = q.order_by(col[id_slot]).limit(first)
        return self._exec(q.sql())

    # ------------------------------------------------------------------
    # FK walk (field resolver for ClassRef slots)
    # ------------------------------------------------------------------

    def resolve_fk_walk(
        self,
        parent: dict[str, Any],
        fk_slot_name: str,
    ) -> dict[str, Any] | None:
        """Resolve a FK slot on a parent row.

        ``parent`` is the dict row returned by a previous resolve call;
        ``fk_slot_name`` is the slot on that class whose type is a
        ``ClassRef``. Returns the resolved row of the target class, or
        ``None`` if the FK is null or the target row is missing.
        """
        canonical_id = parent.get(fk_slot_name)
        if canonical_id is None:
            return None

        # Discover the target class from the parent's FK slot type.
        parent_class_name = _class_name_from_row(parent)
        if parent_class_name is None:
            return None

        # FK semantics route through the concrete root — virtual classes
        # share their root's slots, so the slot lookup + target class are
        # identical.
        cls_or_virtual = self._spec.class_by_name(parent_class_name)
        if isinstance(cls_or_virtual, VirtualClass):
            cls = cls_or_virtual.concrete_root()
        else:
            cls = cls_or_virtual
        slot = cls.get_slot(fk_slot_name)
        from knot.ast.types import ClassRef

        if not isinstance(slot.type, ClassRef):
            raise TypeError(
                f"{parent_class_name}.{fk_slot_name} is not a ClassRef slot"
            )
        target_cls = slot.type.target
        id_slot = self._spec.identifier_slot_name
        q = target_cls.resolved.where(target_cls.col[id_slot] == canonical_id).limit(1)
        rows = self._exec(q.sql())
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Reverse-FK count
    # ------------------------------------------------------------------

    def resolve_reverse_count(
        self,
        parent: dict[str, Any],
        primary_class_name: str,
        fk_slot_name: str,
        ref_class_name: str,
    ) -> int:
        """Return the count of rows in ``ref_class_name`` whose
        ``fk_slot_name`` points at the given ``parent`` row.

        Uses ``cls.back(other_cls, fk_slot_name).count()`` materialized
        via a correlated sub-query through the knot Query AST.
        """
        primary_cls = self._concrete(primary_class_name)
        ref_cls = self._concrete(ref_class_name)
        id_slot = self._spec.identifier_slot_name
        canonical_id = parent.get(id_slot)
        if canonical_id is None:
            return 0

        # Build: SELECT count FROM primary WHERE id = canonical_id,
        # using the CountRel aggregate via back().count().

        count_expr = primary_cls.back(ref_cls, fk_slot_name).count()
        q = (
            primary_cls.resolved.where(primary_cls.col[id_slot] == canonical_id)
            .select(count_expr)
            .limit(1)
        )
        rows = self._exec(q.sql())
        if not rows:
            return 0
        # The row is a dict; the count column may appear as 'count' or as
        # the first value — handle both shapes gracefully.
        row = rows[0]
        if "count" in row:
            return int(row["count"])
        return int(next(iter(row.values())))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _concrete(self, class_name: str) -> OntologyClass:
        cls = self._spec.class_by_name(class_name)
        if not isinstance(cls, OntologyClass):
            raise TypeError(
                f"{class_name!r} is a VirtualClass; use its concrete root instead"
            )
        return cls

    def _resolvable(self, class_name: str) -> OntologyClass | VirtualClass:
        """Read-side dispatch: both OntologyClass and VirtualClass own a
        ``.resolved`` Query, so the resolved-view reads (by-id, list)
        accept either. FK semantics still require the concrete root."""
        return self._spec.class_by_name(class_name)


def _col(cls: OntologyClass | VirtualClass):
    """``cls.col`` for OntologyClass; for VirtualClass, fall through to
    the concrete root's ``col`` (the virtual view is the root's view
    with extra predicates — same column shape)."""
    if isinstance(cls, VirtualClass):
        return cls.concrete_root().col
    return cls.col


def _class_name_from_row(row: dict[str, Any]) -> str | None:
    """Best-effort class-name extraction from a resolver row dict.

    Rows returned by knot SQL have no embedded class name; callers that
    need FK walks should track the class name alongside the row.  This
    helper exists for the common case where the caller passes the class
    name as a ``__class_name`` metadata key (a convention, not a spec
    column).
    """
    return row.get("__class_name")
