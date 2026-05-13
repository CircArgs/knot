"""Storage-side naming helpers — schema + per-class table identifiers.

Single source of truth for:
  - schema name + synthetic correction-source name
  - per-class table identifier (``<schema>.<lowercased>``)
  - per-class bindings table identifier

The compiler is pure and has no env-driven runtime config; identifiers
are constants the host can override by re-binding ``SCHEMA`` /
``USER_CORRECTIONS_SOURCE`` at import time (a tiny ``configure(...)``
helper is intentionally absent — explicit assignment is enough).

Pure spec-graph helpers (``is_stored``, ``effective_slots``,
``stored_slot_names``) live under ``knot.spec``.
"""

from __future__ import annotations

from psycopg import sql

from knot.spec import OntologyClass

# Compile-time constants — the host can rebind these before calling the
# emitters if a non-default schema is required. They are deliberately
# module-level rather than function-call indirected so the emitted DDL
# is deterministic for a given import.
SCHEMA: str = "knot_data"
USER_CORRECTIONS_SOURCE: str = "_user_corrections"


def schema() -> str:
    """Postgres schema for data-plane tables (default ``knot_data``)."""
    return SCHEMA


def user_corrections_source() -> str:
    """Synthetic ``_source`` value for user-correction rows
    (default ``_user_corrections``)."""
    return USER_CORRECTIONS_SOURCE


def table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(schema(), cls.name.lower())


def bindings_table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(schema(), f"{cls.name.lower()}_bindings")
