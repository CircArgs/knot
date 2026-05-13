"""knot.compile.data_io — SCD2 binding write emission."""

import pytest
import sqlglot

from knot import Primitive, Spec
from knot.compile import (
    BindingWrite,
    MappedBindingWrite,
    emit_binding_write,
    emit_mapped_binding_write,
)


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


# ---------------------------------------------------------------------------
# emit_mapped_binding_write — INSERT...SELECT applying mappings server-side
# ---------------------------------------------------------------------------


def test_mapped_returns_dataclass(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    assert isinstance(mw, MappedBindingWrite)


def test_mapped_close_out_columns_omit_source_name(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    # source_name is baked in as a literal — host doesn't pass it.
    assert mw.close_out_columns == ("canonical_id", "source_identifier")
    assert "source_name = 'imdb'" in mw.close_out_sql


def test_mapped_input_columns_include_raw_fields_from_mappings(movie_spec):
    b = movie_spec.source_bindings[0]
    # mappings are year=release_year, runtime_minutes=(regexp_match(runtime, ...))
    mw = emit_mapped_binding_write(movie_spec, b)
    cols = set(mw.insert_input_columns)
    assert "release_year" in cols
    assert "runtime" in cols
    assert "canonical_id" in cols
    assert "source_identifier" in cols


def test_mapped_input_columns_drop_unmapped_slots(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    # genres + name have no mapping — host shouldn't be asked for them.
    cols = set(mw.insert_input_columns)
    assert "genres" not in cols
    assert "name" not in cols


def test_mapped_insert_sql_uses_named_placeholders(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    for col in mw.insert_input_columns:
        assert f"%({col})s" in mw.insert_sql


def test_mapped_insert_sql_inlines_mapping_expressions(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    assert "release_year" in mw.insert_sql
    assert "regexp_match(runtime" in mw.insert_sql


def test_mapped_unmapped_slots_default_null(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    # name + genres aren't mapped — should land as NULL in the projection
    assert "NULL" in mw.insert_sql


def test_mapped_both_sql_parse_postgres(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b)
    sqlglot.parse_one(mw.close_out_sql, dialect="postgres")
    sqlglot.parse_one(mw.insert_sql, dialect="postgres")


def test_mapped_source_name_escapes_apostrophe():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    src = spec.add_source("o_brien")  # name pattern doesn't allow apostrophe;
    # but test the escape path with a name we manually mutate
    b = spec.bind(src, movie, identifier=movie["canonical_id"])
    src.name = "o'brien"  # simulate an unescaped apostrophe sneaking in
    mw = emit_mapped_binding_write(spec, b)
    assert "'o''brien'" in mw.insert_sql


def test_mapped_kwargs_threading(movie_spec):
    b = movie_spec.source_bindings[0]
    mw = emit_mapped_binding_write(movie_spec, b, schema="alt", bindings_suffix="__s")
    assert "alt.movie__s" in mw.insert_sql
    assert "alt.movie__s" in mw.close_out_sql


def test_mapped_abstract_class_rejected():
    spec = Spec(id="m", version="0.1")
    abstract = spec.add_class("A", kind="abstract")
    abstract.slot("canonical_id", Primitive.TEXT, identifier=True)
    src = spec.add_source("s")
    from knot import SourceBinding
    b = SourceBinding(source=src, class_=abstract, identifier_slot=abstract["canonical_id"])
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_mapped_binding_write(spec, b)
