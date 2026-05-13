"""knot.compile.data_io — SCD2 binding write emission."""

import pytest
import sqlglot

from knot.compile import BindingWrite, emit_binding_write


def test_returns_binding_write_dataclass(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    assert isinstance(bw, BindingWrite)


def test_close_out_columns_are_identifier_and_source_pair(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    assert bw.close_out_columns == (
        "canonical_id",
        "source_name",
        "source_identifier",
    )


def test_close_out_sql_targets_bindings_table(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    assert "UPDATE knot_data.movie_bindings" in bw.close_out_sql
    assert "SET valid_to = now()" in bw.close_out_sql
    assert "valid_to IS NULL" in bw.close_out_sql


def test_insert_columns_include_inherited_and_own(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    cols = set(bw.insert_columns)
    # Source metadata always present
    assert {"source_name", "source_identifier"} <= cols
    # Inherited from Title
    assert {"canonical_id", "name"} <= cols
    # Own to Movie
    assert {"year", "runtime_minutes", "genres"} <= cols


def test_insert_sql_uses_named_placeholders(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    for col in bw.insert_columns:
        assert f"%({col})s" in bw.insert_sql


def test_close_out_sql_uses_named_placeholders(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    for col in bw.close_out_columns:
        assert f"%({col})s" in bw.close_out_sql


def test_both_statements_parse_postgres(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie")
    sqlglot.parse_one(bw.close_out_sql, dialect="postgres")
    sqlglot.parse_one(bw.insert_sql, dialect="postgres")


def test_schema_and_suffix_kwargs(movie_spec):
    bw = emit_binding_write(movie_spec, "Movie", schema="alt", bindings_suffix="__s")
    assert "alt.movie__s" in bw.insert_sql
    assert "alt.movie__s" in bw.close_out_sql


def test_abstract_class_rejected(movie_spec):
    with pytest.raises(ValueError, match="abstract"):
        emit_binding_write(movie_spec, "Title")


def test_unknown_class_rejected(movie_spec):
    with pytest.raises(ValueError, match="no concrete class"):
        emit_binding_write(movie_spec, "Ghost")


def test_virtual_class_rejected(movie_spec):
    with pytest.raises(ValueError, match="no concrete class"):
        emit_binding_write(movie_spec, "DirectedMovie")
