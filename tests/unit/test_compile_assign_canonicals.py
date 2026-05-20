"""knot.compile.write — emit_assign_canonicals_sql (batched ER mint)."""

import sqlglot

from knot import Spec, types
from knot.compile.write import emit_assign_canonicals_sql

# ---------------------------------------------------------------------------
# Shared fixture — two-class spec with a ClassRef FK (Credit → Movie)
# so forward + backward fan-out paths are both exercised.
# ---------------------------------------------------------------------------


def _make_spec() -> tuple:
    """Return (spec, imdb_movie_binding, imdb_credit_binding)."""
    spec = Spec(identifier_slot_name="canonical_id")

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)

    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)  # ClassRef FK → Movie

    imdb = spec.add_source("imdb")
    imdb_movie = imdb.bind(movie)
    imdb_credit = imdb.bind(credit)
    return spec, imdb_movie, imdb_credit


# ---------------------------------------------------------------------------
# 1. SQL parses as postgres (sqlglot)
# ---------------------------------------------------------------------------


def test_sql_parses_postgres():
    _, b, _ = _make_spec()
    sqlglot.parse_one(emit_assign_canonicals_sql(b), dialect="postgres")


# ---------------------------------------------------------------------------
# 2. Single %(assignments)s::jsonb parameter
# ---------------------------------------------------------------------------


def test_sql_contains_assignments_placeholder():
    _, b, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b)
    assert "%(assignments)s::jsonb" in sql


# ---------------------------------------------------------------------------
# 3. jsonb_to_recordset drives the stamp
# ---------------------------------------------------------------------------


def test_sql_contains_jsonb_to_recordset():
    _, b, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b)
    assert "jsonb_to_recordset" in sql
    assert "AS a(canonical_id text, source_identifier text, er_metadata jsonb)" in sql


# ---------------------------------------------------------------------------
# 4. Stamp CTE filters canonical_id IS NULL (idempotency)
# ---------------------------------------------------------------------------


def test_stamp_filters_canonical_id_is_null():
    _, b, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b)
    # The ident slot name is canonical_id; IS NULL guard enforces idempotency.
    assert "canonical_id IS NULL" in sql


# ---------------------------------------------------------------------------
# 5. Forward FK translation appears for each ClassRef slot
# ---------------------------------------------------------------------------


def test_forward_fk_translation_present_for_classref_slot():
    _, _, b_credit = _make_spec()
    sql = emit_assign_canonicals_sql(b_credit)
    # The Credit class has a ClassRef slot 'movie' → Movie; the stamp SET
    # clause must include a COALESCE lookup into movie_bindings.
    assert "movie_bindings" in sql
    assert "COALESCE" in sql


def test_no_forward_fk_when_no_classref_slots():
    _, b_movie, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b_movie)
    # Movie has no ClassRef slots — the stamp SET block should not contain a
    # per-slot subquery lookup (``SELECT canonical_id FROM ... LIMIT 1``).
    # (credit_bindings DOES appear, but only in the backward fanout CTE.)
    assert "LIMIT 1" not in sql


# ---------------------------------------------------------------------------
# 6. Backward fan-out CTE for each referrer
# ---------------------------------------------------------------------------


def test_backward_fanout_cte_present_for_referrer():
    _, b_movie, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b_movie)
    # Credit.movie → Movie, so stamping Movie must fan-out into credit_bindings.
    assert "fanout_credit_movie" in sql
    assert "credit_bindings" in sql


def test_no_fanout_when_no_referrers():
    _, _, b_credit = _make_spec()
    sql = emit_assign_canonicals_sql(b_credit)
    # Credit is not referenced by any FK slot — no fan-out CTEs.
    assert "fanout_" not in sql


# ---------------------------------------------------------------------------
# 7. Per-source filter (source_name = '<source>') in stamp + fan-out
# ---------------------------------------------------------------------------


def test_source_name_literal_in_stamp_and_fanout():
    _, b_movie, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b_movie)
    assert "'imdb'" in sql
    # source_name filter appears at least twice: once in stamp WHERE,
    # once in each fanout WHERE.
    assert sql.count("source_name = 'imdb'") >= 2


# ---------------------------------------------------------------------------
# 8. Facade: binding.assign_canonicals_sql() matches free-function output
# ---------------------------------------------------------------------------


def test_facade_matches_free_function():
    _, b_movie, _ = _make_spec()
    assert b_movie.assign_canonicals_sql() == emit_assign_canonicals_sql(
        b_movie, schema="knot_data"
    )


# ---------------------------------------------------------------------------
# 9. Schema / suffix kwargs forwarded correctly
# ---------------------------------------------------------------------------


def test_schema_and_suffix_kwargs():
    _, b_movie, _ = _make_spec()
    sql = emit_assign_canonicals_sql(b_movie, schema="alt", bindings_suffix="__s")
    assert "alt.movie__s" in sql
    assert "knot_data.movie_bindings" not in sql
