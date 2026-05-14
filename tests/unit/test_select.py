"""Unit tests for the read substrate (query AST + compile_query)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from knot.compile.query_sql import compile_query
from knot.select import OrderBy, Query
from knot.spec import Primitive, Spec


def _make_movie_spec() -> tuple[Spec, "OntologyClass"]:  # noqa: F821
    spec = Spec(id="test", version="0.0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("title", Primitive.TEXT)
    movie.slot("year", Primitive.INTEGER)
    return spec, movie


def test_bare_class_is_select_star():
    spec, movie = _make_movie_spec()
    q = Query(class_name="Movie")
    sql, params = compile_query(q, spec=spec, schema="knot_data")
    assert sql == "SELECT *\nFROM knot_data.movie_resolved;"
    assert params == []


def test_simple_where():
    spec, movie = _make_movie_spec()
    q = movie.where(movie.col.year >= 1900)
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT *" in sql
    assert "FROM knot_data.movie_resolved" in sql
    assert "WHERE knot_data.movie_resolved.year >= 1900" in sql


def test_chain_where_ands():
    spec, movie = _make_movie_spec()
    q = movie.where(movie.col.year >= 1900).where(movie.col.year <= 2000)
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert "1900" in sql
    assert "2000" in sql
    assert "AND" in sql


def test_order_by_limit_offset():
    spec, movie = _make_movie_spec()
    q = movie.order_by(movie.col.year, "desc").limit(10).offset(5)
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert "ORDER BY knot_data.movie_resolved.year DESC" in sql
    assert "LIMIT 10" in sql
    assert "OFFSET 5" in sql


def test_multi_order():
    spec, movie = _make_movie_spec()
    q = movie.order_by(movie.col.year, "desc").order_by(movie.col.title)
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert (
        "ORDER BY knot_data.movie_resolved.year DESC, knot_data.movie_resolved.title ASC"
    ) in sql


def test_projection():
    spec, movie = _make_movie_spec()
    q = movie.select(movie.col.title, movie.col.year)
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT knot_data.movie_resolved.title, knot_data.movie_resolved.year" in sql
    assert "FROM knot_data.movie_resolved" in sql


def test_full_chain():
    spec, movie = _make_movie_spec()
    q = (
        movie.where(movie.col.year >= 1990)
        .where(movie.col.year <= 2000)
        .order_by(movie.col.year, "desc")
        .limit(50)
        .select(movie.col.title, movie.col.year)
    )
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert "SELECT knot_data.movie_resolved.title, knot_data.movie_resolved.year" in sql
    assert "FROM knot_data.movie_resolved" in sql
    assert "WHERE" in sql
    assert "AND" in sql
    assert "ORDER BY knot_data.movie_resolved.year DESC" in sql
    assert "LIMIT 50" in sql


def test_target_suffix_canonical():
    """Query can target the canonical table instead of _resolved."""
    spec, movie = _make_movie_spec()
    q = replace(movie.where(movie.col.year == 2020), target_suffix="")
    sql, _ = compile_query(q, spec=spec, schema="knot_data")
    assert "FROM knot_data.movie\n" in sql
    assert "knot_data.movie.year = 2020" in sql


def test_invalid_order_direction():
    with pytest.raises(ValueError, match="direction"):
        OrderBy(ref=None, direction="sideways")  # type: ignore[arg-type]


def test_fluent_immutability():
    """Each builder call returns a new Query — the original is untouched."""
    spec, movie = _make_movie_spec()
    base = movie.where(movie.col.year == 2020)
    with_limit = base.limit(10)
    assert base.limit_value is None
    assert with_limit.limit_value == 10
