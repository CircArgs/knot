"""Feature 3 — OntologyClass.back() reverse-FK navigator."""

import pytest

from knot import Spec, types
from knot.ast.expr import CountRel, Exists
from knot.ast.select import Layer
from knot.compile.expr import compile_sql
from knot.spec import ReverseRef


def _movie_spec() -> tuple[Spec, object, object]:
    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT)
    credit.slot("person", person)
    credit.slot("movie", movie)
    src = spec.add_source("imdb")
    src.bind(person)
    src.bind(movie)
    src.bind(credit)
    return spec, person, credit


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_back_returns_reverse_ref():
    spec, person, credit = _movie_spec()
    ref = person.back(credit, "person")
    assert isinstance(ref, ReverseRef)
    assert ref.primary_cls is person
    assert ref.other_cls is credit
    assert ref.fk_slot_name == "person"
    assert ref.where_clause is None


def test_back_bad_slot_raises():
    spec, person, credit = _movie_spec()
    with pytest.raises(KeyError, match="no FK"):
        person.back(credit, "nonexistent")


def test_back_wrong_class_raises():
    spec, person, credit = _movie_spec()
    # credit.movie points at Movie, not Person
    with pytest.raises(KeyError):
        person.back(credit, "movie")


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_count_returns_count_rel():
    spec, person, credit = _movie_spec()
    expr = person.back(credit, "person").count()
    assert isinstance(expr, CountRel)
    assert expr.other_class_name == "Credit"
    assert expr.fk_slot_name == "person"
    assert expr.primary_class_name == "Person"
    assert expr.where is None


def test_any_returns_exists_not_negated():
    spec, person, credit = _movie_spec()
    expr = person.back(credit, "person").any()
    assert isinstance(expr, Exists)
    assert not expr.negated


def test_none_returns_exists_negated():
    spec, person, credit = _movie_spec()
    expr = person.back(credit, "person").none()
    assert isinstance(expr, Exists)
    assert expr.negated


def test_where_count_embeds_predicate():
    spec, person, credit = _movie_spec()
    predicate = credit.col.role == "director"
    expr = person.back(credit, "person").where(predicate).count()
    assert isinstance(expr, CountRel)
    assert expr.where is not None
    sql = compile_sql(expr, schema="kd", layer=Layer.RESOLVED)
    assert "director" in sql
    assert "COUNT(*)" in sql


def test_count_compiles_to_correlated_subquery():
    spec, person, credit = _movie_spec()
    expr = person.back(credit, "person").count()
    sql = compile_sql(expr, schema="kd", layer=Layer.RESOLVED)
    assert "SELECT COUNT(*)" in sql
    assert "credit_resolved" in sql
    assert "person_resolved" in sql
