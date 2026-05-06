"""Tests for Within, Between, Matches, RecursiveTraversal, and SDK ergonomics."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from knot.metaschema import (
    Between,
    BoolExpr,
    BoolOp,
    Literal_,
    Matches,
    OntologyClass,
    RecursiveTraversal,
    RelationProject,
    RelationRef,
    Slot,
    SlotPath,
    TypeDefinition,
    Within,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
integer_t = TypeDefinition(name="integer", base="int")

movie_year = Slot(name="year", range=integer_t)
movie_genres = Slot(name="genres", range=string_t, multivalued=True)
movie_title = Slot(name="title", range=string_t)
movie_imdb_id = Slot(name="imdb_id", range=string_t, identifier=True)

Movie = OntologyClass(
    name="Movie",
    slots=[movie_year, movie_genres, movie_title, movie_imdb_id],
)

person_name = Slot(name="name", range=string_t)
person_knows = Slot(name="knows", range=None)  # range patched below
Person = OntologyClass(name="Person", slots=[person_name, person_knows])
person_knows.range = Person

Title = OntologyClass(name="Title", abstract=True, slots=[])
SubMovie = OntologyClass(name="SubMovie", is_a=Title, slots=[])


# ---------------------------------------------------------------------------
# Within — direct construction
# ---------------------------------------------------------------------------

def test_within_direct_construction() -> None:
    path = SlotPath(from_class=Movie, slots=[movie_genres])
    node = Within(left=path, values=[Literal_(value="Action"), Literal_(value="Sci-Fi")])
    assert node.op == "within"
    assert len(node.values) == 2
    assert node.values[0].value == "Action"


def test_within_extra_forbid() -> None:
    path = SlotPath(from_class=Movie, slots=[movie_genres])
    with pytest.raises(ValidationError):
        Within(left=path, values=[], unknown="x")  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Within — SDK ergonomics
# ---------------------------------------------------------------------------

def test_within_sdk() -> None:
    node = movie_genres.within(["Action", "Sci-Fi"])
    assert isinstance(node, Within)
    assert node.op == "within"
    assert [v.value for v in node.values] == ["Action", "Sci-Fi"]
    assert node.left.slots == [movie_genres]


def test_within_empty_list() -> None:
    node = movie_genres.within([])
    assert isinstance(node, Within)
    assert node.values == []


# ---------------------------------------------------------------------------
# Between — direct construction
# ---------------------------------------------------------------------------

def test_between_direct_construction() -> None:
    path = SlotPath(from_class=Movie, slots=[movie_year])
    node = Between(
        left=path,
        lower=Literal_(value=1990),
        upper=Literal_(value=2000),
    )
    assert node.op == "between"
    assert node.lower.value == 1990
    assert node.upper.value == 2000
    assert node.inclusive is True  # default


def test_between_non_inclusive() -> None:
    path = SlotPath(from_class=Movie, slots=[movie_year])
    node = Between(
        left=path,
        lower=Literal_(value=1990),
        upper=Literal_(value=2000),
        inclusive=False,
    )
    assert node.inclusive is False


# ---------------------------------------------------------------------------
# Between — SDK ergonomics
# ---------------------------------------------------------------------------

def test_between_sdk_inclusive() -> None:
    node = movie_year.between(1990, 2000)
    assert isinstance(node, Between)
    assert node.op == "between"
    assert node.lower.value == 1990
    assert node.upper.value == 2000
    assert node.inclusive is True
    assert node.left.slots == [movie_year]


def test_between_sdk_exclusive() -> None:
    node = movie_year.between(1990, 2000, inclusive=False)
    assert isinstance(node, Between)
    assert node.inclusive is False


# ---------------------------------------------------------------------------
# Matches — direct construction
# ---------------------------------------------------------------------------

def test_matches_direct_construction() -> None:
    path = SlotPath(from_class=Movie, slots=[movie_imdb_id])
    node = Matches(left=path, pattern=r"^tt\d+")
    assert node.op == "matches"
    assert node.pattern == r"^tt\d+"


# ---------------------------------------------------------------------------
# Matches — SDK ergonomics
# ---------------------------------------------------------------------------

def test_matches_sdk() -> None:
    node = movie_imdb_id.matches(r"^tt\d+")
    assert isinstance(node, Matches)
    assert node.pattern == r"^tt\d+"
    assert node.left.slots == [movie_imdb_id]


def test_starts_with_sdk() -> None:
    node = movie_imdb_id.starts_with("tt")
    assert isinstance(node, Matches)
    assert node.pattern == "tt%"


def test_ends_with_sdk() -> None:
    node = movie_title.ends_with(".jpg")
    assert isinstance(node, Matches)
    assert node.pattern == "%.jpg"


# ---------------------------------------------------------------------------
# RecursiveTraversal — direct construction
# ---------------------------------------------------------------------------

def test_recursive_traversal_direct() -> None:
    knows_ref = RelationRef(from_class=Person, slot=person_knows)
    step = SlotPath(from_class=Person, slots=[person_knows])
    node = RecursiveTraversal(start=knows_ref, step=step, max_depth=3)
    assert node.op == "recursive"
    assert node.max_depth == 3
    assert node.until is None


def test_recursive_traversal_with_until() -> None:
    from knot.metaschema import Compare, CompareOp

    knows_ref = RelationRef(from_class=Person, slot=person_knows)
    step = SlotPath(from_class=Person, slots=[person_knows])
    stop = BoolExpr(
        op=BoolOp.NOT,
        operands=[
            Compare(
                op=CompareOp.IS_NULL,
                left=SlotPath(from_class=Person, slots=[person_name]),
            )
        ],
    )
    node = RecursiveTraversal(start=knows_ref, step=step, until=stop)
    assert node.until is not None
    assert node.max_depth is None


# ---------------------------------------------------------------------------
# RecursiveTraversal — SDK: RelationRef.transitive()
# ---------------------------------------------------------------------------

def test_transitive_sdk() -> None:
    knows_ref = RelationRef(from_class=Person, slot=person_knows)
    node = knows_ref.transitive(max_depth=3)
    assert isinstance(node, RecursiveTraversal)
    assert node.op == "recursive"
    assert node.max_depth == 3
    assert node.start is knows_ref
    assert node.step.slots == [person_knows]


def test_transitive_no_depth() -> None:
    knows_ref = RelationRef(from_class=Person, slot=person_knows)
    node = knows_ref.transitive()
    assert isinstance(node, RecursiveTraversal)
    assert node.max_depth is None


# ---------------------------------------------------------------------------
# RecursiveTraversal — SDK: OntologyClass.descendants()
# ---------------------------------------------------------------------------

def test_descendants_sdk() -> None:
    node = Title.descendants()
    assert isinstance(node, RecursiveTraversal)
    assert node.op == "recursive"
    assert node.start.from_class is Title
    assert node.step.from_class is Title
    assert node.max_depth is None


def test_descendants_with_max_depth() -> None:
    node = Title.descendants(max_depth=2)
    assert isinstance(node, RecursiveTraversal)
    assert node.max_depth == 2


# ---------------------------------------------------------------------------
# Boolean composition on new nodes
# ---------------------------------------------------------------------------

def test_within_bool_compose() -> None:
    a = movie_genres.within(["Action"])
    b = movie_year.between(1990, 2000)
    combined = a & b
    assert isinstance(combined, BoolExpr)
    assert combined.op == BoolOp.AND
    assert len(combined.operands) == 2


def test_between_invert() -> None:
    node = movie_year.between(1990, 2000)
    negated = ~node
    assert isinstance(negated, BoolExpr)
    assert negated.op == BoolOp.NOT


def test_matches_or_compose() -> None:
    a = movie_imdb_id.starts_with("tt")
    b = movie_imdb_id.starts_with("nm")
    combined = a | b
    assert isinstance(combined, BoolExpr)
    assert combined.op == BoolOp.OR


# ---------------------------------------------------------------------------
# RelationProject.select() — multi-slot projection
# ---------------------------------------------------------------------------

def test_relation_project_select() -> None:
    from knot.metaschema import FilteredRelation, Compare, CompareOp

    credits_slot = Slot(name="credits", range=Movie)
    Credit = OntologyClass(name="Credit", slots=[credits_slot])
    rel = RelationRef(from_class=Credit, slot=credits_slot)
    base_project = RelationProject(
        relation=rel,
        project=SlotPath(from_class=Credit, slots=[credits_slot]),
    )
    projections = base_project.select(movie_title, movie_year)
    assert len(projections) == 2
    assert all(isinstance(p, RelationProject) for p in projections)
    assert projections[0].project.slots == [movie_title]
    assert projections[1].project.slots == [movie_year]
    # All share the same relation
    assert all(p.relation is rel for p in projections)
