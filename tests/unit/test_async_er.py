"""Unit tests for the ER helpers: assign_canonical_sql + recanonicalize_sql."""

import sqlglot

from knot import Spec, types
from knot.compile import emit_assign_canonical_sql, emit_recanonicalize_sql


def _basic_spec():
    spec = Spec(id="t", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    imdb = spec.add_source("imdb")
    binding = imdb.bind(movie).set_default_weight(0.85)
    return spec, binding


# ---------------------------------------------------------------------------
# assign_canonical_sql
# ---------------------------------------------------------------------------


def test_assign_canonical_emits_safe_update():
    """assign_canonical_sql UPDATEs only NULL-id, open bindings — re-running
    is a no-op once the id is set."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "UPDATE knot_data.movie_bindings" in sql
    assert "SET canonical_id = %(canonical_id)s" in sql
    assert "AND canonical_id IS NULL" in sql  # safety: no clobber
    assert "AND valid_to IS NULL" in sql  # scope: open binding only


def test_assign_canonical_bakes_in_source_name():
    """source is pinned by the binding — baked in as a SQL literal,
    not bound at runtime."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "source_name = 'imdb'" in sql
    assert "%(source_name)s" not in sql


def test_assign_canonical_runtime_placeholders():
    """Three named placeholders: canonical_id, source_identifier,
    er_metadata. Host binds them via cur.execute params."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "%(canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    assert "%(er_metadata)s" in sql


def test_assign_canonical_er_metadata_uses_coalesce():
    """er_metadata uses COALESCE so binding ``None`` keeps the
    existing column value; binding a JSON string overwrites."""
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding)
    assert "er_metadata = COALESCE(%(er_metadata)s::jsonb, er_metadata)" in sql


def test_assign_canonical_schema_kwarg():
    _, binding = _basic_spec()
    sql = emit_assign_canonical_sql(binding, schema="alt")
    assert "UPDATE alt.movie_bindings" in sql


def test_assign_canonical_parses_postgres():
    _, binding = _basic_spec()
    sqlglot.parse_one(emit_assign_canonical_sql(binding), dialect="postgres")


# ---------------------------------------------------------------------------
# recanonicalize_sql
# ---------------------------------------------------------------------------


def test_recanonicalize_emits_writable_cte():
    """recanonicalize_sql closes the open binding and inserts a new
    one with the corrected canonical_id, all in one statement."""
    _, binding = _basic_spec()
    sql = emit_recanonicalize_sql(binding)
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


def test_recanonicalize_runtime_placeholders():
    _, binding = _basic_spec()
    sql = emit_recanonicalize_sql(binding)
    assert "%(new_canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    assert "%(er_metadata)s" in sql
    # source_name is baked in
    assert "source_name = 'imdb'" in sql
    assert "%(source_name)s" not in sql


def test_recanonicalize_er_metadata_uses_coalesce():
    """recanonicalize_sql uses COALESCE so binding ``None`` inherits
    the closed row's er_metadata; a JSON string overrides."""
    _, binding = _basic_spec()
    sql = emit_recanonicalize_sql(binding)
    assert "COALESCE(%(er_metadata)s::jsonb, er_metadata) AS er_metadata" in sql


def test_recanonicalize_parses_postgres():
    _, binding = _basic_spec()
    sqlglot.parse_one(emit_recanonicalize_sql(binding), dialect="postgres")
