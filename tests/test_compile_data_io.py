"""knot.compile.data_io — batched SCD2 binding write emission."""

import json

import pytest
import sqlglot

from knot import OntologyClass, Primitive, Severity, Source, SourceBinding, Spec
from knot.compile import BatchWrite, ClassWrites, emit_batch_write


# ---------------------------------------------------------------------------
# Public dataclass shape
# ---------------------------------------------------------------------------


def test_returns_batch_write_dataclass(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[{"canonical_id": "m1", "source_identifier": "i1"}])],
    )
    assert isinstance(bw, BatchWrite)


def test_affected_classes_reports_each_class(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[{"canonical_id": "m1", "source_identifier": "i1"}])],
    )
    assert bw.affected_classes == ("Movie",)


# ---------------------------------------------------------------------------
# Single-class single-row (mapped)
# ---------------------------------------------------------------------------


def test_mapped_single_row_emits_close_out_and_insert(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(
            binding=b,
            rows=[{"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020}],
            use_mappings=True,
        )],
        enforce=False,
    )
    assert "UPDATE knot_data.movie_bindings" in bw.sql
    assert "INSERT INTO knot_data.movie_bindings" in bw.sql
    assert "source_name = 'imdb'" in bw.sql  # baked-in literal
    assert "jsonb_array_elements(%(movie_rows)s::jsonb)" in bw.sql


def test_mapped_rows_serialized_as_jsonb_array(movie_spec):
    b = movie_spec.source_bindings[0]
    rows = [
        {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        {"canonical_id": "m2", "source_identifier": "tt002", "release_year": 2021},
    ]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=rows, use_mappings=True)],
        enforce=False,
    )
    assert json.loads(bw.params["movie_rows"]) == rows


def test_mapped_sql_shape_independent_of_row_count(movie_spec):
    b = movie_spec.source_bindings[0]
    one = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        ], use_mappings=True)],
        enforce=False,
    )
    many = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": f"m{i}", "source_identifier": f"tt{i:03}", "release_year": 2000 + i}
            for i in range(50)
        ], use_mappings=True)],
        enforce=False,
    )
    # Constant SQL size, just the jsonb param payload grows.
    assert one.sql == many.sql


def test_mapped_unmapped_slot_lands_null(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        ], use_mappings=True)],
        enforce=False,
    )
    # `genres` and `name` aren't mapped — should be NULL in the SELECT projection
    assert "NULL" in bw.sql


# ---------------------------------------------------------------------------
# use_mappings=False (direct slot values)
# ---------------------------------------------------------------------------


def test_direct_slot_values_use_jsonb_extraction(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {
                "canonical_id": "m1",
                "source_identifier": "tt001",
                "name": "Test Film",
                "year": 2020,
                "genres": ["drama"],
                "runtime_minutes": 110,
            },
        ], use_mappings=False)],
        enforce=False,
    )
    # Direct values means slot names appear as jsonb extracts with casts.
    assert "(r->>'year')::integer" in bw.sql
    # Arrays go through unnest+ARRAY round-trip
    assert "jsonb_array_elements(r->'genres')" in bw.sql


# ---------------------------------------------------------------------------
# Multi-class batch
# ---------------------------------------------------------------------------


def test_multi_class_batch_emits_both_classes(movie_spec):
    movie_b = movie_spec.source_bindings[0]
    # Build a credit binding for the same source
    imdb = movie_spec.sources[0]
    credit = next(c for c in movie_spec.classes if c.name == "Credit")
    credit_b = movie_spec.bind(imdb, credit, identifier=credit["canonical_id"], accuracy=0.8)

    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(binding=movie_b, rows=[
                {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
            ], use_mappings=True),
            ClassWrites(binding=credit_b, rows=[
                {"canonical_id": "c1", "source_identifier": "cr001",
                 "role": "director", "movie": "m1", "person": "p1"},
            ], use_mappings=False),
        ],
        enforce=False,
    )
    assert "knot_data.movie_bindings" in bw.sql
    assert "knot_data.credit_bindings" in bw.sql
    assert bw.affected_classes == ("Credit", "Movie")  # sorted
    assert set(bw.params.keys()) == {"movie_rows", "credit_rows"}


def test_duplicate_class_in_batch_rejected(movie_spec):
    b = movie_spec.source_bindings[0]
    with pytest.raises(ValueError, match="duplicate ClassWrites"):
        emit_batch_write(
            movie_spec,
            [
                ClassWrites(binding=b, rows=[{"canonical_id": "m1", "source_identifier": "i1"}]),
                ClassWrites(binding=b, rows=[{"canonical_id": "m2", "source_identifier": "i2"}]),
            ],
            enforce=False,
        )


def test_empty_writes_rejected(movie_spec):
    with pytest.raises(ValueError, match="empty"):
        emit_batch_write(movie_spec, [], enforce=False)


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


def test_enforce_true_appends_do_block(movie_spec):
    # movie_spec has constraint 'year_sane' (ERROR) on Movie
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        ], use_mappings=True)],
        enforce=True,
    )
    assert "DO $$" in bw.sql
    assert "RAISE EXCEPTION" in bw.sql
    assert "year_sane" in bw.sql


def test_enforce_false_omits_do_block(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        ], use_mappings=True)],
        enforce=False,
    )
    assert "DO $$" not in bw.sql


def test_enforce_only_runs_constraints_for_affected_classes(movie_spec):
    # Add a constraint on Credit; batch only writes Movie; Credit constraint
    # should NOT appear in the DO block.
    credit = next(c for c in movie_spec.classes if c.name == "Credit")
    movie_spec.add_constraint(
        "role_present", primary=credit, body=credit.col.role.is_not_null()
    )

    movie_b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=movie_b, rows=[
            {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        ], use_mappings=True)],
        enforce=True,
    )
    assert "year_sane" in bw.sql
    assert "role_present" not in bw.sql


def test_enforce_skips_warning_severity():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)
    spec.add_constraint("warn_only", primary=movie, body=movie.col.year > 1900, severity=Severity.WARNING)
    src = spec.add_source("imdb")
    b = spec.bind(src, movie, identifier=movie["canonical_id"])

    bw = emit_batch_write(
        spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "i1", "year": 2020},
        ], use_mappings=False)],
        enforce=True,
    )
    # No error-severity constraint affects this batch → no DO block.
    assert "DO $$" not in bw.sql
    assert "warn_only" not in bw.sql


def test_enforce_no_constraints_at_all_no_do_block():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    src = spec.add_source("imdb")
    b = spec.bind(src, movie, identifier=movie["canonical_id"])
    bw = emit_batch_write(
        spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "i1"},
        ], use_mappings=False)],
        enforce=True,
    )
    assert "DO $$" not in bw.sql


# ---------------------------------------------------------------------------
# Schema/suffix kwargs
# ---------------------------------------------------------------------------


def test_schema_and_suffix_kwargs(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "i1", "release_year": 2020},
        ], use_mappings=True)],
        schema="alt",
        bindings_suffix="__s",
        enforce=False,
    )
    assert "alt.movie__s" in bw.sql
    assert "knot_data.movie_bindings" not in bw.sql


# ---------------------------------------------------------------------------
# Source name escaping (literal embedded in SQL)
# ---------------------------------------------------------------------------


def test_source_name_apostrophe_escaped():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate an unescaped apostrophe
    b = spec.bind(src, movie, identifier=movie["canonical_id"])
    bw = emit_batch_write(
        spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "i1"},
        ], use_mappings=False)],
        enforce=False,
    )
    assert "'o''brien'" in bw.sql


# ---------------------------------------------------------------------------
# Abstract / virtual class rejection
# ---------------------------------------------------------------------------


def test_abstract_class_rejected():
    spec = Spec(id="m", version="0.1")
    abstract = spec.add_class("A", kind="abstract")
    abstract.slot("canonical_id", Primitive.TEXT, identifier=True)
    src = spec.add_source("s")
    b = SourceBinding(source=src, class_=abstract, identifier_slot=abstract["canonical_id"])
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_batch_write(
            spec,
            [ClassWrites(binding=b, rows=[
                {"canonical_id": "a1", "source_identifier": "i1"},
            ], use_mappings=False)],
            enforce=False,
        )


# ---------------------------------------------------------------------------
# SQL parses
# ---------------------------------------------------------------------------


def test_emitted_sql_parses_postgres(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=[
            {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        ], use_mappings=True)],
        enforce=True,
    )
    # Multi-statement script — parse each separately (skip DO block,
    # which sqlglot can't fully parse).
    parts = [p.strip() for p in bw.sql.split("\n\n") if p.strip()]
    for p in parts:
        if p.startswith("DO $$"):
            continue
        sqlglot.parse_one(p, dialect="postgres")
