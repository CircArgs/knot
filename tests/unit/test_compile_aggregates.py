"""Aggregate projections + Query.group_by.

count() / sum_() / avg() / min_() / max_() are _ValueExpr-typed
nodes that compose into Query.select() (and Query.order_by() /
Query.where() for comparison). Without Query.group_by, the aggregate
reduces to one scalar row; with group_by, one row per group.
"""

from __future__ import annotations

import sqlglot

from knot import Spec, types
from knot.ast.expr import AggExpr, avg, count, max_, min_, sum_


def _make_movie_spec():
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime", types.INTEGER)
    spec.add_source("imdb").bind(movie)
    return spec, movie


# ---------------------------------------------------------------------------
# AggExpr construction
# ---------------------------------------------------------------------------


def test_count_no_arg_is_count_star():
    expr = count()
    assert isinstance(expr, AggExpr)
    assert expr.kind == "count"
    assert expr.expr is None


def test_count_with_arg_is_typed():
    spec, movie = _make_movie_spec()
    expr = count(movie.col.year)
    assert expr.kind == "count"
    assert expr.expr is not None
    # Refs are equality-by-operator (Compare), so check attributes.
    assert expr.expr.class_name == "Movie"
    assert expr.expr.slot_name == "year"


def test_non_count_aggregates_require_arg():
    import pytest

    spec, movie = _make_movie_spec()
    for factory in (sum_, avg, min_, max_):
        expr = factory(movie.col.year)
        assert expr.expr is not None
        assert expr.expr.slot_name == "year"

    with pytest.raises(ValueError, match="requires an inner Expr"):
        AggExpr(kind="sum", expr=None)


def test_invalid_kind_rejected():
    import pytest

    with pytest.raises(ValueError, match="kind must be one of"):
        AggExpr(kind="median", expr=None)


# ---------------------------------------------------------------------------
# SQL rendering — scalar (no group_by)
# ---------------------------------------------------------------------------


def test_scalar_count_star_renders_correctly():
    spec, movie = _make_movie_spec()
    q = movie.resolved.select(count())
    sql = q.sql()
    assert "SELECT COUNT(*)" in sql
    assert "FROM knot_data.movie_resolved" in sql
    assert "GROUP BY" not in sql


def test_scalar_mixed_aggregates_in_select():
    spec, movie = _make_movie_spec()
    q = movie.resolved.select(count(), avg(movie.col.year), sum_(movie.col.runtime))
    sql = q.sql()
    assert "COUNT(*)" in sql
    assert "AVG(knot_data.movie_resolved.year)" in sql
    assert "SUM(knot_data.movie_resolved.runtime)" in sql


def test_count_of_column_is_count_col_not_count_star():
    spec, movie = _make_movie_spec()
    q = movie.resolved.select(count(movie.col.year))
    sql = q.sql()
    assert "COUNT(knot_data.movie_resolved.year)" in sql
    assert "COUNT(*)" not in sql


# ---------------------------------------------------------------------------
# SQL rendering — grouped
# ---------------------------------------------------------------------------


def test_group_by_renders_between_where_and_order_by():
    spec, movie = _make_movie_spec()
    q = (
        movie.resolved
        .where(movie.col.year >= 1900)
        .group_by(movie.col.year)
        .select(movie.col.year, count())
        .order_by(movie.col.year, "desc")
    )
    sql = q.sql()
    # Clause order: SELECT, FROM, WHERE, GROUP BY, ORDER BY.
    parts = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY"]
    positions = [sql.find(p) for p in parts]
    assert positions == sorted(positions), f"clauses out of order in:\n{sql}"
    assert "GROUP BY knot_data.movie_resolved.year" in sql


def test_group_by_appends_not_replaces():
    spec, movie = _make_movie_spec()
    q = (
        movie.resolved
        .group_by(movie.col.year)
        .group_by(movie.col.title)  # appends
        .select(count())
    )
    sql = q.sql()
    assert (
        "GROUP BY knot_data.movie_resolved.year, knot_data.movie_resolved.title"
        in sql
    )


def test_min_max_render():
    spec, movie = _make_movie_spec()
    q = movie.resolved.select(min_(movie.col.year), max_(movie.col.year))
    sql = q.sql()
    assert "MIN(knot_data.movie_resolved.year)" in sql
    assert "MAX(knot_data.movie_resolved.year)" in sql


# ---------------------------------------------------------------------------
# Aggregate as comparison (composes via _ValueExpr)
# ---------------------------------------------------------------------------


def test_aggregate_composes_with_comparison_in_order_by():
    """count() returns a value-expr so order_by(count(), 'desc') works."""
    spec, movie = _make_movie_spec()
    q = (
        movie.resolved
        .group_by(movie.col.year)
        .select(movie.col.year, count())
        .order_by(count(), "desc")
        .limit(10)
    )
    sql = q.sql()
    assert "ORDER BY COUNT(*) DESC" in sql


# ---------------------------------------------------------------------------
# Parses postgres
# ---------------------------------------------------------------------------


def test_emitted_aggregate_sql_parses_postgres():
    spec, movie = _make_movie_spec()
    queries = [
        movie.resolved.select(count()),
        movie.resolved.select(count(movie.col.year), avg(movie.col.year)),
        (
            movie.resolved
            .where(movie.col.year >= 1900)
            .group_by(movie.col.year)
            .select(movie.col.year, count(), avg(movie.col.runtime))
            .order_by(movie.col.year, "desc")
            .limit(5)
        ),
    ]
    for q in queries:
        sqlglot.parse_one(q.sql(), dialect="postgres")
