"""Storage-side naming helpers — schema + per-class table identifiers.

Single source of truth for:
  - configurable schema name + synthetic correction-source name (env-driven)
  - per-class table identifier (``<schema>.<lowercased>``)
  - per-class bindings table identifier

Imported by ``knot.db.graph_store`` (DML) and by the postgres compile
dialect (``knot.spec.compile.postgres``) so naming stays in
lockstep. Postgres-specific compilation (``slot.range`` → postgres column
type) lives in the compile dialect, not here.

Pure spec-graph helpers (``is_stored``, ``effective_slots``,
``stored_slot_names``) live under ``knot.spec`` — they walk the typed
entity tree and don't touch SQL.
"""

from __future__ import annotations

from psycopg import sql

from knot.config.config import get_settings
from knot.spec import OntologyClass


def schema() -> str:
    """Postgres schema for data-plane tables (default ``knot_data``)."""
    return get_settings().data_schema


def user_corrections_source() -> str:
    """Synthetic ``_source`` value for user-correction rows
    (default ``_user_corrections``)."""
    return get_settings().user_corrections_source


def table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(schema(), cls.name.lower())


def bindings_table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(schema(), f"{cls.name.lower()}_bindings")
