"""Single-dispatch visitor over the knot expression tree.

walk_refs(node) yields every OntologyClass / Slot / Source / DerivedSlot
reference the node touches.  One handler per node type — missing handlers
raise loudly (NotImplementedError).

Used by:
- registration validation (validate_datacontexts)
- impact analysis (datacontext_refs, affected_workflows)
- compile-network determination

Per core-design.md commitment 10: one canonical form, one impact-analysis
visitor.  No parallel meta-structure.
"""

from __future__ import annotations

from functools import singledispatch
from typing import Iterator

from knot.metaschema import (
    Between,
    BoolExpr,
    Compare,
    FilteredRelation,
    FormatDerivation,
    Literal_,
    Matches,
    OntologyClass,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ScalarDerivation,
    Slot,
    SlotPath,
    Source,
    Spec,
    Within,
)

# Type alias for what walk_refs yields.
Ref = OntologyClass | Slot | Source


@singledispatch
def walk_refs(node: object, visited: set[int] | None = None) -> Iterator[Ref]:
    """Yield every spec entity reference this expression-tree node touches.

    Dispatch is on the concrete type of *node*.  Unregistered types raise
    NotImplementedError so newly added metaschema nodes are caught immediately.
    """
    raise NotImplementedError(
        f"No walk_refs handler for {type(node).__name__}. "
        "Add a @walk_refs.register handler in knot/walk.py."
    )


def _v(visited: set[int] | None) -> set[int]:
    """Return visited set, initialising to empty if None."""
    return set() if visited is None else visited


def _guard(node: object, visited: set[int]) -> bool:
    """Return True (and mark visited) if node is new; False if already seen."""
    nid = id(node)
    if nid in visited:
        return False
    visited.add(nid)
    return True


# ---------------------------------------------------------------------------
# Leaf nodes
# ---------------------------------------------------------------------------

@walk_refs.register
def _(node: Literal_, visited: set[int] | None = None) -> Iterator[Ref]:
    return
    yield  # make generator


@walk_refs.register
def _(node: SlotPath, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    if node.from_class.name != "__sentinel__":
        yield node.from_class
    for slot in node.slots:
        yield from _walk_slot(slot, v)


# ---------------------------------------------------------------------------
# Predicate nodes
# ---------------------------------------------------------------------------

@walk_refs.register
def _(node: Compare, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.left, v)
    if node.right is not None:
        yield from walk_refs(node.right, v)


@walk_refs.register
def _(node: BoolExpr, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    for operand in node.operands:
        yield from walk_refs(operand, v)


@walk_refs.register
def _(node: Within, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.left, v)
    for val in node.values:
        yield from walk_refs(val, v)


@walk_refs.register
def _(node: Between, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.left, v)
    yield from walk_refs(node.lower, v)
    yield from walk_refs(node.upper, v)


@walk_refs.register
def _(node: Matches, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.left, v)


# ---------------------------------------------------------------------------
# Relation nodes
# ---------------------------------------------------------------------------

@walk_refs.register
def _(node: RelationRef, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield node.from_class
    yield from _walk_slot(node.slot, v)


@walk_refs.register
def _(node: FilteredRelation, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)
    yield from walk_refs(node.filter, v)


@walk_refs.register
def _(node: RelationProject, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)
    yield from walk_refs(node.project, v)


@walk_refs.register
def _(node: RelationCount, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)


@walk_refs.register
def _(node: RelationAggregate, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)
    if node.operand is not None:
        yield from walk_refs(node.operand, v)
    for ob in node.order_by:
        yield from walk_refs(ob, v)


@walk_refs.register
def _(node: RelationAny, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)


@walk_refs.register
def _(node: RelationAll, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)
    if node.body is not None:
        yield from walk_refs(node.body, v)


@walk_refs.register
def _(node: RelationFirst, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.relation, v)
    yield from walk_refs(node.project, v)
    for ob in node.order_by:
        yield from walk_refs(ob, v)


@walk_refs.register
def _(node: RecursiveTraversal, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.start, v)
    yield from walk_refs(node.step, v)
    if node.until is not None:
        yield from walk_refs(node.until, v)


# ---------------------------------------------------------------------------
# Derivation nodes
# ---------------------------------------------------------------------------

@walk_refs.register
def _(node: ScalarDerivation, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield from walk_refs(node.expression, v)


@walk_refs.register
def _(node: FormatDerivation, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    for sp in node.slots:
        yield from walk_refs(sp, v)


# ---------------------------------------------------------------------------
# Spec-level nodes
# ---------------------------------------------------------------------------

@walk_refs.register
def _(node: OntologyClass, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield node
    for slot in node.slots:
        yield from _walk_slot(slot, v)


@walk_refs.register
def _(node: Slot, visited: set[int] | None = None) -> Iterator[Ref]:
    yield from _walk_slot(node, _v(visited))


@walk_refs.register
def _(node: Source, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    if not _guard(node, v):
        return
    yield node
    yield node.entity_class
    yield from _walk_slot(node.identifier_slot, v)


@walk_refs.register
def _(node: Spec, visited: set[int] | None = None) -> Iterator[Ref]:
    v = _v(visited)
    for cls in node.classes:
        yield from walk_refs(cls, v)


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _walk_slot(slot: Slot, visited: set[int]) -> Iterator[Ref]:
    """Yield the slot, its OntologyClass range if any, and walk its derivation."""
    if not _guard(slot, visited):
        return
    yield slot
    if isinstance(slot.range, OntologyClass):
        yield slot.range
    if slot.derivation is not None:
        yield from walk_refs(slot.derivation, visited)
