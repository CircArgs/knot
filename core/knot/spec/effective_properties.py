"""Pure spec-graph helpers — no SQL, no postgres, no I/O.

These walk the typed entity tree (``OntologyClass`` + ``DefinedClass`` + ``Property``)
and answer spec-shape questions:

  - ``is_stored(property)`` — derivation is None ⇒ property has a column
  - ``effective_properties(cls)`` — own + transitive mixin properties
    For DefinedClass: inherits from is_a parent (no own properties).
  - ``stored_property_names(cls)`` — column names a class's table holds
    For DefinedClass: always [] (backed by a VIEW, no own columns).

Storage / SQL naming (schema, table_id, bindings_table_id) lives under
``knot.db._naming``; that's where the psycopg ``sql.Identifier`` plumbing
belongs. Pure spec-graph computation belongs here.
"""

from __future__ import annotations

from knot.spec.metaschema import DefinedClass, OntologyClass, Property


def is_stored(prop: Property) -> bool:
    """Derived properties are query-time projections; only stored properties get columns."""
    return getattr(property, "derivation", None) is None


def effective_properties(cls: OntologyClass | DefinedClass) -> list[Property]:
    """All properties visible on a class: own properties + inherited is_a properties + mixin properties.

    For OntologyClass:
      Walk order (earlier entries shadow later ones):
        1. Own properties (``cls.properties``)
        2. is_a ancestor properties, breadth-first up the inheritance chain
        3. Mixin properties, breadth-first (including mixins of ancestors)

      This mirrors standard OO/RDF inheritance: a concrete subclass carries
      its parent's properties as real columns on its own table.  The publish gate
      rejects is_a property name collisions the same way it does for mixins.

      Own properties shadow ancestor properties of the same name; ancestor properties shadow
      later-ancestor (grandparent) properties.

    For DefinedClass:
      The class has no own properties (a VIEW has no own columns).  Effective properties
      are inherited from the is_a parent + any mixins (same walk as above but
      starting from is_a instead of self).
    """
    if isinstance(cls, DefinedClass):
        # DefinedClass has no own properties. Delegate to is_a parent + mixins.
        return _effective_properties_from_parent(cls.is_a, extra_mixins=cls.mixins)

    # OntologyClass — standard BFS walk.
    seen: set[str] = set()
    result: list[Property] = []

    # BFS over the is_a chain (including self) then mixins at each level.
    # We collect classes in BFS order: self → is_a → is_a.is_a → ...
    # For each class in that order we add own properties then enqueue its mixins.
    isa_queue: list[OntologyClass] = [cls]
    isa_visited: list[OntologyClass] = []
    mixin_queue: list[OntologyClass] = []

    while isa_queue:
        current = isa_queue.pop(0)
        if any(current is v for v in isa_visited):
            continue
        isa_visited.append(current)
        # Own properties of this class in the is_a chain.
        for p in current.properties:
            if p.name not in seen:
                seen.add(p.name)
                result.append(p)
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
        for p in current.properties:
            if p.name not in seen:
                seen.add(p.name)
                result.append(p)
        mixin_queue.extend(current.mixins)

    return result


def _effective_properties_from_parent(
    parent: OntologyClass,
    extra_mixins: list[OntologyClass],
) -> list[Property]:
    """Compute effective properties starting from a parent OntologyClass + extra mixins.

    Used by DefinedClass to inherit properties from its is_a parent without
    contributing any own properties of its own.
    """
    seen: set[str] = set()
    result: list[Property] = []

    isa_queue: list[OntologyClass] = [parent]
    isa_visited: list[OntologyClass] = []
    mixin_queue: list[OntologyClass] = list(extra_mixins)

    while isa_queue:
        current = isa_queue.pop(0)
        if any(current is v for v in isa_visited):
            continue
        isa_visited.append(current)
        for p in current.properties:
            if p.name not in seen:
                seen.add(p.name)
                result.append(p)
        mixin_queue.extend(current.mixins)
        if current.is_a is not None:
            isa_queue.append(current.is_a)

    mixin_visited: list[OntologyClass] = []
    while mixin_queue:
        current = mixin_queue.pop(0)
        if any(current is v for v in mixin_visited):
            continue
        mixin_visited.append(current)
        for p in current.properties:
            if p.name not in seen:
                seen.add(p.name)
                result.append(p)
        mixin_queue.extend(current.mixins)

    return result


def stored_property_names(cls: OntologyClass | DefinedClass) -> list[str]:
    """Column names for the class's storage.

    Returns [] for DefinedClass — it is backed by a VIEW with no own columns.
    """
    if isinstance(cls, DefinedClass):
        return []
    return [p.name for p in effective_properties(cls) if is_stored(p)]
