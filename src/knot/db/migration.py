"""Migration emitter — spec → postgres DDL.

Runs at publish time (after the spec-graph gate). The previously-published
spec IS the previously-applied schema state by construction, so the diff
input is two Pydantic typed trees, not "spec vs unknown DB."

Mapping (locked):
  - schema:           ``knot_data`` (data plane, separate from ``public``)
  - class → table:    lowercase class name (``Movie`` → ``knot_data.movie``)
  - system columns:   underscore-prefixed (``_canonical_id``, ``_source``,
                      ``_source_row_id``, ``_ingest_at``, ``_spec_revision``)
  - slot → column:    range-based postgres type; multivalued → ``T[]``
  - derived slots:    skipped (query-time projections, not stored)
  - primary key:      ``(_source, _source_row_id)``

Identifiers are quoted via ``psycopg.sql.Identifier`` (class + slot names
come from the API and must be safe). Postgres types come from a fixed
whitelist; type names from the spec are never spliced raw.

Today this module handles **first-publish only** (idempotent
``CREATE TABLE IF NOT EXISTS``). The diff visitor for ALTER paths on
subsequent publishes is open and lands as the next slice.
"""

from __future__ import annotations

import psycopg
from psycopg import sql

from knot.ontology import OntologyClass, Slot, Spec, TypeDefinition


_SCHEMA = "knot_data"


_PG_TYPE_FOR_BASE: dict[str, str] = {
    "str":      "TEXT",
    "string":   "TEXT",
    "int":      "BIGINT",
    "integer":  "BIGINT",
    "float":    "DOUBLE PRECISION",
    "bool":     "BOOLEAN",
    "boolean":  "BOOLEAN",
    "datetime": "TIMESTAMPTZ",
    "date":     "DATE",
}


def _slot_pg_type(slot: Slot) -> str:
    """Postgres column type for a stored slot. Returns a whitelist value
    (never a user-supplied string)."""
    if isinstance(slot.range, OntologyClass):
        base = "TEXT"
    elif isinstance(slot.range, TypeDefinition):
        key = (slot.range.base or "str").lower()
        base = _PG_TYPE_FOR_BASE.get(key, "TEXT")
    else:
        base = "TEXT"
    return f"{base}[]" if slot.multivalued else base


def _table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(_SCHEMA, cls.name.lower())


def _is_stored(slot: Slot) -> bool:
    return getattr(slot, "derivation", None) is None


# System columns — knot-managed; underscore-prefixed so they can't collide
# with user slot names. ``_spec_revision`` references public.spec_revisions
# so a row's lineage joins straight to the revision that shaped its meaning.
_SYSTEM_COLUMNS_SQL = sql.SQL(
    "_canonical_id TEXT NOT NULL, "
    "_source TEXT NOT NULL, "
    "_source_row_id TEXT NOT NULL, "
    "_ingest_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "_spec_revision INTEGER NOT NULL REFERENCES public.spec_revisions(revision), "
    "PRIMARY KEY (_source, _source_row_id)"
)


def _create_table_sql(cls: OntologyClass) -> sql.Composable:
    user_cols: list[sql.Composable] = []
    for slot in cls.slots:
        if not _is_stored(slot):
            continue
        nullable_kw = sql.SQL("NOT NULL") if slot.required else sql.SQL("NULL")
        user_cols.append(
            sql.SQL("{name} {pgtype} {nullable}").format(
                name=sql.Identifier(slot.name),
                pgtype=sql.SQL(_slot_pg_type(slot)),
                nullable=nullable_kw,
            )
        )
    body = sql.SQL(", ").join([_SYSTEM_COLUMNS_SQL, *user_cols])
    return sql.SQL("CREATE TABLE IF NOT EXISTS {table} ({body})").format(
        table=_table_id(cls), body=body,
    )


def apply_initial_schema(conn: psycopg.Connection, spec: Spec) -> None:
    """Apply first-publish DDL (CREATE TABLE IF NOT EXISTS per concrete class)."""
    for c in spec.classes:
        if c.abstract:
            continue
        conn.execute(_create_table_sql(c))
