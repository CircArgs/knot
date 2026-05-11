"""Pure spec-graph helpers — no SQL, no postgres, no I/O.

These walk the typed entity tree (``OntologyClass`` + ``Slot``) and answer
spec-shape questions:

  - ``is_stored(slot)`` — derivation is None ⇒ slot has a column
  - ``effective_slots(cls)`` — own + transitive mixin slots
  - ``stored_slot_names(cls)`` — column names a class's table holds

Storage / SQL naming (schema, table_id, bindings_table_id) lives under
``knot.db._naming``; that's where the psycopg ``sql.Identifier`` plumbing
belongs. Pure spec-graph computation belongs here.
"""

from __future__ import annotations

from knot.spec.metaschema import OntologyClass, Slot


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
