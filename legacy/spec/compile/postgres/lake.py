"""Lake materialization SQL generators.

knot doesn't materialize to Iceberg / lake itself — it generates SELECT
bodies that an external operator can wrap in whatever dialect their
target requires (Trino ``CREATE OR REPLACE TABLE … AS``, Spark CTAS,
dbt model file, Iceberg ``MERGE INTO``, etc.).

Two views per concrete class:

  - **current**: resolves the SCD2 bindings to a flat snapshot — one row
    per current canonical_id with all stored slot values, source
    attribution, and bind metadata. ``valid_to IS NULL`` filter applied.
  - **history**: the full SCD2 timeline including closed bindings, with
    ``valid_from`` / ``valid_to`` / ``change_type`` columns.

Defined classes (storage = parent VIEW) and abstract classes are skipped:
they have no table to materialize. Polymorphic classes go through normally
(they have a regular table even though Sources can't target them).
"""

from __future__ import annotations

from psycopg import sql

from knot.spec import OntologyClass
from knot.spec import effective_slots as _effective_slots
from knot.spec import is_stored as _is_stored

from ._naming import (
    bindings_table_id as _bindings_table_id,
)
from ._naming import (
    table_id as _table_id,
)


def is_materializable(cls: OntologyClass) -> bool:
    """A class has its own table iff it's concrete and not a defined-class view."""
    if cls.abstract:
        return False
    if getattr(cls, "definition", None) is not None:
        return False
    return True


def _slot_cols(cls: OntologyClass) -> list[sql.Composable]:
    return [
        sql.SQL("s.{}").format(sql.Identifier(slot.name))
        for slot in _effective_slots(cls)
        if _is_stored(slot)
    ]


def materialize_current(cls: OntologyClass) -> str:
    """SELECT body for a flat current snapshot of ``cls``.

    Columns: canonical_id, source, source_row_id, ingest_at,
    spec_revision, valid_from (= bound_at), then every stored slot.
    """
    body = sql.SQL(
        "SELECT b.canonical_id, "
        "       b.valid_from AS bound_at, "
        "       b.applied_revision AS spec_revision, "
        "       s._source AS source, "
        "       s._source_row_id AS source_row_id, "
        "       s._ingest_at AS ingest_at"
        "{slot_cols}"
        " FROM {tbl} s "
        "JOIN {bind} b "
        "  ON b.knot_row_id = s._knot_row_id "
        " AND b.valid_to IS NULL"
    ).format(
        slot_cols=_prefix_join(_slot_cols(cls)),
        tbl=_table_id(cls),
        bind=_bindings_table_id(cls),
    )
    return body.as_string(None)


def materialize_history(cls: OntologyClass) -> str:
    """SELECT body for the full SCD2 timeline of ``cls``.

    Columns: canonical_id, valid_from, valid_to, change_type, source,
    source_row_id, ingest_at, spec_revision, then every stored slot.
    Closed (valid_to IS NOT NULL) and current (valid_to IS NULL) bindings
    are both emitted.
    """
    body = sql.SQL(
        "SELECT b.canonical_id, "
        "       b.valid_from, "
        "       b.valid_to, "
        "       b.change_type, "
        "       b.applied_revision AS spec_revision, "
        "       b.correction_id, "
        "       s._source AS source, "
        "       s._source_row_id AS source_row_id, "
        "       s._ingest_at AS ingest_at"
        "{slot_cols}"
        " FROM {tbl} s "
        "JOIN {bind} b "
        "  ON b.knot_row_id = s._knot_row_id"
    ).format(
        slot_cols=_prefix_join(_slot_cols(cls)),
        tbl=_table_id(cls),
        bind=_bindings_table_id(cls),
    )
    return body.as_string(None)


def _prefix_join(parts: list[sql.Composable]) -> sql.Composable:
    """``", "``-join with a leading separator if any parts; empty SQL otherwise.

    Used so the slot-column list can sit cleanly after a fixed prefix
    in the SELECT list without trailing-comma plumbing.
    """
    if not parts:
        return sql.SQL("")
    return sql.SQL(", ") + sql.SQL(", ").join(parts)
