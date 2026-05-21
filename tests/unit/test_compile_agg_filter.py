"""AggExpr.filter(predicate) — FILTER (WHERE ...) conditional aggregation."""

from __future__ import annotations

import sqlglot

from knot import Spec, types
from knot.ast.expr import AggExpr, count

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)

    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)

    imdb = spec.add_source("imdb")
    imdb.bind(movie)
    imdb.bind(credit)
    return spec, movie, credit


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_filter_returns_new_agg_expr():
    spec, movie, credit = _make_spec()
    base = count(credit.col.role)
    filtered = base.filter(credit.col.role == "director")
    assert isinstance(filtered, AggExpr)
    assert filtered is not base
    assert filtered.filter_predicate is not None


def test_filter_preserves_kind_and_expr():
    spec, movie, credit = _make_spec()
    base = count(credit.col.role)
    filtered = base.filter(credit.col.role == "director")
    assert filtered.kind == "count"
    assert filtered.expr is base.expr


def test_count_star_filter():
    """count() (no arg) with filter still works — COUNT(*) FILTER (WHERE ...)."""
    spec, movie, credit = _make_spec()
    filtered = count().filter(credit.col.role == "director")
    assert filtered.expr is None
    assert filtered.filter_predicate is not None


# ---------------------------------------------------------------------------
# SQL rendering
# ---------------------------------------------------------------------------


def test_count_col_filter_renders_filter_clause():
    spec, movie, credit = _make_spec()
    q = credit.resolved.select(
        count(credit.col.role).filter(credit.col.role == "director")
    )
    sql = q.sql()
    assert "COUNT(knot_data.credit_resolved.role)" in sql
    assert "FILTER (WHERE" in sql
    assert "role = 'director'" in sql


def test_count_star_filter_renders_filter_clause():
    spec, movie, credit = _make_spec()
    q = credit.resolved.select(count().filter(credit.col.role == "director"))
    sql = q.sql()
    assert "COUNT(*)" in sql
    assert "FILTER (WHERE" in sql
    assert "role = 'director'" in sql


def test_unfiltered_count_no_filter_clause():
    spec, movie, credit = _make_spec()
    q = credit.resolved.select(count(credit.col.role))
    sql = q.sql()
    assert "FILTER" not in sql


def test_filter_sql_parses_postgres():
    spec, movie, credit = _make_spec()
    queries = [
        credit.resolved.select(count().filter(credit.col.role == "director")),
        credit.resolved.select(
            count(credit.col.role).filter(credit.col.role == "actor")
        ),
        (
            credit.resolved.group_by(credit.col.movie).select(
                credit.col.movie,
                count().filter(credit.col.role == "director"),
                count(),
            )
        ),
    ]
    for q in queries:
        sqlglot.parse_one(q.sql(), dialect="postgres")


def test_filter_composes_with_group_by():
    spec, movie, credit = _make_spec()
    q = credit.resolved.group_by(credit.col.movie).select(
        credit.col.movie,
        count().filter(credit.col.role == "director"),
        count(),
    )
    sql = q.sql()
    assert "GROUP BY" in sql
    assert "FILTER (WHERE" in sql
    assert "COUNT(*)" in sql
