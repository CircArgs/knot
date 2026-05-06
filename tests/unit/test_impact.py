"""Unit tests for knot.impact — affected_workflows and datacontext_refs."""

from __future__ import annotations

from knot.metaschema import (
    Compare,
    CompareOp,
    FilteredRelation,
    Literal_,
    OntologyClass,
    RelationProject,
    RelationRef,
    Slot,
    SlotPath,
    Source,
    TypeDefinition,
)
from knot.protocols import DataContext
from knot.impact import datacontext_refs, affected_workflows, BoundImpl

from tests.test_env import SpecEdit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
int_t = TypeDefinition(name="integer", base="int")


def _cls(name: str, slots: list[Slot] | None = None) -> OntologyClass:
    return OntologyClass(name=name, slots=slots or [])


def _slot(name: str, range_=None) -> Slot:
    return Slot(name=name, range=range_ or string_t)


# ---------------------------------------------------------------------------
# datacontext_refs
# ---------------------------------------------------------------------------

def test_datacontext_refs_single_class() -> None:
    Movie = _cls("Movie")
    dc = DataContext(primary=Movie)
    refs = datacontext_refs(dc)
    assert Movie in refs


def test_datacontext_refs_list_of_classes() -> None:
    Movie = _cls("Movie")
    Person = _cls("Person")
    dc = DataContext(primary=[Movie, Person])
    refs = datacontext_refs(dc)
    assert Movie in refs
    assert Person in refs


def test_datacontext_refs_where_clause() -> None:
    Credit = _cls("Credit")
    role = _slot("role")
    path = SlotPath(from_class=Credit, slots=[role])
    filt = Compare(op=CompareOp.EQ, left=path, right=Literal_(value="director"))
    dc = DataContext(primary=Credit, where=filt)
    refs = datacontext_refs(dc)
    assert Credit in refs
    assert role in refs


def test_datacontext_refs_project() -> None:
    Movie = _cls("Movie")
    title = _slot("title")
    year = _slot("year")
    path = SlotPath(from_class=Movie, slots=[title])
    dc = DataContext(primary=Movie, project=[path, year])
    refs = datacontext_refs(dc)
    assert title in refs
    assert year in refs


def test_datacontext_refs_derived_slot_primary() -> None:
    Person = _cls("Person")
    director_slot = _slot("director")
    dc = DataContext(primary=director_slot)
    refs = datacontext_refs(dc)
    assert director_slot in refs


# ---------------------------------------------------------------------------
# affected_workflows — add_slot
# ---------------------------------------------------------------------------

def _make_movie_impl(Movie: OntologyClass) -> type:
    class MovieImpl:
        movies: DataContext = DataContext(primary=Movie)
    return MovieImpl


def _make_binding(impl_class: type, name: str, workflow: str) -> BoundImpl:
    return BoundImpl(impl_class=impl_class, impl_name=name, workflow=workflow)


def test_add_slot_affects_impl_with_that_class() -> None:
    Movie = _cls("Movie", slots=[_slot("title")])
    impl_cls = _make_movie_impl(Movie)
    binding = _make_binding(impl_cls, "er_movie", "Movie")

    edit = SpecEdit.add_slot(class_="Movie", slot={"name": "budget"})
    impact = affected_workflows(edit, [binding])

    assert "Movie" in impact.affected_classes
    assert "er_movie" in impact.affected_impls
    assert "Movie" in impact.affected_workflows


def test_add_slot_does_not_affect_unrelated_impl() -> None:
    Person = _cls("Person", slots=[_slot("name")])

    class PersonImpl:
        persons: DataContext = DataContext(primary=Person)

    binding = _make_binding(PersonImpl, "er_person", "Person")

    edit = SpecEdit.add_slot(class_="Movie", slot={"name": "budget"})
    impact = affected_workflows(edit, [binding])

    assert "er_person" not in impact.affected_impls


# ---------------------------------------------------------------------------
# affected_workflows — delete_slot
# ---------------------------------------------------------------------------

def test_delete_slot_flags_impl_referencing_that_class() -> None:
    Movie = _cls("Movie", slots=[_slot("title"), _slot("year")])
    impl_cls = _make_movie_impl(Movie)
    binding = _make_binding(impl_cls, "er_movie", "Movie")

    edit = SpecEdit.delete_slot("Movie.year")
    impact = affected_workflows(edit, [binding])

    assert "Movie" in impact.affected_classes
    assert "er_movie" in impact.affected_impls


# ---------------------------------------------------------------------------
# affected_workflows — rename_slot
# ---------------------------------------------------------------------------

def test_rename_slot_flags_impl_using_that_class() -> None:
    Movie = _cls("Movie", slots=[_slot("title")])
    impl_cls = _make_movie_impl(Movie)
    binding = _make_binding(impl_cls, "er_movie", "Movie")

    edit = SpecEdit.rename_slot("Movie.title", "Movie.display_title")
    impact = affected_workflows(edit, [binding])

    assert "Movie" in impact.affected_classes
    assert "er_movie" in impact.affected_impls


# ---------------------------------------------------------------------------
# affected_workflows — add_class
# ---------------------------------------------------------------------------

def test_add_class_affects_impl_using_parent_class() -> None:
    Title = _cls("Title")

    class TitleImpl:
        titles: DataContext = DataContext(primary=Title)

    binding = _make_binding(TitleImpl, "er_title", "Title")

    edit = SpecEdit.add_class(class_def={"name": "Podcast"}, is_a="Title")
    impact = affected_workflows(edit, [binding])

    # Title is the parent; it's in the edit target classes
    assert "Title" in impact.affected_classes


def test_add_class_leaf_does_not_affect_unrelated_impl() -> None:
    Person = _cls("Person")

    class PersonImpl:
        persons: DataContext = DataContext(primary=Person)

    binding = _make_binding(PersonImpl, "er_person", "Person")
    edit = SpecEdit.add_class(class_def={"name": "Book"})
    impact = affected_workflows(edit, [binding])

    # Person impl doesn't touch Book or None parent
    assert "er_person" not in impact.affected_impls


# ---------------------------------------------------------------------------
# affected_workflows — rename_class
# ---------------------------------------------------------------------------

def test_rename_class_flags_impl_using_that_class() -> None:
    Movie = _cls("Movie")
    impl_cls = _make_movie_impl(Movie)
    binding = _make_binding(impl_cls, "er_movie", "Movie")

    edit = SpecEdit.rename_class("Movie", "Film")
    impact = affected_workflows(edit, [binding])

    assert "Movie" in impact.affected_classes
    assert "er_movie" in impact.affected_impls


# ---------------------------------------------------------------------------
# affected_workflows — remove_class
# ---------------------------------------------------------------------------

def test_remove_class_flags_impl_using_that_class() -> None:
    Movie = _cls("Movie")
    impl_cls = _make_movie_impl(Movie)
    binding = _make_binding(impl_cls, "er_movie", "Movie")

    edit = SpecEdit.remove_class("Movie")
    impact = affected_workflows(edit, [binding])

    assert "Movie" in impact.affected_classes
    assert "er_movie" in impact.affected_impls


# ---------------------------------------------------------------------------
# affected_workflows — where clause slot ref surfaced
# ---------------------------------------------------------------------------

def test_where_clause_slot_ref_triggers_impact() -> None:
    """Delete a slot that appears only in the DataContext where-clause."""
    Credit = _cls("Credit")
    role = _slot("role")
    path = SlotPath(from_class=Credit, slots=[role])
    filt = Compare(op=CompareOp.EQ, left=path, right=Literal_(value="director"))

    class CreditImpl:
        directors: DataContext = DataContext(primary=Credit, where=filt)

    binding = _make_binding(CreditImpl, "er_credit", "Credit")
    edit = SpecEdit.delete_slot("Credit.role")
    impact = affected_workflows(edit, [binding])

    assert "Credit" in impact.affected_classes
    assert "er_credit" in impact.affected_impls


# ---------------------------------------------------------------------------
# Multiple bindings — only affected ones flagged
# ---------------------------------------------------------------------------

def test_multiple_bindings_only_affected_flagged() -> None:
    Movie = _cls("Movie")
    Person = _cls("Person")

    class MovieImpl:
        movies: DataContext = DataContext(primary=Movie)

    class PersonImpl:
        persons: DataContext = DataContext(primary=Person)

    bindings = [
        _make_binding(MovieImpl, "er_movie", "Movie"),
        _make_binding(PersonImpl, "er_person", "Person"),
    ]
    edit = SpecEdit.delete_slot("Movie.year")
    impact = affected_workflows(edit, bindings)

    assert "er_movie" in impact.affected_impls
    assert "er_person" not in impact.affected_impls
