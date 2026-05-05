"""Unit tests for knot.metaschema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from knot.metaschema import (
    BoolExpr,
    BoolOp,
    Compare,
    CompareOp,
    DirectRef,
    Literal_,
    OntologyClass,
    PermissibleValue,
    ResolutionPolicy,
    Slot,
    SlotPath,
    Spec,
    TypeDefinition,
)


# ---------------------------------------------------------------------------
# ResolutionPolicy
# ---------------------------------------------------------------------------

def test_resolution_policy_enum_values() -> None:
    values = {e.value for e in ResolutionPolicy}
    assert values == {
        "argmax_trust",
        "mode",
        "weighted_vote",
        "median_numeric",
        "latest_watermark",
        "unique_or_fail",
    }
    assert len(list(ResolutionPolicy)) == 6


# ---------------------------------------------------------------------------
# Slot defaults
# ---------------------------------------------------------------------------

def test_slot_default_resolution_policy() -> None:
    string_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="title", range=string_t)
    assert slot.resolution_policy == ResolutionPolicy.ARGMAX_TRUST


# ---------------------------------------------------------------------------
# Real object refs
# ---------------------------------------------------------------------------

def test_real_object_refs() -> None:
    string_t = TypeDefinition(name="string", base="str")
    person_name = Slot(name="name", range=string_t)
    Person = OntologyClass(name="Person", slots=[person_name])

    slot = Slot(name="lead", range=Person)
    assert isinstance(slot.range, OntologyClass)
    assert slot.range.name == "Person"
    # It is the same object, not a copy
    assert slot.range is Person


# ---------------------------------------------------------------------------
# extra="forbid"
# ---------------------------------------------------------------------------

def test_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        TypeDefinition(name="x", base="str", unknown_field="oops")  # type: ignore[call-arg]

    with pytest.raises(ValidationError):
        string_t = TypeDefinition(name="string", base="str")
        Slot(name="y", range=string_t, no_such_field=True)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# PermissibleValues shape
# ---------------------------------------------------------------------------

def test_permissible_values_mixed_case_rejection() -> None:
    # PermissibleValue must have text; no extra fields
    with pytest.raises(ValidationError):
        PermissibleValue(text="director", bogus="x")  # type: ignore[call-arg]

    # Slot accepts a list of PermissibleValue
    string_t = TypeDefinition(name="string", base="str")
    pv = PermissibleValue(text="director", description="Directed the work.")
    slot = Slot(name="role", range=string_t, permissible_values=[pv])
    assert slot.permissible_values is not None
    assert slot.permissible_values[0].text == "director"


# ---------------------------------------------------------------------------
# B2 fixture
# ---------------------------------------------------------------------------

def test_b2_spec_imports_and_validates() -> None:
    from tests.fixtures.B2 import spec as b2

    assert isinstance(b2.spec, Spec)
    assert b2.spec.id == "b2_integration"
    assert len(b2.spec.classes) == 3
    class_names = {c.name for c in b2.spec.classes}
    assert class_names == {"Movie", "Person", "Credit"}


# ---------------------------------------------------------------------------
# C2 fixture — subclass hierarchy
# ---------------------------------------------------------------------------

def test_c2_spec_imports_and_validates() -> None:
    from tests.fixtures.C2 import spec as c2

    assert isinstance(c2.spec, Spec)
    assert c2.spec.id == "c2_stress"

    # Movie.is_a is Title (real object ref, not a string)
    movie = next(c for c in c2.spec.classes if c.name == "Movie")
    title = next(c for c in c2.spec.classes if c.name == "Title")

    assert movie.is_a is title
    assert title.abstract is True


# ---------------------------------------------------------------------------
# Operator overloading: Slot.__eq__ → Compare node
# ---------------------------------------------------------------------------

def test_compare_operator_overloading() -> None:
    string_t = TypeDefinition(name="string", base="str")
    role = Slot(name="role", range=string_t)

    result = role == "director"
    assert isinstance(result, Compare)
    assert result.op == CompareOp.EQ
    assert isinstance(result.right, Literal_)
    assert result.right.value == "director"

    lt_result = role < 5
    assert isinstance(lt_result, Compare)
    assert lt_result.op == CompareOp.LT

    gt_result = role > 5
    assert isinstance(gt_result, Compare)
    assert gt_result.op == CompareOp.GT

    # Boolean composition via & and |
    a = role == "director"
    b = role == "actor"
    combined = a & b
    assert isinstance(combined, BoolExpr)
    assert combined.op == BoolOp.AND
    assert len(combined.operands) == 2

    negated = ~a
    assert isinstance(negated, BoolExpr)
    assert negated.op == BoolOp.NOT
