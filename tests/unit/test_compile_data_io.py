"""knot.compile.write — per-binding upsert SQL emission."""

import pytest
import sqlglot

from knot import Severity, SourceBinding, Spec, types
from knot.compile import emit_binding_write_sql

# ---------------------------------------------------------------------------
# Public shape
# ---------------------------------------------------------------------------


def test_returns_one_sql_string(movie_spec):
    b = movie_spec.source_bindings[0]
    out = emit_binding_write_sql(b)
    assert isinstance(out, str)
    assert "INSERT INTO" in out
    assert "ON CONFLICT" in out


def test_binding_write_sql_method_returns_same(movie_spec):
    b = movie_spec.source_bindings[0]
    assert b.write_sql() == emit_binding_write_sql(b)


# ---------------------------------------------------------------------------
# Upsert shape — single statement, ON CONFLICT DO UPDATE
# ---------------------------------------------------------------------------


def test_upsert_targets_bindings_table_and_bakes_source(movie_spec):
    movie = movie_spec.classes["Movie"]
    b = movie.binding_for(movie_spec.sources["imdb"])
    sql = emit_binding_write_sql(b)
    assert "INSERT INTO knot_data.movie_bindings" in sql
    assert "'imdb'" in sql  # baked-in source literal in the SELECT
    assert "jsonb_array_elements(%(rows)s::jsonb)" in sql


def test_upsert_uses_pk_conflict_target(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b)
    assert "ON CONFLICT (source_name, source_identifier) DO UPDATE SET" in sql


def test_upsert_preserves_canonical_id_and_er_metadata(movie_spec):
    """ON CONFLICT DO UPDATE updates every slot the source provides,
    but canonical_id and er_metadata are ER-owned and never overwritten
    by a re-ingest."""
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b)
    # Split on ON CONFLICT to inspect just the SET clause.
    set_block = sql.split("ON CONFLICT", 1)[1]
    assert "canonical_id = EXCLUDED" not in set_block
    assert "er_metadata = EXCLUDED" not in set_block


def test_upsert_overwrites_non_identity_slots(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b)
    set_block = sql.split("ON CONFLICT", 1)[1]
    # Sample of non-identity slots from the movie_spec fixture.
    assert "year = EXCLUDED.year" in set_block
    assert "name = EXCLUDED.name" in set_block
    assert "raw_payload = EXCLUDED.raw_payload" in set_block


# ---------------------------------------------------------------------------
# Insert SELECT shape
# ---------------------------------------------------------------------------


def test_insert_includes_raw_payload_column_and_projection(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b)
    assert "raw_payload)" in sql
    assert "r AS __raw_payload" in sql
    assert "raw.__raw_payload" in sql


def test_insert_uses_jsonb_extraction_for_unmapped_passthrough(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b)
    # `name` is unmapped — implicit passthrough at same name.
    assert "raw.name::text" in sql
    # Arrays go through unnest+ARRAY round-trip via __raw_payload.
    assert "jsonb_array_elements(raw.__raw_payload->'genres')" in sql


# ---------------------------------------------------------------------------
# Row count independence
# ---------------------------------------------------------------------------


def test_sql_shape_independent_of_row_count(movie_spec):
    """Whether the host binds 1 row or 10000, the SQL is identical —
    only the jsonb param payload grows."""
    b = movie_spec.source_bindings[0]
    a = emit_binding_write_sql(b)
    b2 = emit_binding_write_sql(b)
    assert a == b2  # deterministic, no dependence on call site or rows


# ---------------------------------------------------------------------------
# Schema / suffix kwargs
# ---------------------------------------------------------------------------


def test_schema_and_suffix_kwargs(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b, schema="alt", bindings_suffix="__s")
    assert "alt.movie__s" in sql
    assert "knot_data.movie_bindings" not in sql


# ---------------------------------------------------------------------------
# Source name embedding
# ---------------------------------------------------------------------------


def test_source_name_apostrophe_escaped():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate an apostrophe
    b = src.bind(movie)
    sql = emit_binding_write_sql(b)
    assert "'o''brien'" in sql


# ---------------------------------------------------------------------------
# Abstract class rejection
# ---------------------------------------------------------------------------


def test_abstract_class_rejected():
    spec = Spec(identifier_slot_name="canonical_id")
    abstract = spec.add_class("A", kind="abstract")
    src = spec.add_source("s")
    b = SourceBinding(source=src, class_=abstract)
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_binding_write_sql(b)


# ---------------------------------------------------------------------------
# SQL parses
# ---------------------------------------------------------------------------


def test_emitted_sql_parses_postgres(movie_spec):
    b = movie_spec.source_bindings[0]
    sqlglot.parse_one(emit_binding_write_sql(b), dialect="postgres")


# ---------------------------------------------------------------------------
# Constraint enforcement is host-side. Write SQL emits no DO blocks
# regardless of constraint declarations on the spec.
# ---------------------------------------------------------------------------


def test_write_emits_no_do_block_regardless_of_constraints(movie_spec):
    """movie_spec carries the year_sane error-severity constraint;
    write_sql doesn't bundle it. Host runs ``spec.emit_validation()``
    separately after the write."""
    b = movie_spec.source_bindings[0]
    assert "DO $$" not in emit_binding_write_sql(b)


def test_severity_warning_does_not_affect_write_sql():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.add_constraint(
        "warn_only",
        body=movie.col.year > 1900,
        severity=Severity.WARNING,
    )
    src = spec.add_source("imdb")
    b = src.bind(movie)
    assert "warn_only" not in emit_binding_write_sql(b)


# ---------------------------------------------------------------------------
# returning= kwarg on emit_binding_write_sql / binding.write_sql
# ---------------------------------------------------------------------------


def test_returning_none_unchanged(movie_spec):
    b = movie_spec.source_bindings[0]
    default_sql = emit_binding_write_sql(b)
    explicit_none_sql = emit_binding_write_sql(b, returning=None)
    assert default_sql == explicit_none_sql
    assert "RETURNING" not in default_sql


def test_returning_star_appends_returning_star(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b, returning="*")
    assert "RETURNING *" in sql
    assert sql.rstrip().endswith(";")
    sqlglot.parse_one(sql, dialect="postgres")


def test_returning_slot_list_appends_named_columns(movie_spec):
    b = movie_spec.source_bindings[0]
    sql = emit_binding_write_sql(b, returning=["year", "name"])
    assert "RETURNING year, name" in sql
    assert sql.rstrip().endswith(";")
    sqlglot.parse_one(sql, dialect="postgres")


def test_returning_bad_slot_raises_key_error(movie_spec):
    b = movie_spec.source_bindings[0]
    with pytest.raises(KeyError, match="bogus_slot"):
        emit_binding_write_sql(b, returning=["bogus_slot"])


def test_write_sql_facade_passes_returning(movie_spec):
    b = movie_spec.source_bindings[0]
    assert b.write_sql(returning="*") == emit_binding_write_sql(b, returning="*")
