"""Unit tests for knot.registration — validate_datacontexts at impl registration."""

from __future__ import annotations

import pytest

from knot.metaschema import (
    OntologyClass,
    Slot,
    Spec,
    TypeDefinition,
)
from knot.protocols import DataContext
from knot.registration import DataContextValidationError, validate_datacontexts


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
int_t = TypeDefinition(name="integer", base="int")


def _slot(name: str, range_=None) -> Slot:
    return Slot(name=name, range=range_ or string_t)


def _cls(name: str, slots: list[Slot] | None = None) -> OntologyClass:
    return OntologyClass(name=name, slots=slots or [])


def _spec(*classes: OntologyClass) -> Spec:
    all_slots = [s for c in classes for s in c.slots]
    return Spec(
        id="test_spec",
        version="0.1.0",
        classes=list(classes),
        slots=all_slots,
    )


# ---------------------------------------------------------------------------
# Valid impl — no error
# ---------------------------------------------------------------------------

def test_valid_impl_no_error() -> None:
    Movie = _cls("Movie", slots=[_slot("title"), _slot("year", int_t)])
    spec = _spec(Movie)

    class MyImpl:
        movies: DataContext = DataContext(primary=Movie)

    validate_datacontexts(MyImpl, spec)  # should not raise


def test_valid_impl_multiple_datacontexts() -> None:
    Movie = _cls("Movie", slots=[_slot("title")])
    Person = _cls("Person", slots=[_slot("name")])
    spec = _spec(Movie, Person)

    class MyImpl:
        movies: DataContext = DataContext(primary=Movie)
        persons: DataContext = DataContext(primary=Person)

    validate_datacontexts(MyImpl, spec)  # should not raise


# ---------------------------------------------------------------------------
# Broken class reference — raises with class + impl context
# ---------------------------------------------------------------------------

def test_broken_class_ref_raises() -> None:
    Ghost = OntologyClass(name="Ghost", slots=[])  # not in spec
    Movie = _cls("Movie", slots=[_slot("title")])
    spec = _spec(Movie)

    class BrokenImpl:
        ghosts: DataContext = DataContext(primary=Ghost)

    with pytest.raises(DataContextValidationError) as exc_info:
        validate_datacontexts(BrokenImpl, spec)

    err = exc_info.value
    assert err.impl_name == "BrokenImpl"
    assert err.datacontext_attr == "ghosts"
    assert err.ref_type == "OntologyClass"
    assert err.ref_name == "Ghost"
    assert "BrokenImpl" in str(err)
    assert "Ghost" in str(err)


# ---------------------------------------------------------------------------
# Broken slot reference — raises with slot context
# ---------------------------------------------------------------------------

def test_broken_slot_ref_raises() -> None:
    phantom_slot = _slot("phantom_field")  # not in spec
    Movie = _cls("Movie", slots=[_slot("title")])
    spec = _spec(Movie)

    class BrokenImpl:
        movies: DataContext = DataContext(primary=Movie, project=[phantom_slot])

    with pytest.raises(DataContextValidationError) as exc_info:
        validate_datacontexts(BrokenImpl, spec)

    err = exc_info.value
    assert err.ref_name == "phantom_field"
    assert "phantom_field" in str(err)


# ---------------------------------------------------------------------------
# did-you-mean suggestion for near-miss class name
# ---------------------------------------------------------------------------

def test_did_you_mean_for_near_miss_class() -> None:
    Movei = OntologyClass(name="Movei", slots=[])  # typo
    Movie = _cls("Movie", slots=[_slot("title")])
    spec = _spec(Movie)

    class TypoImpl:
        movies: DataContext = DataContext(primary=Movei)

    with pytest.raises(DataContextValidationError) as exc_info:
        validate_datacontexts(TypoImpl, spec)

    err = exc_info.value
    assert err.did_you_mean == "Movie"
    assert "Movie" in str(err)


# ---------------------------------------------------------------------------
# No DataContext attributes — valid (nothing to validate)
# ---------------------------------------------------------------------------

def test_impl_with_no_datacontexts_passes() -> None:
    Movie = _cls("Movie")
    spec = _spec(Movie)

    class NoDataContextImpl:
        some_method = lambda self: None  # noqa: E731

    validate_datacontexts(NoDataContextImpl, spec)  # should not raise


# ---------------------------------------------------------------------------
# Error message includes impl + attr path
# ---------------------------------------------------------------------------

def test_error_message_format() -> None:
    Ghost = OntologyClass(name="Specter", slots=[])
    spec = _spec(_cls("Movie"))

    class MyImpl:
        specter_view: DataContext = DataContext(primary=Ghost)

    with pytest.raises(DataContextValidationError) as exc_info:
        validate_datacontexts(MyImpl, spec)

    msg = str(exc_info.value)
    assert "MyImpl" in msg
    assert "specter_view" in msg
    assert "Specter" in msg


# ---------------------------------------------------------------------------
# B2 fixture impl validates cleanly against B2 spec
# ---------------------------------------------------------------------------

def test_b2_er_movie_validates_against_b2_spec() -> None:
    from tests.fixtures.B2.spec import spec as b2_spec
    from tests.fixtures.B2.impls.er_movie import ERMovie

    validate_datacontexts(ERMovie, b2_spec)  # should not raise
