"""knot.compile.write — per-binding SCD2 write SQL emission."""

import pytest
import sqlglot

from knot import Severity, SourceBinding, Spec, types
from knot.compile import emit_binding_write_sql

# ---------------------------------------------------------------------------
# Public shape
# ---------------------------------------------------------------------------


def test_returns_two_sql_strings(movie_spec):
    b = movie_spec.source_bindings[0]
    out = emit_binding_write_sql(b)
    assert isinstance(out, tuple)
    assert len(out) == 2
    close_out, insert = out
    assert isinstance(close_out, str)
    assert isinstance(insert, str)


def test_binding_write_sql_method_returns_same(movie_spec):
    b = movie_spec.source_bindings[0]
    assert b.write_sql() == emit_binding_write_sql(b)


# ---------------------------------------------------------------------------
# close-out shape
# ---------------------------------------------------------------------------


def test_close_out_targets_bindings_table_with_jsonb_keys(movie_spec):
    b = movie_spec.source_bindings[0]
    close_out, _ = emit_binding_write_sql(b)
    assert "UPDATE knot_data.movie_bindings" in close_out
    assert "SET valid_to = now()" in close_out
    assert "source_name = 'imdb'" in close_out  # baked-in literal
    assert "jsonb_array_elements(%(rows)s::jsonb)" in close_out
    assert "AND b.valid_to IS NULL" in close_out


# ---------------------------------------------------------------------------
# insert shape
# ---------------------------------------------------------------------------


def test_insert_includes_mapped_field_projection(movie_spec):
    b = movie_spec.source_bindings[0]
    _, insert = emit_binding_write_sql(b)
    assert "INSERT INTO knot_data.movie_bindings" in insert
    assert "source_name = 'imdb'" not in insert  # written as SELECT literal
    assert "'imdb'" in insert
    assert "jsonb_array_elements(%(rows)s::jsonb)" in insert


def test_insert_includes_raw_payload_column_and_projection(movie_spec):
    b = movie_spec.source_bindings[0]
    _, insert = emit_binding_write_sql(b)
    # raw_payload is the last column in the INSERT, sourced from r passthrough.
    assert "raw_payload)" in insert
    assert "r AS __raw_payload" in insert
    assert "raw.__raw_payload" in insert


def test_insert_uses_jsonb_extraction_for_unmapped_passthrough(movie_spec):
    b = movie_spec.source_bindings[0]
    _, insert = emit_binding_write_sql(b)
    # `name` is unmapped — implicit passthrough at same name
    assert "raw.name::text" in insert
    # Arrays go through unnest+ARRAY round-trip via __raw_payload.
    assert "jsonb_array_elements(raw.__raw_payload->'genres')" in insert


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
    close_out, insert = emit_binding_write_sql(b, schema="alt", bindings_suffix="__s")
    assert "alt.movie__s" in close_out
    assert "alt.movie__s" in insert
    assert "knot_data.movie_bindings" not in close_out
    assert "knot_data.movie_bindings" not in insert


# ---------------------------------------------------------------------------
# Source name embedding
# ---------------------------------------------------------------------------


def test_source_name_apostrophe_escaped():
    spec = Spec(id="m", version="0.1", identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate an apostrophe
    b = src.bind(movie)
    close_out, insert = emit_binding_write_sql(b)
    assert "'o''brien'" in close_out
    assert "'o''brien'" in insert


# ---------------------------------------------------------------------------
# Abstract class rejection
# ---------------------------------------------------------------------------


def test_abstract_class_rejected():
    spec = Spec(id="m", version="0.1", identifier_slot_name="canonical_id")
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
    close_out, insert = emit_binding_write_sql(b)
    sqlglot.parse_one(close_out, dialect="postgres")
    sqlglot.parse_one(insert, dialect="postgres")


# ---------------------------------------------------------------------------
# Constraint enforcement is no longer baked into the write — host
# concern. These tests cover what's left: the binding doesn't emit
# any DO block, and Severity warnings have no impact on write SQL.
# ---------------------------------------------------------------------------


def test_write_emits_no_do_block_regardless_of_constraints(movie_spec):
    """movie_spec carries the year_sane error-severity constraint;
    write_sql doesn't bundle it. Host runs ``spec.emit_validation()``
    separately after the write."""
    b = movie_spec.source_bindings[0]
    close_out, insert = emit_binding_write_sql(b)
    assert "DO $$" not in close_out
    assert "DO $$" not in insert


def test_severity_warning_does_not_affect_write_sql():
    spec = Spec(id="m", version="0.1", identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.add_constraint(
        "warn_only",
        body=movie.col.year > 1900,
        severity=Severity.WARNING,
    )
    src = spec.add_source("imdb")
    b = src.bind(movie)
    close_out, insert = emit_binding_write_sql(b)
    assert "warn_only" not in close_out
    assert "warn_only" not in insert
