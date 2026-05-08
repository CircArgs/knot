"""Postgres column-type mapping — ``slot.range`` → postgres SQL type.

Whitelist; user-supplied ``slot.range.base`` is never spliced raw into DDL.
This is compilation: the spec describes a slot in abstract terms, this
module decides what postgres column type it becomes.
"""

from __future__ import annotations

from knot.spec.metaschema import OntologyClass, Slot, TypeDefinition


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
    """Postgres column type for a stored slot. Multivalued → ``T[]``."""
    if isinstance(slot.range, OntologyClass):
        base = "TEXT"
    elif isinstance(slot.range, TypeDefinition):
        key = (slot.range.base or "str").lower()
        base = PG_TYPE_FOR_BASE.get(key, "TEXT")
    else:
        base = "TEXT"
    return f"{base}[]" if slot.multivalued else base
