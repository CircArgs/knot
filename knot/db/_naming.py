"""Shared naming helpers for the data plane.

Single source of truth for:
  - configurable schema name + synthetic correction-source name (env-driven)
  - per-class table identifier (``<schema>.<lowercased>``)
  - per-class bindings table identifier
  - "stored" slot detection (derivation is None)
  - effective-slot walk (own + transitive mixins)

Imported by ``knot.db.graph_store`` (DML) and by the postgres compile
dialect (``knot.spec.compile.sql.dialects.postgres``) so naming stays in
lockstep. Postgres-specific compilation (``slot.range`` → postgres column
type) lives in the compile dialect, not here.
"""

from __future__ import annotations

from psycopg import sql

from knot.config.config import get_settings
from knot.spec import OntologyClass, Slot


def schema() -> str:
    """Postgres schema for data-plane tables (default ``knot_data``)."""
    return get_settings().data_schema


def user_corrections_source() -> str:
    """Synthetic ``_source`` value for user-correction rows
    (default ``_user_corrections``)."""
    return get_settings().user_corrections_source


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
    return sql.Identifier(schema(), cls.name.lower())


def bindings_table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(schema(), f"{cls.name.lower()}_bindings")
