"""Shared naming + type-mapping helpers for the data plane.

Single source of truth for:
  - per-class table identifier (``knot_data.<lowercased>``)
  - per-class bindings table identifier
  - "stored" slot detection (derivation is None)
  - ``slot.range`` → postgres column type whitelist

Both ``knot.db.migration`` (DDL emission) and ``knot.db.graph_store``
(DML) import from here so the conventions stay in lockstep.
"""

from __future__ import annotations

from psycopg import sql

from knot.ontology import OntologyClass, Slot, TypeDefinition


SCHEMA = "knot_data"

USER_CORRECTIONS_SOURCE = "_user_corrections"


PG_TYPE_FOR_BASE: dict[str, str] = {
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


def slot_pg_type(slot: Slot) -> str:
    """Postgres column type for a stored slot. Whitelist values only —
    user-supplied ``slot.range.base`` is never spliced raw into DDL."""
    if isinstance(slot.range, OntologyClass):
        base = "TEXT"
    elif isinstance(slot.range, TypeDefinition):
        key = (slot.range.base or "str").lower()
        base = PG_TYPE_FOR_BASE.get(key, "TEXT")
    else:
        base = "TEXT"
    return f"{base}[]" if slot.multivalued else base


def is_stored(slot: Slot) -> bool:
    """Derived slots are query-time projections; only stored slots get columns."""
    return getattr(slot, "derivation", None) is None


def stored_slot_names(cls: OntologyClass) -> list[str]:
    return [s.name for s in cls.slots if is_stored(s)]


def table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(SCHEMA, cls.name.lower())


def bindings_table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(SCHEMA, f"{cls.name.lower()}_bindings")
