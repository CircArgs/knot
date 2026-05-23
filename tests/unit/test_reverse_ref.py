"""OntologyClass.via() — FK-correlated-aggregate navigator.

``via`` takes a typed ``FkRef`` (cls.col.<slot>) and returns a
``ReverseRef`` that materializes into a correlated subquery with
``.count()`` / ``.any()`` / ``.none()``. Replaces the older ``.back()``
method — the typed FkRef arg moves slot-existence validation to the
``.col`` access (one step earlier than the navigator call itself).
"""

import pytest

from knot import Spec, types
from knot.ast.expr import CountRel, Exists, FkRef
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


def test_via_returns_reverse_ref():
    spec, person, credit = _movie_spec()
    fk = credit.col.person
    assert isinstance(fk, FkRef)
    ref = person.via(fk)
    assert isinstance(ref, ReverseRef)
    assert ref.primary_cls is person
    assert ref.other_cls is credit
    assert ref.fk_slot_name == "person"
    assert ref.where_clause is None


def test_via_bad_slot_caught_at_col_access():
    """Typo in the slot name raises at ``credit.col.nonexistent``,
    one step BEFORE the via() call — the typed FkRef arg pushes
    validation to the access site."""
    spec, person, credit = _movie_spec()
    with pytest.raises(KeyError):
        credit.col.nonexistent  # noqa: B018 — intentional access


def test_via_non_fk_arg_raises_typeerror():
    """Passing a Ref that isn't an FkRef (e.g. a plain text slot)
    must raise — via() is explicitly for FK navigation."""
    spec, person, credit = _movie_spec()
    with pytest.raises(TypeError, match="takes an FkRef"):
        person.via(credit.col.role)  # role is TEXT, not an FK


def test_via_wrong_target_class_raises():
    """credit.col.movie points at Movie, not Person — via() rejects
    because the FK target doesn't match the receiving class."""
    spec, person, credit = _movie_spec()
    with pytest.raises(TypeError, match="points at"):
        person.via(credit.col.movie)


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def test_count_returns_count_rel():
    spec, person, credit = _movie_spec()
    expr = person.via(credit.col.person).count()
    assert isinstance(expr, CountRel)
    assert expr.other_class_name == "Credit"
    assert expr.fk_slot_name == "person"
    assert expr.primary_class_name == "Person"
    assert expr.where is None


def test_any_returns_exists_not_negated():
    spec, person, credit = _movie_spec()
    expr = person.via(credit.col.person).any()
    assert isinstance(expr, Exists)
    assert not expr.negated


def test_none_returns_exists_negated():
    spec, person, credit = _movie_spec()
    expr = person.via(credit.col.person).none()
    assert isinstance(expr, Exists)
    assert expr.negated


def test_where_count_embeds_predicate():
    spec, person, credit = _movie_spec()
    predicate = credit.col.role == "director"
    expr = person.via(credit.col.person).where(predicate).count()
    assert isinstance(expr, CountRel)
    assert expr.where is not None
    sql = compile_sql(expr, schema="kd", layer=Layer.RESOLVED)
    assert "director" in sql
    assert "COUNT(*)" in sql


def test_count_compiles_to_correlated_subquery():
    spec, person, credit = _movie_spec()
    expr = person.via(credit.col.person).count()
    sql = compile_sql(expr, schema="kd", layer=Layer.RESOLVED)
    assert "SELECT COUNT(*)" in sql
    assert "credit_resolved" in sql
    assert "person_resolved" in sql
