"""Constraint inheritance — walk the is_a chain and mixins.

A constraint with ``primary=MediaItem`` applies to ``MediaItem`` *and* every
class that has ``MediaItem`` reachable via is_a or mixin. The walk mirrors
``effective_properties``: own → is_a chain → mixin chain.

For DefinedClass: inherits constraints from its is_a parent + mixins. The
class itself can't be a constraint primary (``Constraint.primary`` is typed
``OntologyClass``).
"""

from __future__ import annotations

from knot.spec.metaschema import Constraint, DefinedClass, OntologyClass, Spec


def effective_constraints(
    cls: OntologyClass | DefinedClass, spec: Spec
) -> list[Constraint]:
    """All constraints that apply to ``cls`` via own + is_a + mixin chains.

    Deduplicated by constraint name (so a constraint reachable via both is_a
    and mixin lands once). Order: own constraints first, then ancestors
    (breadth-first up is_a), then mixins (breadth-first).
    """
    ancestors = _ancestor_classes(cls)
    ancestor_names = {c.name for c in ancestors}

    seen: set[str] = set()
    result: list[Constraint] = []
    for c in spec.constraints:
        if c.primary.name not in ancestor_names:
            continue
        if c.name in seen:
            continue
        seen.add(c.name)
        result.append(c)
    return result


def _ancestor_classes(
    cls: OntologyClass | DefinedClass,
) -> list[OntologyClass]:
    """Every OntologyClass reachable from ``cls`` via is_a or mixins (incl. self)."""
    result: list[OntologyClass] = []
    visited: list[OntologyClass] = []

    if isinstance(cls, DefinedClass):
        isa_queue: list[OntologyClass] = [cls.is_a]
        mixin_queue: list[OntologyClass] = list(cls.mixins)
    else:
        isa_queue = [cls]
        mixin_queue = []

    while isa_queue:
        current = isa_queue.pop(0)
        if any(current is v for v in visited):
            continue
        visited.append(current)
        result.append(current)
        mixin_queue.extend(current.mixins)
        if current.is_a is not None:
            isa_queue.append(current.is_a)

    while mixin_queue:
        current = mixin_queue.pop(0)
        if any(current is v for v in visited):
            continue
        visited.append(current)
        result.append(current)
        mixin_queue.extend(current.mixins)

    return result
