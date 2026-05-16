"""Live-database introspection helpers used by the diff driver.

Thin wrappers over ``information_schema`` / ``pg_indexes`` /
``pg_views`` plus a small type-normalization helper so postgres' shape
strings (e.g. ``timestamp with time zone``) compare cleanly against
``knot.compile.ddl._pg_type``'s output (``timestamptz``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Query callable: takes (sql, params) and returns row tuples.
QueryFn = Callable[[str, tuple[Any, ...]], list[tuple[Any, ...]]]


def _existing_schemas(query: QueryFn) -> set[str]:
    rows = query("SELECT schema_name FROM information_schema.schemata", ())
    return {r[0] for r in rows}


def _existing_tables(query: QueryFn, schema: str) -> set[str]:
    rows = query(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
        (schema,),
    )
    return {r[0] for r in rows}


def _existing_views(query: QueryFn, schema: str) -> set[str]:
    rows = query(
        "SELECT table_name FROM information_schema.views WHERE table_schema = %s",
        (schema,),
    )
    return {r[0] for r in rows}


@dataclass(frozen=True, slots=True)
class _ColInfo:
    """Type + nullability for a single existing column."""

    pg_type: str  # normalized to match _pg_type's output
    nullable: bool


def _existing_columns(query: QueryFn, schema: str, table: str) -> set[str]:
    """Names of columns on a table — used where we only care about
    presence. See ``_existing_column_details`` for full type info."""
    return set(_existing_column_details(query, schema, table).keys())


def _existing_column_details(
    query: QueryFn,
    schema: str,
    table: str,
) -> dict[str, _ColInfo]:
    """``{column_name: _ColInfo}`` from ``information_schema.columns``,
    with postgres types normalized so they compare cleanly against the
    spec's ``_pg_type`` output (``timestamp with time zone`` →
    ``timestamptz``, ``ARRAY`` + ``udt_name='_text'`` → ``text[]``,
    etc.)."""
    rows = query(
        "SELECT column_name, data_type, is_nullable, udt_name "
        "FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return {
        r[0]: _ColInfo(
            pg_type=_normalize_pg_type(r[1], r[3] if len(r) > 3 else None),
            nullable=(r[2] == "YES"),
        )
        for r in rows
    }


_NORMALIZE_DATA_TYPE: dict[str, str] = {
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "character varying": "text",
}


def _normalize_pg_type(data_type: str, udt_name: str | None) -> str:
    """Map an ``information_schema.columns.data_type`` to the same
    string ``_pg_type`` produces. Handles arrays via ``udt_name`` (the
    underscore-prefixed element type name)."""
    if data_type == "ARRAY":
        if udt_name and udt_name.startswith("_"):
            return _normalize_pg_type(udt_name[1:], None) + "[]"
        return "?[]"
    return _NORMALIZE_DATA_TYPE.get(data_type, data_type)


def _existing_indexes(query: QueryFn, schema: str, table: str) -> set[str]:
    rows = query(
        "SELECT indexname FROM pg_indexes WHERE schemaname = %s AND tablename = %s",
        (schema, table),
    )
    return {r[0] for r in rows}


def _existing_fk_constraints(query: QueryFn, schema: str, table: str) -> set[str]:
    rows = query(
        "SELECT constraint_name FROM information_schema.table_constraints "
        "WHERE table_schema = %s AND table_name = %s "
        "AND constraint_type = 'FOREIGN KEY'",
        (schema, table),
    )
    return {r[0] for r in rows}


def _existing_weight_rows(
    query: QueryFn,
    schema: str,
    weight_table_name: str,
) -> dict[tuple[str, str, str], float]:
    """Return ``{(source_name, class_name, slot_name): weight}`` from the
    weight table, or empty dict if the table doesn't exist."""
    if weight_table_name not in _existing_tables(query, schema):
        return {}
    rows = query(
        f"SELECT source_name, class_name, slot_name, weight "
        f"FROM {schema}.{weight_table_name}",
        (),
    )
    return {(r[0], r[1], r[2]): float(r[3]) for r in rows}
