"""knot_graphql.resolvers — resolver template tests.

These tests verify that resolvers build the correct knot Query AST and
pass the right SQL to the executor.  The executor is a simple spy
(returns a fixed list); we assert on the SQL string it received.
"""

import pytest

from knot import Spec, types
from knot_graphql.resolvers import Resolvers, projection_from_selection

# ---------------------------------------------------------------------------
# Shared spec + executor spy
# ---------------------------------------------------------------------------


@pytest.fixture
def spec():
    s = Spec(identifier_slot_name="canonical_id")

    person = s.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_year", types.INTEGER)

    movie = s.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)

    credit = s.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)

    return s


class SqlSpy:
    """Records every SQL string handed to the executor."""

    def __init__(self, rows: list[dict] | None = None):
        self.calls: list[str] = []
        self._rows = rows or []

    def __call__(self, sql: str) -> list[dict]:
        self.calls.append(sql)
        return self._rows


# ---------------------------------------------------------------------------
# projection_from_selection
# ---------------------------------------------------------------------------


def test_projection_returns_refs_for_known_slots(spec):
    movie = spec.classes["Movie"]
    refs = projection_from_selection(["title", "year"], movie)
    assert len(refs) == 2
    names = [r.slot_name for r in refs]
    assert "title" in names
    assert "year" in names


def test_projection_skips_unknown_field_names(spec):
    movie = spec.classes["Movie"]
    # "movieCount" is a reverse-FK count field — not a spec slot.
    refs = projection_from_selection(["title", "movieCount", "year"], movie)
    assert len(refs) == 2


def test_projection_fk_slot_returns_fk_ref(spec):
    movie = spec.classes["Movie"]
    refs = projection_from_selection(["director"], movie)
    assert len(refs) == 1
    from knot.ast.expr import FkRef

    assert isinstance(refs[0], FkRef)


# ---------------------------------------------------------------------------
# resolve_by_id
# ---------------------------------------------------------------------------


def test_resolve_by_id_queries_resolved_layer(spec):
    spy = SqlSpy(rows=[{"canonical_id": "m_001", "title": "Pulp Fiction"}])
    r = Resolvers(spec, spy)
    result = r.resolve_by_id("Movie", "m_001")
    assert result == {"canonical_id": "m_001", "title": "Pulp Fiction"}
    assert len(spy.calls) == 1
    sql = spy.calls[0]
    assert "movie_resolved" in sql
    assert "m_001" in sql


def test_resolve_by_id_returns_none_when_not_found(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    assert r.resolve_by_id("Movie", "missing") is None


# ---------------------------------------------------------------------------
# resolve_list
# ---------------------------------------------------------------------------


def test_resolve_list_applies_limit(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    r.resolve_list("Movie", first=5)
    sql = spy.calls[0]
    assert "LIMIT 5" in sql


def test_resolve_list_applies_after_cursor(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    r.resolve_list("Movie", first=10, after="m_010")
    sql = spy.calls[0]
    assert "m_010" in sql


def test_resolve_list_applies_equality_filter(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    r.resolve_list("Movie", first=10, filters={"year": 1994})
    sql = spy.calls[0]
    assert "1994" in sql


def test_resolve_list_targets_resolved_layer(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    r.resolve_list("Person", first=20)
    sql = spy.calls[0]
    assert "person_resolved" in sql


# ---------------------------------------------------------------------------
# resolve_fk_walk
# ---------------------------------------------------------------------------


def test_resolve_fk_walk_returns_none_for_null_fk(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    parent = {"canonical_id": "c_001", "director": None, "__class_name": "Movie"}
    result = r.resolve_fk_walk(parent, "director")
    assert result is None
    # No SQL executed since FK is null.
    assert len(spy.calls) == 0


def test_resolve_fk_walk_queries_target_class(spec):
    spy = SqlSpy(rows=[{"canonical_id": "p_001", "name": "Tarantino"}])
    r = Resolvers(spec, spy)
    parent = {
        "canonical_id": "m_001",
        "director": "p_001",
        "__class_name": "Movie",
    }
    result = r.resolve_fk_walk(parent, "director")
    assert result == {"canonical_id": "p_001", "name": "Tarantino"}
    sql = spy.calls[0]
    assert "person_resolved" in sql
    assert "p_001" in sql


# ---------------------------------------------------------------------------
# resolve_reverse_count
# ---------------------------------------------------------------------------


def test_resolve_reverse_count_uses_back(spec):
    spy = SqlSpy(rows=[{"count": 3}])
    r = Resolvers(spec, spy)
    parent = {"canonical_id": "p_001", "__class_name": "Person"}
    count = r.resolve_reverse_count(parent, "Person", "person", "Credit")
    assert count == 3
    sql = spy.calls[0]
    # The compiled SQL should reference the person's resolved view and
    # contain a COUNT subquery over credit_resolved.
    assert "person_resolved" in sql


def test_resolve_reverse_count_returns_zero_when_no_id(spec):
    spy = SqlSpy(rows=[])
    r = Resolvers(spec, spy)
    parent = {"canonical_id": None}
    count = r.resolve_reverse_count(parent, "Person", "person", "Credit")
    assert count == 0
    assert len(spy.calls) == 0
