"""knot.compile.write — emit_translate_fks_sql.

Re-translates a (source, class) binding's FK columns from source-ids
to canonical-ids. Idempotent; needed to recover from the re-ingest
FK-clobber where write_sql's ON CONFLICT DO UPDATE wipes FK columns
back to source-ids.
"""

import pytest
import sqlglot

from knot import CORRECTIONS_SOURCE_NAME, Spec, types
from knot.compile.write import emit_translate_fks_sql
from knot.spec import ClassKind, SourceBinding


def _make_spec():
    """Return (spec, imdb_movie_binding) — Movie has TWO ClassRef FKs
    (director → Person, studio → Studio) to exercise multi-FK CTE
    emission."""
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")

    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)

    studio = spec.add_class("Studio")
    studio.slot("name", types.TEXT, required=True)

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("director", person)
    movie.slot("studio", studio)

    imdb = spec.add_source("imdb")
    imdb.bind(person)
    imdb.bind(studio)
    imdb_movie = imdb.bind(movie)
    return spec, imdb_movie


# ---------------------------------------------------------------------------
# Parses + shape
# ---------------------------------------------------------------------------


def test_sql_parses_postgres():
    _, b = _make_spec()
    sqlglot.parse_one(emit_translate_fks_sql(b), dialect="postgres")


def test_one_update_per_fk_slot():
    _, b = _make_spec()
    sql = emit_translate_fks_sql(b)
    # Movie has 2 ClassRef slots (director, studio) → one UPDATE each.
    # Standalone statements (not WITH-CTE chain) because postgres can
    # silently prune data-modifying CTEs that share a chain.
    assert sql.count("UPDATE knot_data.movie_bindings") == 2
    assert "SET director" in sql
    assert "SET studio" in sql


def test_baked_source_literal_per_update():
    _, b = _make_spec()
    sql = emit_translate_fks_sql(b)
    # 'imdb' appears in BOTH the source filter on movie_bindings AND on
    # the target join (2 UPDATE statements × 2 occurrences each = 4)
    assert sql.count("source_name = 'imdb'") == 4


def test_targets_bindings_table_with_schema():
    _, b = _make_spec()
    sql = emit_translate_fks_sql(b)
    assert "knot_data.movie_bindings" in sql
    assert "knot_data.person_bindings" in sql
    assert "knot_data.studio_bindings" in sql


def test_update_only_matches_source_id_shaped_values():
    """The WHERE clause makes this naturally idempotent: only rows
    whose FK column still matches a known source_identifier get
    rewritten. Values that already hold canonical-ids don't match."""
    _, b = _make_spec()
    sql = emit_translate_fks_sql(b)
    assert "b.director = m.source_identifier" in sql
    assert "b.studio = m.source_identifier" in sql


def test_map_filters_unstamped_target_rows():
    """The translation map only carries (source_id → canonical_id)
    pairs where the target binding has been ER-stamped."""
    _, b = _make_spec()
    sql = emit_translate_fks_sql(b)
    # Both maps require the target's canonical_id IS NOT NULL.
    assert sql.count("canonical_id IS NOT NULL") == 2


# ---------------------------------------------------------------------------
# Corrections source — no-op
# ---------------------------------------------------------------------------


def test_corrections_source_no_op():
    """Corrections bypass the same-source-namespace assumption; they
    write canonical-ids directly. translate_fks is a no-op for them."""
    spec, _ = _make_spec()
    corr_b = spec.classes["Movie"].corrections_binding()
    sql = emit_translate_fks_sql(corr_b)
    assert "UPDATE" not in sql.upper().replace("UPDATE TABLE", "")
    assert CORRECTIONS_SOURCE_NAME in sql or "no-op" in sql


# ---------------------------------------------------------------------------
# Class with no FK slots — no-op
# ---------------------------------------------------------------------------


def test_no_fk_slots_is_no_op():
    spec, _ = _make_spec()
    person_b = spec.classes["Person"].binding_for(spec.sources["imdb"])
    sql = emit_translate_fks_sql(person_b)
    assert "UPDATE" not in sql.upper().replace("UPDATE TABLE", "")
    assert "no FK slots" in sql


# ---------------------------------------------------------------------------
# Abstract class — refused
# ---------------------------------------------------------------------------


def test_abstract_class_rejected():
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    title = spec.add_class("Title", kind=ClassKind.ABSTRACT)
    src = spec.add_source("imdb")
    b = SourceBinding(source=src, class_=title)
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_translate_fks_sql(b)


# ---------------------------------------------------------------------------
# Binding method matches the free-function
# ---------------------------------------------------------------------------


def test_binding_method_matches_free_function():
    _, b = _make_spec()
    assert b.translate_fks_sql() == emit_translate_fks_sql(
        b, schema=b._require_spec().schema
    )


# ---------------------------------------------------------------------------
# Knot's public surface exposes the new emitter
# ---------------------------------------------------------------------------


def test_module_reexport():
    from knot.compile import emit_translate_fks_sql as ext

    assert ext is emit_translate_fks_sql
