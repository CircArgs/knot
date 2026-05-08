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


def effective_slots(cls: OntologyClass) -> list[Slot]:
    """All slots a class declares for *its own* table — own + mixin slots.

    Walks the mixin chain breadth-first; later mixins do NOT shadow earlier
    ones (publish-gate rejects collisions before we ever get here). Own slots
    DO shadow mixin slots of the same name.

    Does NOT walk ``is_a``: a concrete subclass with its own table inherits
    its parent's slots structurally via the GraphQL surface, not via column
    duplication. Mixins, by contrast, are pure trait composition — their
    slots live on every including class's own table.
    """
    seen: set[str] = set()
    result: list[Slot] = []
    for s in cls.slots:
        seen.add(s.name)
        result.append(s)
    queue: list[OntologyClass] = list(cls.mixins)
    visited: list[OntologyClass] = []
    while queue:
        current = queue.pop(0)
        if any(current is v for v in visited):
            continue
        visited.append(current)
        for s in current.slots:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s)
        queue.extend(current.mixins)
    return result


def stored_slot_names(cls: OntologyClass) -> list[str]:
    return [s.name for s in effective_slots(cls) if is_stored(s)]


def table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(SCHEMA, cls.name.lower())


def bindings_table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(SCHEMA, f"{cls.name.lower()}_bindings")
