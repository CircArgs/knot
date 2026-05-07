"""Graph data-plane CRUD against per-class postgres tables.

Owns INSERT/SELECT against ``knot_data.<class>``. Tables themselves are
emitted by ``knot.db.migration`` at publish time; this module assumes
they exist (and they do, because publish wires them).

System-column conventions (locked, see ``migration``):
  - ``_canonical_id``    initially the identifier-slot value (ER refines later)
  - ``_source``          source.name (FK by name to the published spec)
  - ``_source_row_id``   string-coerced identifier-slot value
  - ``_spec_revision``   spec_revisions.revision at ingest time (FK)
  - ``_ingest_at``       ``now()`` (DB default on insert; refreshed on update)

PK is ``(_source, _source_row_id)``; INSERTs upsert on conflict.

All identifiers are quoted via ``psycopg.sql.Identifier`` — never f-strings —
because class and slot names come from API requests and must be safe.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql

from knot.ontology import OntologyClass, Source


_SCHEMA = "knot_data"
_SYSTEM_COLS = ("_canonical_id", "_source", "_source_row_id", "_spec_revision")


def _table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(_SCHEMA, cls.name.lower())


def _stored_slot_names(cls: OntologyClass) -> list[str]:
    return [s.name for s in cls.slots if getattr(s, "derivation", None) is None]


def insert_rows(
    conn: psycopg.Connection,
    *,
    source: Source,
    spec_revision: int,
    rows: list[dict[str, Any]],
) -> int:
    """Upsert a batch of rows for one source. Returns number written."""
    cls = source.entity_class
    id_slot_name = source.identifier_slot.name
    slot_names = _stored_slot_names(cls)
    col_names = [*_SYSTEM_COLS, *slot_names]

    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in col_names)
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(col_names))
    update_set = sql.SQL(", ").join(
        sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
        for c in col_names
        if c not in ("_source", "_source_row_id")
    )

    stmt = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        "ON CONFLICT (_source, _source_row_id) DO UPDATE "
        "SET {update_set}, _ingest_at = now()"
    ).format(
        table=_table_id(cls),
        cols=cols_sql,
        placeholders=placeholders,
        update_set=update_set,
    )

    count = 0
    for row in rows:
        id_value = row[id_slot_name]
        values: list[Any] = [
            id_value,        # _canonical_id
            source.name,     # _source
            str(id_value),   # _source_row_id
            spec_revision,   # _spec_revision
        ]
        for slot_name in slot_names:
            values.append(row.get(slot_name))
        conn.execute(stmt, values)
        count += 1
    return count
