"""knot — _user_corrections synthetic source + emit_close_out."""

import pytest
import sqlglot

from knot import CORRECTIONS_SOURCE_NAME, Spec, types
from knot.compile import (
    ClassWrites,
    emit_batch_write,
    emit_close_out,
    emit_trust_seed,
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

    src = spec.enable_corrections(base_trust=0.99)
    assert src.name == CORRECTIONS_SOURCE_NAME
    binding_pairs = {(b.source.name, b.class_.name) for b in spec.source_bindings}
    assert (CORRECTIONS_SOURCE_NAME, "Movie") in binding_pairs
    assert (CORRECTIONS_SOURCE_NAME, "Person") in binding_pairs


def test_enable_corrections_skips_abstract_and_virtual_classes():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    movie = spec.add_class("Movie", is_a=title)
    spec.add_virtual_class("DirectedMovie", base=movie, where=movie.col.canonical_id.is_not_null())

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
    assert sum(1 for b in spec.source_bindings if b.source.name == CORRECTIONS_SOURCE_NAME) == 1


def test_corrections_source_name_is_reserved():
    spec = Spec(id="m", version="0.1")
    with pytest.raises(ValueError, match="reserved source name"):
        spec.add_source(CORRECTIONS_SOURCE_NAME)


def test_corrections_default_accuracy_is_high():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    spec.enable_corrections()
    b = spec.corrections_binding_for(movie)
    assert b.base_trust == 0.99


def test_corrections_custom_accuracy():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    spec.enable_corrections(base_trust=0.8)
    b = spec.corrections_binding_for(movie)
    assert b.base_trust == 0.8


def test_corrections_binding_for_raises_when_disabled():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    with pytest.raises(KeyError, match="enable_corrections"):
        spec.corrections_binding_for(movie)


# ---------------------------------------------------------------------------
# emit_close_out — used to withdraw a correction (no replacement INSERT)
# ---------------------------------------------------------------------------


def test_emit_close_out_targets_correct_table_and_source():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    sql = emit_close_out(
        spec,
        class_name="Movie",
        source_name=CORRECTIONS_SOURCE_NAME,
    )
    assert "UPDATE knot_data.movie_bindings" in sql
    assert "SET valid_to = now()" in sql
    assert "source_name = '_user_corrections'" in sql
    assert "%(canonical_id)s" in sql
    assert "%(source_identifier)s" in sql
    assert "valid_to IS NULL" in sql


def test_emit_close_out_parses_postgres():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    sql = emit_close_out(spec, class_name="Movie", source_name="imdb")
    sqlglot.parse_one(sql, dialect="postgres")


def test_emit_close_out_uses_class_identifier_column_name():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("imdb_id", types.TEXT, identifier=True)  # non-default ident
    sql = emit_close_out(spec, class_name="Movie", source_name="imdb")
    # The WHERE clause uses the actual identifier slot's column name,
    # not the literal "canonical_id".
    assert "imdb_id = %(canonical_id)s" in sql


def test_emit_close_out_escapes_apostrophe_in_source_name():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    sql = emit_close_out(spec, class_name="Movie", source_name="o'brien")
    assert "'o''brien'" in sql


def test_emit_close_out_rejects_abstract_class():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    with pytest.raises(ValueError, match="abstract"):
        emit_close_out(spec, class_name="Title", source_name="imdb")


def test_emit_close_out_rejects_unknown_class():
    spec = Spec(id="m", version="0.1")
    with pytest.raises(ValueError, match="no concrete class"):
        emit_close_out(spec, class_name="Ghost", source_name="imdb")


# ---------------------------------------------------------------------------
# End-to-end: corrections write through emit_batch_write + trust seed
# ---------------------------------------------------------------------------


def test_corrections_write_uses_batch_write():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    spec.enable_corrections()
    b = spec.corrections_binding_for(movie)
    bw = emit_batch_write(
        spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {"canonical_id": "m1", "source_identifier": "curator-42", "year": 1925},
                ],
            )
        ],
        enforce=False,
    )
    # Same SCD2 machinery as any other binding write.
    assert "source_name = '_user_corrections'" in bw.sql
    assert "UPDATE knot_data.movie_bindings" in bw.sql  # close-out
    assert "INSERT INTO knot_data.movie_bindings" in bw.sql


def test_corrections_appear_in_trust_seed():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    spec.enable_corrections(base_trust=0.95)
    seeds = emit_trust_seed(spec)
    # One row per (source, class, non-identifier-slot). Corrections binds
    # to every concrete class with the same base_trust applied to every
    # non-identifier slot.
    correction_seed = next((sql, p) for sql, p in seeds if p[0] == CORRECTIONS_SOURCE_NAME)
    assert correction_seed[1] == [CORRECTIONS_SOURCE_NAME, "Movie", "year", 0.95]
