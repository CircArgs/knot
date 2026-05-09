"""SQL fragment builders for reads against the per-class data plane.

Composes the SELECT/JOIN structure for entity reads — with or without
derived-slot projections, with or without tombstoned rows. Inlines the
predicate compiler for derived slots so callers don't have to.

Consumed by ``knot.db.graph_store`` at request time. graph_store appends
WHERE / ORDER BY / LIMIT on top of the fragment returned here.
"""

from __future__ import annotations

from typing import Any

from psycopg import sql

from knot.db._naming import (
    bindings_table_id as _bindings_id,
)
from knot.db._naming import (
    effective_slots as _effective_slots,
)
from knot.db._naming import (
    is_stored as _is_stored,
)
from knot.db._naming import (
    table_id as _table_id,
)
from knot.spec.metaschema import OntologyClass


def _is_defined_class(cls: OntologyClass) -> bool:
    """True when the class is a defined class (backed by a VIEW, not a TABLE)."""
    return getattr(cls, "definition", None) is not None


def select_with_binding(
    cls: OntologyClass,
    include_tombstoned: bool = False,
) -> sql.Composable:
    """``SELECT s.*, b.canonical_id AS _canonical_id FROM source s JOIN bindings b ...``

    When ``include_tombstoned=True``, also surfaces rows whose most-recent
    binding has ``change_type = 'tombstone'`` (closed bindings).

    For defined classes, the VIEW already embeds the JOIN and exposes
    ``_canonical_id`` directly; query it without the extra JOIN.
    """
    if _is_defined_class(cls):
        return sql.SQL("SELECT s.* FROM {view} s").format(view=_table_id(cls))
    if include_tombstoned:
        return sql.SQL(
            "SELECT s.*, b.canonical_id AS _canonical_id "
            "FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id "
            "  AND b.valid_from = ("
            "    SELECT max(b2.valid_from) FROM {bindings} b2 "
            "    WHERE b2.knot_row_id = s._knot_row_id "
            "      AND b2.change_type IN ('tombstone', 'ingest', 'correction', "
            "                             'merge', 'split', 'add', 'rejected')"
            "  ) "
            "  AND (b.valid_to IS NULL OR b.change_type = 'tombstone')"
        ).format(source=_table_id(cls), bindings=_bindings_id(cls))
    return sql.SQL(
        "SELECT s.*, b.canonical_id AS _canonical_id "
        "FROM {source} s "
        "JOIN {bindings} b "
        "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
    ).format(source=_table_id(cls), bindings=_bindings_id(cls))


def derived_column_exprs(
    cls: OntologyClass,
) -> tuple[list[sql.Composable], list[Any]]:
    """Build ``(<subquery>) AS <slot_name>`` fragments for derived slots,
    plus the accumulated positional parameters for those subqueries.

    Returns ``([], [])`` when the class has no derived slots (the common case).
    Outer alias used for the source-row table is ``"s"`` — same alias emitted
    by :func:`select_with_binding`.

    Parameters must be prepended to the outer query's parameter list because
    the derived columns appear in the SELECT clause before any WHERE params.
    """
    from knot.spec.compile.postgres import CompileContext, compile_value

    derived_cols: list[sql.Composable] = []
    derived_params: list[Any] = []
    for slot in _effective_slots(cls):
        if _is_stored(slot):
            continue
        derivation = getattr(slot, "derivation", None)
        if derivation is None:
            continue
        ctx = CompileContext(primary_class=cls, alias="s")
        expr_sql = compile_value(derivation, ctx)
        derived_cols.append(
            sql.SQL("({expr}) AS {col}").format(
                expr=expr_sql,
                col=sql.Identifier(slot.name),
            )
        )
        derived_params.extend(ctx.params)
    return derived_cols, derived_params


def select_with_derivations(
    cls: OntologyClass,
    include_tombstoned: bool = False,
) -> tuple[sql.Composable, list[Any]]:
    """Same as :func:`select_with_binding` but appends a computed column
    per derived slot, compiled via the SQL compiler.

    Returns ``(sql_composable, derived_params)``. Caller must prepend the
    params to the outer query's parameter list (SELECT params come before
    WHERE params in psycopg positional binding).
    """
    base = select_with_binding(cls, include_tombstoned=include_tombstoned)
    derived_cols, derived_params = derived_column_exprs(cls)
    if not derived_cols:
        return base, []
    extra = sql.SQL(", ").join(derived_cols)
    if _is_defined_class(cls):
        stmt = sql.SQL("SELECT s.*, {extra} FROM {view} s").format(extra=extra, view=_table_id(cls))
    elif include_tombstoned:
        stmt = sql.SQL(
            "SELECT s.*, b.canonical_id AS _canonical_id, {extra} "
            "FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id "
            "  AND b.valid_from = ("
            "    SELECT max(b2.valid_from) FROM {bindings} b2 "
            "    WHERE b2.knot_row_id = s._knot_row_id "
            "      AND b2.change_type IN ('tombstone', 'ingest', 'correction', "
            "                             'merge', 'split', 'add', 'rejected')"
            "  ) "
            "  AND (b.valid_to IS NULL OR b.change_type = 'tombstone')"
        ).format(
            extra=extra,
            source=_table_id(cls),
            bindings=_bindings_id(cls),
        )
    else:
        stmt = sql.SQL(
            "SELECT s.*, b.canonical_id AS _canonical_id, {extra} "
            "FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
        ).format(
            extra=extra,
            source=_table_id(cls),
            bindings=_bindings_id(cls),
        )
    return stmt, derived_params
