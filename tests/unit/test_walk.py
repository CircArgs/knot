"""Unit tests for knot.walk — walk_refs over each expression-tree node type."""

from __future__ import annotations

import pytest

from knot.metaschema import (
    Between,
    BoolExpr,
    BoolOp,
    Compare,
    CompareOp,
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
    TypeDefinition,
    Within,
    AggFunc,
)
from knot.walk import walk_refs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _refs(node) -> list:
    """Return walk_refs results as a list (identity-deduplicated by walk_refs itself)."""
    return list(walk_refs(node))


def _ref_set(node) -> set[int]:
    """Return the set of object ids from walk_refs — for membership tests."""
    return {id(r) for r in walk_refs(node)}


string_t = TypeDefinition(name="string", base="str")
int_t = TypeDefinition(name="integer", base="int")


def _cls(name: str, slots: list[Slot] | None = None) -> OntologyClass:
    return OntologyClass(name=name, slots=slots or [])


def _slot(name: str, range_=None) -> Slot:
    return Slot(name=name, range=range_ or string_t)


# ---------------------------------------------------------------------------
# Literal_ — no refs
# ---------------------------------------------------------------------------

def test_literal_yields_nothing() -> None:
    node = Literal_(value=42)
    assert list(walk_refs(node)) == []


# ---------------------------------------------------------------------------
# SlotPath — from_class + slots
# ---------------------------------------------------------------------------

def test_slotpath_yields_class_and_slot() -> None:
    cls = _cls("Movie")
    slot = _slot("title")
    node = SlotPath(from_class=cls, slots=[slot])
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


def test_slotpath_sentinel_class_skipped() -> None:
    from knot.metaschema import _sentinel_class
    slot = _slot("role")
    node = SlotPath(from_class=_sentinel_class, slots=[slot])
    refs = _refs(node)
    # Use identity checks — Slot.__eq__ returns a Compare node (not bool),
    # so 'in' on a list is unreliable for cross-type comparisons.
    assert not any(r is _sentinel_class for r in refs)
    assert any(r is slot for r in refs)


# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

def test_compare_walks_left_and_right() -> None:
    cls = _cls("Movie")
    slot = _slot("year")
    path = SlotPath(from_class=cls, slots=[slot])
    node = Compare(op=CompareOp.GT, left=path, right=Literal_(value=2000))
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


def test_compare_unary_right_none() -> None:
    cls = _cls("Movie")
    slot = _slot("title")
    path = SlotPath(from_class=cls, slots=[slot])
    node = Compare(op=CompareOp.IS_NULL, left=path, right=None)
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


# ---------------------------------------------------------------------------
# BoolExpr
# ---------------------------------------------------------------------------

def test_boolexpr_walks_all_operands() -> None:
    cls = _cls("Credit")
    role = _slot("role")
    year = _slot("year")
    path1 = SlotPath(from_class=cls, slots=[role])
    path2 = SlotPath(from_class=cls, slots=[year])
    c1 = Compare(op=CompareOp.EQ, left=path1, right=Literal_(value="director"))
    c2 = Compare(op=CompareOp.GT, left=path2, right=Literal_(value=2000))
    node = BoolExpr(op=BoolOp.AND, operands=[c1, c2])
    refs = _refs(node)
    assert role in refs
    assert year in refs
    assert cls in refs


# ---------------------------------------------------------------------------
# Within / Between / Matches
# ---------------------------------------------------------------------------

def test_within_yields_left_refs() -> None:
    cls = _cls("Movie")
    slot = _slot("genres")
    path = SlotPath(from_class=cls, slots=[slot])
    node = Within(left=path, values=[Literal_(value="Action"), Literal_(value="Drama")])
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


def test_between_yields_all_refs() -> None:
    cls = _cls("Movie")
    slot = _slot("year")
    path = SlotPath(from_class=cls, slots=[slot])
    node = Between(left=path, lower=Literal_(value=1990), upper=Literal_(value=2020))
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


def test_matches_yields_left_refs() -> None:
    cls = _cls("Person")
    slot = _slot("name")
    path = SlotPath(from_class=cls, slots=[slot])
    node = Matches(left=path, pattern="^[A-Z]")
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


# ---------------------------------------------------------------------------
# RelationRef
# ---------------------------------------------------------------------------

def test_relation_ref_yields_class_and_slot() -> None:
    credit = _cls("Credit")
    work_slot = _slot("work")
    node = RelationRef(from_class=credit, slot=work_slot)
    refs = _refs(node)
    assert credit in refs
    assert work_slot in refs


# ---------------------------------------------------------------------------
# FilteredRelation
# ---------------------------------------------------------------------------

def test_filtered_relation_walks_relation_and_filter() -> None:
    credit = _cls("Credit")
    work_slot = _slot("work")
    role_slot = _slot("role")
    rel = RelationRef(from_class=credit, slot=work_slot)
    filt = Compare(
        op=CompareOp.EQ,
        left=SlotPath(from_class=credit, slots=[role_slot]),
        right=Literal_(value="director"),
    )
    node = FilteredRelation(relation=rel, filter=filt)
    refs = _refs(node)
    assert credit in refs
    assert work_slot in refs
    assert role_slot in refs


# ---------------------------------------------------------------------------
# RelationProject
# ---------------------------------------------------------------------------

def test_relation_project_walks_relation_and_project() -> None:
    credit = _cls("Credit")
    work_slot = _slot("work")
    person_slot = _slot("person")
    rel = RelationRef(from_class=credit, slot=work_slot)
    project = SlotPath(from_class=credit, slots=[person_slot])
    node = RelationProject(relation=rel, project=project)
    refs = _refs(node)
    assert credit in refs
    assert work_slot in refs
    assert person_slot in refs


# ---------------------------------------------------------------------------
# RelationCount
# ---------------------------------------------------------------------------

def test_relation_count_walks_relation() -> None:
    credit = _cls("Credit")
    slot = _slot("work")
    rel = RelationRef(from_class=credit, slot=slot)
    node = RelationCount(relation=rel)
    refs = _refs(node)
    assert credit in refs
    assert slot in refs


# ---------------------------------------------------------------------------
# RelationAggregate
# ---------------------------------------------------------------------------

def test_relation_aggregate_walks_relation_and_operand() -> None:
    credit = _cls("Credit")
    work_slot = _slot("work")
    year_slot = _slot("year")
    rel = RelationRef(from_class=credit, slot=work_slot)
    operand = SlotPath(from_class=credit, slots=[year_slot])
    node = RelationAggregate(relation=rel, func=AggFunc.AVG, operand=operand)
    refs = _refs(node)
    assert credit in refs
    assert year_slot in refs


# ---------------------------------------------------------------------------
# RelationAny / RelationAll / RelationFirst
# ---------------------------------------------------------------------------

def test_relation_any_walks_relation() -> None:
    cls = _cls("Credit")
    slot = _slot("person")
    rel = RelationRef(from_class=cls, slot=slot)
    node = RelationAny(relation=rel)
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


def test_relation_all_walks_relation_and_body() -> None:
    cls = _cls("Credit")
    work_slot = _slot("work")
    role_slot = _slot("role")
    rel = RelationRef(from_class=cls, slot=work_slot)
    body = Compare(
        op=CompareOp.IS_NOT_NULL,
        left=SlotPath(from_class=cls, slots=[role_slot]),
    )
    node = RelationAll(relation=rel, body=body)
    refs = _refs(node)
    assert work_slot in refs
    assert role_slot in refs


def test_relation_first_walks_relation_project_order_by() -> None:
    cls = _cls("Credit")
    work_slot = _slot("work")
    person_slot = _slot("person")
    year_slot = _slot("year")
    rel = RelationRef(from_class=cls, slot=work_slot)
    project = SlotPath(from_class=cls, slots=[person_slot])
    order_by = [SlotPath(from_class=cls, slots=[year_slot])]
    node = RelationFirst(relation=rel, project=project, order_by=order_by)
    refs = _refs(node)
    assert work_slot in refs
    assert person_slot in refs
    assert year_slot in refs


# ---------------------------------------------------------------------------
# RecursiveTraversal
# ---------------------------------------------------------------------------

def test_recursive_traversal_walks_start_and_step() -> None:
    cls = _cls("Title")
    child_slot = _slot("children")
    name_slot = _slot("name")
    start = RelationRef(from_class=cls, slot=child_slot)
    step = SlotPath(from_class=cls, slots=[name_slot])
    node = RecursiveTraversal(start=start, step=step)
    refs = _refs(node)
    assert cls in refs
    assert child_slot in refs
    assert name_slot in refs


# ---------------------------------------------------------------------------
# ScalarDerivation / FormatDerivation
# ---------------------------------------------------------------------------

def test_scalar_derivation_walks_expression() -> None:
    cls = _cls("Movie")
    slot = _slot("year")
    path = SlotPath(from_class=cls, slots=[slot])
    node = ScalarDerivation(expression=path)
    refs = _refs(node)
    assert cls in refs
    assert slot in refs


def test_format_derivation_walks_slots() -> None:
    cls = _cls("Person")
    last = _slot("last_name")
    first = _slot("first_name")
    node = FormatDerivation(
        template="{last}, {first}",
        slots=[
            SlotPath(from_class=cls, slots=[last]),
            SlotPath(from_class=cls, slots=[first]),
        ],
    )
    refs = _refs(node)
    assert last in refs
    assert first in refs


# ---------------------------------------------------------------------------
# OntologyClass (top-level walk)
# ---------------------------------------------------------------------------

def test_walk_refs_on_ontology_class_yields_class_and_slots() -> None:
    slot_a = _slot("title")
    slot_b = _slot("year")
    cls = OntologyClass(name="Movie", slots=[slot_a, slot_b])
    refs = _refs(cls)
    assert cls in refs
    assert slot_a in refs
    assert slot_b in refs


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

def test_walk_refs_on_source_yields_source_class_and_slot() -> None:
    cls = _cls("Movie")
    id_slot = _slot("imdb_id")
    src = Source(name="imdb_movies", entity_class=cls, identifier_slot=id_slot)
    refs = _refs(src)
    assert src in refs
    assert cls in refs
    assert id_slot in refs


# ---------------------------------------------------------------------------
# No double-count — cycle / shared-object deduplication
# ---------------------------------------------------------------------------

def test_no_double_count_shared_slot_object() -> None:
    """The same Slot object referenced twice should appear once in results."""
    cls = _cls("Movie")
    slot = _slot("year")
    path1 = SlotPath(from_class=cls, slots=[slot])
    path2 = SlotPath(from_class=cls, slots=[slot])
    c1 = Compare(op=CompareOp.GT, left=path1, right=Literal_(value=1990))
    c2 = Compare(op=CompareOp.LT, left=path2, right=Literal_(value=2020))
    node = BoolExpr(op=BoolOp.AND, operands=[c1, c2])
    result_list = list(walk_refs(node))
    # slot should appear exactly once — count by identity
    slot_count = sum(1 for r in result_list if r is slot)
    assert slot_count == 1


# ---------------------------------------------------------------------------
# Unknown node type raises NotImplementedError
# ---------------------------------------------------------------------------

def test_unregistered_type_raises() -> None:
    class UnknownNode:
        pass

    with pytest.raises(NotImplementedError, match="UnknownNode"):
        list(walk_refs(UnknownNode()))
