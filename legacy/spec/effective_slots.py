"""Pure spec-graph helpers — no SQL, no postgres, no I/O.

These walk the typed entity tree (``OntologyClass`` + ``DefinedClass`` + ``Slot``)
and answer spec-shape questions:

  - ``is_stored(slot)`` — derivation is None ⇒ slot has a column
  - ``effective_slots(cls)`` — own + transitive mixin slots
    For DefinedClass: inherits from is_a parent (no own slots).
  - ``stored_slot_names(cls)`` — column names a class's table holds
    For DefinedClass: always [] (backed by a VIEW, no own columns).

Storage / SQL naming (schema, table_id, bindings_table_id) lives under
``knot.db._naming``; that's where the psycopg ``sql.Identifier`` plumbing
belongs. Pure spec-graph computation belongs here.
"""

from __future__ import annotations

from knot.spec.metaschema import DefinedClass, OntologyClass, Slot


def is_stored(slot: Slot) -> bool:
    """Derived slots are query-time projections; only stored slots get columns."""
    return getattr(slot, "derivation", None) is None


def effective_slots(cls: OntologyClass | DefinedClass) -> list[Slot]:
    """All slots visible on a class: own slots + inherited is_a slots + mixin slots.

    For OntologyClass:
      Walk order (earlier entries shadow later ones):
        1. Own slots (``cls.slots``)
        2. is_a ancestor slots, breadth-first up the inheritance chain
        3. Mixin slots, breadth-first (including mixins of ancestors)

      This mirrors standard OO/RDF inheritance: a concrete subclass carries
      its parent's slots as real columns on its own table.  The publish gate
      rejects is_a slot name collisions the same way it does for mixins.

      Own slots shadow ancestor slots of the same name; ancestor slots shadow
      later-ancestor (grandparent) slots.

    For DefinedClass:
      The class has no own slots (a VIEW has no own columns).  Effective slots
      are inherited from the is_a parent + any mixins (same walk as above but
      starting from is_a instead of self).
    """
    if isinstance(cls, DefinedClass):
        # DefinedClass has no own slots. Delegate to is_a parent + mixins.
        return _effective_slots_from_parent(cls.is_a, extra_mixins=cls.mixins)

    # OntologyClass — standard BFS walk.
    seen: set[str] = set()
    result: list[Slot] = []

    # BFS over the is_a chain (including self) then mixins at each level.
    # We collect classes in BFS order: self → is_a → is_a.is_a → ...
    # For each class in that order we add own slots then enqueue its mixins.
    isa_queue: list[OntologyClass] = [cls]
    isa_visited: list[OntologyClass] = []
    mixin_queue: list[OntologyClass] = []

    while isa_queue:
        current = isa_queue.pop(0)
        if any(current is v for v in isa_visited):
            continue
        isa_visited.append(current)
        # Own slots of this class in the is_a chain.
        for s in current.slots:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s)
        # Enqueue mixins for the mixin pass.
        mixin_queue.extend(current.mixins)
        # Walk up is_a.
        if current.is_a is not None:
            isa_queue.append(current.is_a)

    # Mixin pass: breadth-first over collected mixins (and their own mixins).
    mixin_visited: list[OntologyClass] = []
    while mixin_queue:
        current = mixin_queue.pop(0)
        if any(current is v for v in mixin_visited):
            continue
        mixin_visited.append(current)
        for s in current.slots:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s)
        mixin_queue.extend(current.mixins)

    return result


def _effective_slots_from_parent(
    parent: OntologyClass,
    extra_mixins: list[OntologyClass],
) -> list[Slot]:
    """Compute effective slots starting from a parent OntologyClass + extra mixins.

    Used by DefinedClass to inherit slots from its is_a parent without
    contributing any own slots of its own.
    """
    seen: set[str] = set()
    result: list[Slot] = []

    isa_queue: list[OntologyClass] = [parent]
    isa_visited: list[OntologyClass] = []
    mixin_queue: list[OntologyClass] = list(extra_mixins)

    while isa_queue:
        current = isa_queue.pop(0)
        if any(current is v for v in isa_visited):
            continue
        isa_visited.append(current)
        for s in current.slots:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s)
        mixin_queue.extend(current.mixins)
        if current.is_a is not None:
            isa_queue.append(current.is_a)

    mixin_visited: list[OntologyClass] = []
    while mixin_queue:
        current = mixin_queue.pop(0)
        if any(current is v for v in mixin_visited):
            continue
        mixin_visited.append(current)
        for s in current.slots:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s)
        mixin_queue.extend(current.mixins)

    return result


def stored_slot_names(cls: OntologyClass | DefinedClass) -> list[str]:
    """Column names for the class's storage.

    Returns [] for DefinedClass — it is backed by a VIEW with no own columns.
    """
    if isinstance(cls, DefinedClass):
        return []
    return [s.name for s in effective_slots(cls) if is_stored(s)]
