"""knot — _user_corrections is always-on per spec + retract_sql."""

import pytest
import sqlglot

from knot import CORRECTIONS_SOURCE_NAME, SourceBinding, Spec, types
from knot.compile import emit_retract_sql
from knot.spec import ClassKind

# ---------------------------------------------------------------------------
# Corrections source ships by default — every spec has the human-override
# surface. No enable step.
# ---------------------------------------------------------------------------


def test_corrections_source_exists_on_fresh_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    assert CORRECTIONS_SOURCE_NAME in spec.sources


def test_concrete_class_auto_binds_to_corrections():
    spec = Spec(identifier_slot_name="canonical_id")
    spec.add_class("Movie")
    spec.add_class("Person")

    pairs = {(b.source.name, b.class_.name) for b in spec.source_bindings}
    assert (CORRECTIONS_SOURCE_NAME, "Movie") in pairs
    assert (CORRECTIONS_SOURCE_NAME, "Person") in pairs


def test_abstract_and_virtual_classes_skipped():
    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind=ClassKind.ABSTRACT)
    movie = spec.add_class("Movie", is_a=title)
    movie.add_virtual("DirectedMovie", where=movie.col.canonical_id.is_not_null())

    binding_classes = {b.class_.name for b in spec.source_bindings}
    assert "Movie" in binding_classes
    assert "Title" not in binding_classes
    assert "DirectedMovie" not in binding_classes


def test_add_source_with_corrections_name_rejected():
    """The reserved name can't be re-added — Spec.__post_init__ already
    created it."""
    spec = Spec(identifier_slot_name="canonical_id")
    with pytest.raises(ValueError, match="already has a source"):
        spec.add_source(CORRECTIONS_SOURCE_NAME)


def test_corrections_binding_returns_source_binding():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)

    cb = movie.corrections_binding()
    assert isinstance(cb, SourceBinding)
    assert cb.source.name == CORRECTIONS_SOURCE_NAME
    assert cb.class_ is movie


def test_corrections_binding_unavailable_for_abstract_class():
    spec = Spec(identifier_slot_name="canonical_id")
    abstract = spec.add_class("Mixin", kind=ClassKind.ABSTRACT)
    with pytest.raises(KeyError, match="only concrete classes"):
        abstract.corrections_binding()


def test_corrections_binding_exposes_runtime_weight_emitter():
    """Corrections starts unweighted (resolver COALESCE 0). The host
    sets a dominant weight at runtime via the binding's upsert_weight_sql."""
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)

    sql = movie.corrections_binding().upsert_weight_sql()
    assert "INSERT INTO knot_data.source_weight" in sql
    assert f"'{CORRECTIONS_SOURCE_NAME}'" in sql
    assert "%(slot_name)s" in sql
    assert "%(weight)s" in sql


# ---------------------------------------------------------------------------
# End-to-end: corrections write through binding.write_sql
# ---------------------------------------------------------------------------


def test_corrections_write_uses_same_upsert_machinery():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)

    sql = movie.corrections_binding().write_sql()
    assert "INSERT INTO knot_data.movie_bindings" in sql
    assert f"'{CORRECTIONS_SOURCE_NAME}'" in sql
    assert "ON CONFLICT (source_name, source_identifier) DO UPDATE SET" in sql


# ---------------------------------------------------------------------------
# binding.retract_sql — withdraw a correction (no replacement INSERT)
# ---------------------------------------------------------------------------


def test_retract_sql_targets_correct_table_and_source():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    sql = emit_retract_sql(movie.corrections_binding())
    assert "DELETE FROM knot_data.movie_bindings" in sql
    assert f"source_name = '{CORRECTIONS_SOURCE_NAME}'" in sql
    assert "%(canonical_id)s" in sql
    assert "%(source_identifier)s" in sql


def test_retract_sql_parses_postgres():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("imdb")
    sqlglot.parse_one(emit_retract_sql(src.bind(movie)), dialect="postgres")


def test_retract_sql_uses_class_identifier_column_name():
    spec = Spec(identifier_slot_name="imdb_id")
    movie = spec.add_class("Movie")
    src = spec.add_source("imdb")
    sql = emit_retract_sql(src.bind(movie))
    assert "imdb_id = %(canonical_id)s" in sql


def test_retract_sql_rejects_abstract_class():
    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind=ClassKind.ABSTRACT)
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
