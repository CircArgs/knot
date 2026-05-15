"""knot.compile.write — batched SCD2 binding write emission."""

import json

import pytest
import sqlglot

from knot import Severity, SourceBinding, Spec, types
from knot.compile import BatchWrite, ClassWrites, emit_batch_write


def _all_sql(bw: BatchWrite) -> str:
    """Concatenate every statement's SQL — convenience for assertions
    that want to check 'is this fragment anywhere in the batch.'"""
    return "\n\n".join(sql for sql, _ in bw.statements)


def _all_params(bw: BatchWrite) -> dict:
    """Merge every statement's params into one dict — convenience for
    assertions that want to inspect the full param surface."""
    merged: dict = {}
    for _, p in bw.statements:
        merged.update(p)
    return merged


# ---------------------------------------------------------------------------
# Public dataclass shape
# ---------------------------------------------------------------------------


def test_returns_batch_write_dataclass(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b, rows=[{"canonical_id": "m1", "source_identifier": "i1"}]
            )
        ],
    )
    assert isinstance(bw, BatchWrite)


def test_affected_classes_reports_each_class(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b, rows=[{"canonical_id": "m1", "source_identifier": "i1"}]
            )
        ],
    )
    assert bw.affected_classes == ("Movie",)


# ---------------------------------------------------------------------------
# Single-class single-row (mapped)
# ---------------------------------------------------------------------------


def test_mapped_single_row_emits_close_out_and_insert(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    }
                ],
            )
        ],
        enforce=False,
    )
    assert "UPDATE knot_data.movie_bindings" in _all_sql(bw)
    assert "INSERT INTO knot_data.movie_bindings" in _all_sql(bw)
    assert "source_name = 'imdb'" in _all_sql(bw)  # baked-in literal
    assert "jsonb_array_elements(%(imdb_movie_rows)s::jsonb)" in _all_sql(bw)


def test_mapped_rows_serialized_as_jsonb_array(movie_spec):
    b = movie_spec.source_bindings[0]
    rows = [
        {"canonical_id": "m1", "source_identifier": "tt001", "release_year": 2020},
        {"canonical_id": "m2", "source_identifier": "tt002", "release_year": 2021},
    ]
    bw = emit_batch_write(
        movie_spec,
        [ClassWrites(binding=b, rows=rows)],
        enforce=False,
    )
    assert json.loads(_all_params(bw)["imdb_movie_rows"]) == rows


def test_mapped_sql_shape_independent_of_row_count(movie_spec):
    b = movie_spec.source_bindings[0]
    one = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=False,
    )
    many = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": f"m{i}",
                        "source_identifier": f"tt{i:03}",
                        "release_year": 2000 + i,
                    }
                    for i in range(50)
                ],
            )
        ],
        enforce=False,
    )
    # Constant SQL size, just the jsonb param payload grows.
    assert _all_sql(one) == _all_sql(many)


def test_mapped_unmapped_slot_lands_null(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=False,
    )
    # `genres` and `name` aren't mapped — should be NULL in the SELECT projection
    assert "NULL" in _all_sql(bw)


def test_mapped_insert_includes_raw_payload_column_and_projection(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=False,
    )
    # raw_payload is the last column in the INSERT and is sourced from
    # the inner subquery's r passthrough.
    assert "raw_payload)" in _all_sql(bw)
    assert "r AS __raw_payload" in _all_sql(bw)
    assert "raw.__raw_payload" in _all_sql(bw)


# ---------------------------------------------------------------------------
# use_mappings=False (direct slot values)
# ---------------------------------------------------------------------------


def test_direct_slot_values_use_jsonb_extraction(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "name": "Test Film",
                        "year": 2020,
                        "genres": ["drama"],
                        "runtime_minutes": 110,
                    },
                ],
            )
        ],
        enforce=False,
    )
    # For movie_spec, "year" is mapped to source_slot="release_year" so the
    # row dict's "year" key isn't read directly. This test now validates the
    # passthrough form for "name" (unmapped → implicit passthrough, same name).
    assert "raw.name::text" in _all_sql(bw)
    # Arrays go through unnest+ARRAY round-trip via __raw_payload.
    assert "jsonb_array_elements(raw.__raw_payload->'genres')" in _all_sql(bw)


def test_full_row_lands_in_raw_payload_column(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {"canonical_id": "m1", "source_identifier": "tt001", "year": 2020},
                ],
            )
        ],
        enforce=False,
    )
    # raw_payload is the last column in the INSERT and is sourced from
    # the preserved jsonb element via the raw subquery's __raw_payload
    # alias.
    assert "raw_payload)" in _all_sql(bw)
    assert "raw.__raw_payload" in _all_sql(bw)


# ---------------------------------------------------------------------------
# Multi-class batch
# ---------------------------------------------------------------------------


def test_multi_class_batch_emits_both_classes(movie_spec):
    movie_b = movie_spec.source_bindings[0]
    # Build a credit binding for the same source
    imdb = movie_spec.sources[0]
    credit = next(c for c in movie_spec.classes if c.name == "Credit")
    credit_b = imdb.bind(credit, base_trust=0.8)

    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=movie_b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            ),
            ClassWrites(
                binding=credit_b,
                rows=[
                    {
                        "canonical_id": "c1",
                        "source_identifier": "cr001",
                        "role": "director",
                        "movie": "m1",
                        "person": "p1",
                    },
                ],
            ),
        ],
        enforce=False,
    )
    assert "knot_data.movie_bindings" in _all_sql(bw)
    assert "knot_data.credit_bindings" in _all_sql(bw)
    assert bw.affected_classes == ("Credit", "Movie")  # sorted
    assert set(_all_params(bw).keys()) == {"imdb_movie_rows", "imdb_credit_rows"}


def test_duplicate_class_in_batch_rejected(movie_spec):
    b = movie_spec.source_bindings[0]
    with pytest.raises(ValueError, match="duplicate ClassWrites"):
        emit_batch_write(
            movie_spec,
            [
                ClassWrites(
                    binding=b, rows=[{"canonical_id": "m1", "source_identifier": "i1"}]
                ),
                ClassWrites(
                    binding=b, rows=[{"canonical_id": "m2", "source_identifier": "i2"}]
                ),
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
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=True,
    )
    assert "DO $$" in _all_sql(bw)
    assert "RAISE EXCEPTION" in _all_sql(bw)
    assert "year_sane" in _all_sql(bw)


def test_enforce_false_omits_do_block(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=False,
    )
    assert "DO $$" not in _all_sql(bw)


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
        [
            ClassWrites(
                binding=movie_b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=True,
    )
    assert "year_sane" in _all_sql(bw)
    assert "role_present" not in _all_sql(bw)


def test_enforce_skips_warning_severity():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    spec.add_constraint(
        "warn_only",
        primary=movie,
        body=movie.col.year > 1900,
        severity=Severity.WARNING,
    )
    src = spec.add_source("imdb")
    b = src.bind(movie)

    bw = emit_batch_write(
        spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {"canonical_id": "m1", "source_identifier": "i1", "year": 2020},
                ],
            )
        ],
        enforce=True,
    )
    # No error-severity constraint affects this batch → no DO block.
    assert "DO $$" not in _all_sql(bw)
    assert "warn_only" not in _all_sql(bw)


def test_enforce_no_constraints_at_all_no_do_block():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("imdb")
    b = src.bind(movie)
    bw = emit_batch_write(
        spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {"canonical_id": "m1", "source_identifier": "i1"},
                ],
            )
        ],
        enforce=True,
    )
    assert "DO $$" not in _all_sql(bw)


# ---------------------------------------------------------------------------
# Schema/suffix kwargs
# ---------------------------------------------------------------------------


def test_schema_and_suffix_kwargs(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "i1",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        schema="alt",
        bindings_suffix="__s",
        enforce=False,
    )
    assert "alt.movie__s" in _all_sql(bw)
    assert "knot_data.movie_bindings" not in _all_sql(bw)


# ---------------------------------------------------------------------------
# Source name escaping (literal embedded in SQL)
# ---------------------------------------------------------------------------


def test_source_name_apostrophe_escaped():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("o_brien")
    src.name = "o'brien"  # simulate an unescaped apostrophe
    b = src.bind(movie)
    bw = emit_batch_write(
        spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {"canonical_id": "m1", "source_identifier": "i1"},
                ],
            )
        ],
        enforce=False,
    )
    assert "'o''brien'" in _all_sql(bw)


# ---------------------------------------------------------------------------
# Abstract / virtual class rejection
# ---------------------------------------------------------------------------


def test_abstract_class_rejected():
    spec = Spec(id="m", version="0.1")
    abstract = spec.add_class("A", kind="abstract")
    abstract.slot("canonical_id", types.TEXT, identifier=True)
    src = spec.add_source("s")
    b = SourceBinding(source=src, class_=abstract)
    spec.source_bindings.append(b)
    with pytest.raises(ValueError, match="abstract"):
        emit_batch_write(
            spec,
            [
                ClassWrites(
                    binding=b,
                    rows=[
                        {"canonical_id": "a1", "source_identifier": "i1"},
                    ],
                )
            ],
            enforce=False,
        )


# ---------------------------------------------------------------------------
# SQL parses
# ---------------------------------------------------------------------------


def test_emitted_sql_parses_postgres(movie_spec):
    b = movie_spec.source_bindings[0]
    bw = emit_batch_write(
        movie_spec,
        [
            ClassWrites(
                binding=b,
                rows=[
                    {
                        "canonical_id": "m1",
                        "source_identifier": "tt001",
                        "release_year": 2020,
                    },
                ],
            )
        ],
        enforce=True,
    )
    # Multi-statement script — parse each separately (skip DO block,
    # which sqlglot can't fully parse).
    parts = [p.strip() for p in _all_sql(bw).split("\n\n") if p.strip()]
    for p in parts:
        if p.startswith("DO $$"):
            continue
        sqlglot.parse_one(p, dialect="postgres")
