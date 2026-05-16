"""knot — _user_corrections synthetic source + close_out_sql."""

import pytest
import sqlglot

from knot import CORRECTIONS_SOURCE_NAME, SourceBinding, Spec, types
from knot.compile import (
    emit_close_out_sql,
    emit_weight_seed,
)

# ---------------------------------------------------------------------------
# Spec.enable_corrections
# ---------------------------------------------------------------------------


def test_enable_corrections_registers_source_and_per_class_bindings():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    person = spec.add_class("Person")
    person.slot("canonical_id", types.TEXT, identifier=True)

    src = spec.enable_corrections(default_weight=0.99)
    assert src.name == CORRECTIONS_SOURCE_NAME
    binding_pairs = {(b.source.name, b.class_.name) for b in spec.source_bindings}
    assert (CORRECTIONS_SOURCE_NAME, "Movie") in binding_pairs
    assert (CORRECTIONS_SOURCE_NAME, "Person") in binding_pairs


def test_enable_corrections_skips_abstract_and_virtual_classes():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    movie = spec.add_class("Movie", is_a=title)
    movie.add_virtual("DirectedMovie", where=movie.col.canonical_id.is_not_null())

    spec.enable_corrections()
    binding_classes = {b.class_.name for b in spec.source_bindings}
    assert "Movie" in binding_classes
    assert "Title" not in binding_classes
    assert "DirectedMovie" not in binding_classes


def test_enable_corrections_is_idempotent():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    src1 = spec.enable_corrections()
    src2 = spec.enable_corrections()
    assert src1 is src2  # second call returns the same Source
    # Bindings count unchanged on second call.
    assert (
        sum(1 for b in spec.source_bindings if b.source.name == CORRECTIONS_SOURCE_NAME)
        == 1
    )


def test_corrections_source_name_is_reserved():
    spec = Spec(id="m", version="0.1")
    with pytest.raises(ValueError, match="reserved source name"):
        spec.add_source(CORRECTIONS_SOURCE_NAME)


def test_corrections_default_weight_dominates():
    """``enable_corrections`` seeds the corrections binding with a
    very large default_weight (1e6 by default) so corrections win the
    resolver's argmax against any declared source."""
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    spec.enable_corrections()
    b = movie.corrections_binding()
    assert b.default_weight == 1e6


def test_corrections_custom_default_weight():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    spec.enable_corrections(default_weight=12.5)
    b = movie.corrections_binding()
    assert b.default_weight == 12.5


def test_corrections_binding_raises_when_disabled():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    with pytest.raises(KeyError, match="enable_corrections"):
        movie.corrections_binding()


# ---------------------------------------------------------------------------
# binding.close_out_sql — used to withdraw a correction (no replacement INSERT)
# ---------------------------------------------------------------------------


def test_close_out_sql_targets_correct_table_and_source():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    spec.enable_corrections()
    b = movie.corrections_binding()
    sql = emit_close_out_sql(b)
    assert "UPDATE knot_data.movie_bindings" in sql
    assert "SET valid_to = now()" in sql
    assert "source_name = '_user_corrections'" in sql
    assert "%(canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    assert "valid_to IS NULL" in sql


def test_close_out_sql_parses_postgres():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("imdb")
    b = src.bind(movie)
    sqlglot.parse_one(emit_close_out_sql(b), dialect="postgres")


def test_close_out_sql_uses_class_identifier_column_name():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("imdb_id", types.TEXT, identifier=True)  # non-default ident
    src = spec.add_source("imdb")
    b = src.bind(movie)
    sql = emit_close_out_sql(b)
    # The WHERE clause uses the actual identifier slot's column name,
    # not the literal "canonical_id".
    assert "imdb_id = %(canonical_id)s" in sql


def test_close_out_sql_escapes_apostrophe_in_source_name():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate apostrophe
    b = src.bind(movie)
    sql = emit_close_out_sql(b)
    assert "'o''brien'" in sql


def test_close_out_sql_rejects_abstract_class():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("imdb")
    # Construct an abstract binding directly (the builder forbids it).
    b = SourceBinding(source=src, class_=title)
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_close_out_sql(b)


def test_close_out_sql_via_binding_method_matches_free_function():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("imdb")
    b = src.bind(movie)
    assert b.close_out_sql() == emit_close_out_sql(b)


# ---------------------------------------------------------------------------
# End-to-end: corrections write through binding.write_sql + weight seed
# ---------------------------------------------------------------------------


def test_corrections_write_uses_same_scd2_machinery():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    spec.enable_corrections()
    b = movie.corrections_binding()
    close_out, insert = b.write_sql()
    # Same SCD2 machinery as any other binding write.
    assert "UPDATE knot_data.movie_bindings" in close_out
    assert "source_name = '_user_corrections'" in close_out
    assert "INSERT INTO knot_data.movie_bindings" in insert
    assert "'_user_corrections'" in insert  # baked in as INSERT SELECT literal


def test_corrections_appear_in_weight_seed():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    spec.enable_corrections(default_weight=0.95)
    seeds = emit_weight_seed(spec)
    # One row per (source, class, non-identifier-slot). Corrections binds
    # to every concrete class with the same default_weight applied to
    # every non-identifier slot.
    correction_seed = next(
        (sql, p) for sql, p in seeds if p[0] == CORRECTIONS_SOURCE_NAME
    )
    assert correction_seed[1] == [CORRECTIONS_SOURCE_NAME, "Movie", "year", 0.95]
