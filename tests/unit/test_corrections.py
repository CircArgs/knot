"""knot — _user_corrections synthetic source + retract_sql."""

import pytest
import sqlglot

from knot import CORRECTIONS_SOURCE_NAME, SourceBinding, Spec, types
from knot.compile import emit_retract_sql

# ---------------------------------------------------------------------------
# Spec.enable_corrections
# ---------------------------------------------------------------------------


def test_enable_corrections_registers_source_and_per_class_bindings():
    spec = Spec(identifier_slot_name="canonical_id")
    spec.add_class("Movie")
    spec.add_class("Person")

    src = spec.enable_corrections()
    assert src.name == CORRECTIONS_SOURCE_NAME
    binding_pairs = {(b.source.name, b.class_.name) for b in spec.source_bindings}
    assert (CORRECTIONS_SOURCE_NAME, "Movie") in binding_pairs
    assert (CORRECTIONS_SOURCE_NAME, "Person") in binding_pairs


def test_enable_corrections_skips_abstract_and_virtual_classes():
    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind="abstract")
    movie = spec.add_class("Movie", is_a=title)
    movie.add_virtual("DirectedMovie", where=movie.col.canonical_id.is_not_null())

    spec.enable_corrections()
    binding_classes = {b.class_.name for b in spec.source_bindings}
    assert "Movie" in binding_classes
    assert "Title" not in binding_classes
    assert "DirectedMovie" not in binding_classes


def test_enable_corrections_is_idempotent():
    spec = Spec(identifier_slot_name="canonical_id")
    spec.add_class("Movie")
    src1 = spec.enable_corrections()
    src2 = spec.enable_corrections()
    assert src1 is src2  # second call returns the same Source
    # Bindings count unchanged on second call.
    assert (
        sum(1 for b in spec.source_bindings if b.source.name == CORRECTIONS_SOURCE_NAME)
        == 1
    )


def test_corrections_source_name_is_reserved():
    spec = Spec(identifier_slot_name="canonical_id")
    with pytest.raises(ValueError, match="reserved source name"):
        spec.add_source(CORRECTIONS_SOURCE_NAME)


def test_corrections_binding_exposes_runtime_weight_emitters():
    """``enable_corrections`` no longer takes a default_weight kwarg —
    weights are runtime-only. The host upserts a dominating weight at
    runtime via the binding's ``upsert_weight_sql``."""
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    spec.enable_corrections()
    b = movie.corrections_binding()
    sql = b.upsert_weight_sql()
    assert "INSERT INTO knot_data.source_weight" in sql
    assert f"'{CORRECTIONS_SOURCE_NAME}'" in sql


def test_corrections_binding_raises_when_disabled():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    with pytest.raises(KeyError, match="enable_corrections"):
        movie.corrections_binding()


# ---------------------------------------------------------------------------
# binding.retract_sql — used to withdraw a correction (no replacement INSERT)
# ---------------------------------------------------------------------------


def test_retract_sql_targets_correct_table_and_source():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    spec.enable_corrections()
    b = movie.corrections_binding()
    sql = emit_retract_sql(b)
    assert "DELETE FROM knot_data.movie_bindings" in sql
    assert "source_name = '_user_corrections'" in sql
    assert "%(canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    # No SCD2 valid_to filter anymore — retraction deletes the row.
    assert "valid_to" not in sql


def test_retract_sql_parses_postgres():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("imdb")
    b = src.bind(movie)
    sqlglot.parse_one(emit_retract_sql(b), dialect="postgres")


def test_retract_sql_uses_class_identifier_column_name():
    # Spec-level override of identifier name.
    spec = Spec(identifier_slot_name="imdb_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("imdb")
    b = src.bind(movie)
    sql = emit_retract_sql(b)
    # The WHERE clause uses the actual identifier slot's column name,
    # not the literal "canonical_id".
    assert "imdb_id = %(canonical_id)s" in sql


def test_retract_sql_escapes_apostrophe_in_source_name():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate apostrophe
    b = src.bind(movie)
    sql = emit_retract_sql(b)
    assert "'o''brien'" in sql


def test_retract_sql_rejects_abstract_class():
    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind="abstract")
    src = spec.add_source("imdb")
    # Construct an abstract binding directly (the builder forbids it).
    b = SourceBinding(source=src, class_=title)
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_retract_sql(b)


def test_retract_sql_via_binding_method_matches_free_function():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("imdb")
    b = src.bind(movie)
    assert b.retract_sql() == emit_retract_sql(b)


# ---------------------------------------------------------------------------
# End-to-end: corrections write through binding.write_sql
# ---------------------------------------------------------------------------


def test_corrections_write_uses_same_upsert_machinery():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    spec.enable_corrections()
    b = movie.corrections_binding()
    sql = b.write_sql()
    # Same upsert machinery as any other binding write.
    assert "INSERT INTO knot_data.movie_bindings" in sql
    assert "'_user_corrections'" in sql  # baked in as INSERT SELECT literal
    assert "ON CONFLICT (source_name, source_identifier) DO UPDATE SET" in sql
