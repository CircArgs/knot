"""knot.compile.write.emit_update_slot_sql — batched per-slot UPDATE.

Used by host-side workers (embedding workers most prominently) to
fill ONE column on existing binding rows without re-supplying the
whole row through ``write_sql``'s upsert."""

from __future__ import annotations

import pytest
import sqlglot

from knot import Spec, types
from knot.compile import emit_update_slot_sql


def _spec_with_kitchen_sink_slots():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("genres", types.ARRAY(types.TEXT))
    movie.slot("title_embedding", types.VECTOR(384))
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    movie.slot("director", person)
    src = spec.add_source("imdb")
    return spec, src.bind(movie)


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


def test_emits_batched_update_from_jsonb():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "title")
    assert "UPDATE knot_data.movie_bindings AS b" in sql
    assert "SET title = (r->>'title')::text" in sql
    assert "FROM jsonb_array_elements(%(rows)s::jsonb) AS r" in sql
    assert "b.source_name = 'imdb'" in sql
    assert "b.source_identifier = (r->>'source_identifier')" in sql


def test_bakes_source_name_literal_not_placeholder():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "title")
    assert "'imdb'" in sql
    assert "%(source_name)s" not in sql


def test_one_named_placeholder_only():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "title")
    # %(rows)s is the only named placeholder — host binds the batch
    # as a jsonb array.
    assert "%(rows)s" in sql
    assert "%(value)s" not in sql
    assert "%(source_identifier)s" not in sql


# ---------------------------------------------------------------------------
# Type-correct casts per slot kind
# ---------------------------------------------------------------------------


def test_vector_slot_casts_to_vector_dim():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "title_embedding")
    assert "SET title_embedding = (r->>'title_embedding')::vector(384)" in sql


def test_integer_slot_casts_to_integer():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "year")
    assert "SET year = (r->>'year')::integer" in sql


def test_array_slot_round_trips_via_unnest():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "genres")
    # jsonb arrays go through ARRAY(SELECT ...) round-trip.
    assert "ARRAY(SELECT (value #>> '{}')::text" in sql
    assert "jsonb_array_elements(r->'genres')" in sql


def test_classref_slot_casts_to_text():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "director")
    # FK columns are text; the value is whatever the host bound.
    assert "SET director = (r->>'director')::text" in sql


# ---------------------------------------------------------------------------
# Schema + suffix kwargs
# ---------------------------------------------------------------------------


def test_schema_and_suffix_kwargs():
    _, b = _spec_with_kitchen_sink_slots()
    sql = emit_update_slot_sql(b, "title", schema="alt", bindings_suffix="__s")
    assert "alt.movie__s" in sql
    assert "knot_data.movie_bindings" not in sql


# ---------------------------------------------------------------------------
# Source-name apostrophe escaping
# ---------------------------------------------------------------------------


def test_source_name_apostrophe_escaped():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate apostrophe
    b = src.bind(movie)
    sql = emit_update_slot_sql(b, "title")
    assert "'o''brien'" in sql


# ---------------------------------------------------------------------------
# Rejections
# ---------------------------------------------------------------------------


def test_rejects_identifier_slot():
    _, b = _spec_with_kitchen_sink_slots()
    with pytest.raises(ValueError, match="identifier slot"):
        emit_update_slot_sql(b, "canonical_id")


def test_rejects_unknown_slot_name():
    _, b = _spec_with_kitchen_sink_slots()
    with pytest.raises(KeyError, match="not_a_slot"):
        emit_update_slot_sql(b, "not_a_slot")


def test_rejects_abstract_class():
    from knot import SourceBinding

    spec = Spec(identifier_slot_name="canonical_id")
    abstract = spec.add_class("A", kind="abstract")
    src = spec.add_source("s")
    b = SourceBinding(source=src, class_=abstract)
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_update_slot_sql(b, "any_slot")


# ---------------------------------------------------------------------------
# Postgres parses
# ---------------------------------------------------------------------------


def test_parses_postgres_for_every_slot_kind():
    _, b = _spec_with_kitchen_sink_slots()
    for slot_name in ("title", "year", "genres", "title_embedding", "director"):
        sql = emit_update_slot_sql(b, slot_name)
        sqlglot.parse_one(sql.replace("%(rows)s", "'[]'"), dialect="postgres")


# ---------------------------------------------------------------------------
# Method on SourceBinding mirrors the free function
# ---------------------------------------------------------------------------


def test_binding_update_slot_sql_method_matches():
    _, b = _spec_with_kitchen_sink_slots()
    assert b.update_slot_sql("title") == emit_update_slot_sql(b, "title")
