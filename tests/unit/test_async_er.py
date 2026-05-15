"""Unit tests for the async ER helpers: assign_canonical + recanonicalize."""

import sqlglot

from knot import Spec, types
from knot.compile import emit_assign_canonical, emit_recanonicalize


def _basic_spec():
    spec = Spec(id="t", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    imdb = spec.add_source("imdb")
    imdb.bind(movie, base_trust=0.85)
    return spec, movie


def test_assign_canonical_emits_safe_update():
    """assign_canonical UPDATEs only NULL-id, open bindings — re-running
    is a no-op once the id is set."""
    spec, movie = _basic_spec()
    sql, params = emit_assign_canonical(
        movie,
        "m_pulpfiction",
        source_name="imdb",
        source_identifier="tt0110912",
    )
    assert "UPDATE knot_data.movie_bindings" in sql
    assert "SET canonical_id = %(canonical_id)s" in sql
    assert "AND canonical_id IS NULL" in sql  # safety: no clobber
    assert "AND valid_to IS NULL" in sql  # scope: open binding only
    assert params == {
        "canonical_id": "m_pulpfiction",
        "source_name": "imdb",
        "source_identifier": "tt0110912",
    }


def test_assign_canonical_schema_kwarg():
    spec, movie = _basic_spec()
    sql, _ = emit_assign_canonical(
        movie,
        "m_x",
        source_name="imdb",
        source_identifier="tt1",
        schema="alt",
    )
    assert "UPDATE alt.movie_bindings" in sql


def test_assign_canonical_parses_postgres():
    spec, movie = _basic_spec()
    sql, _ = emit_assign_canonical(
        movie,
        "m_x",
        source_name="imdb",
        source_identifier="tt1",
    )
    sqlglot.parse_one(sql, dialect="postgres")


def test_recanonicalize_emits_writable_cte():
    """recanonicalize closes the open binding and inserts a new one
    with the corrected canonical_id, all in one statement."""
    spec, movie = _basic_spec()
    sql, params = emit_recanonicalize(
        movie,
        "m_pulpfiction_v2",
        source_name="imdb",
        source_identifier="tt0110912",
    )
    # Writable CTE shape
    assert "WITH closed AS (" in sql
    assert "UPDATE knot_data.movie_bindings SET valid_to = now()" in sql
    assert "AND valid_to IS NULL" in sql  # only the currently-open row
    assert "RETURNING *" in sql
    # Insert side reuses the closed row's fields, substituting canonical_id
    assert "INSERT INTO knot_data.movie_bindings" in sql
    assert "%(new_canonical_id)s AS canonical_id" in sql
    assert "now() AS valid_from" in sql
    assert "FROM closed" in sql
    assert params["new_canonical_id"] == "m_pulpfiction_v2"
    assert params["source_name"] == "imdb"
    assert params["source_identifier"] == "tt0110912"


def test_recanonicalize_parses_postgres():
    spec, movie = _basic_spec()
    sql, _ = emit_recanonicalize(
        movie,
        "m_x",
        source_name="imdb",
        source_identifier="tt1",
    )
    sqlglot.parse_one(sql, dialect="postgres")
