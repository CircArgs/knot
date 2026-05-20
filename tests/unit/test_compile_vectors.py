"""Tests for VectorRef.distance_to() — literal and cross-row forms.

Covers:
- Literal vector still compiles correctly (regression)
- Cross-row (VectorRef target) renders as col_a <op> col_b without cast
- Metric mismatch raises ValueError at construction
- Dim mismatch raises ValueError at construction
- Cross-row composes in .order_by().limit(k) (the k-NN shape)
- Cross-row composes in .where(... < 0.3)
- Emitted SQL parses as postgres (sqlglot)
"""

from __future__ import annotations

import pytest
import sqlglot

from knot import Spec, types
from knot.ast.expr import VectorRef

# ---------------------------------------------------------------------------
# Spec fixtures
# ---------------------------------------------------------------------------


def _make_spec_one_class():
    """Single class with a cosine VECTOR(4) slot."""
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("title_embedding", types.VECTOR(4))
    spec.add_source("imdb").bind(movie)
    return spec, movie


def _make_spec_two_classes():
    """Two classes each with a cosine VECTOR(4) slot — for cross-class distance."""
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("title_embedding", types.VECTOR(4))

    doc = spec.add_class("Document")
    doc.slot("body", types.TEXT, required=True)
    doc.slot("body_embedding", types.VECTOR(4))

    src = spec.add_source("src")
    src.bind(movie)
    src.bind(doc)
    return spec, movie, doc


def _make_spec_l2():
    """Single class with an l2 VECTOR(3) slot — for metric-mismatch tests."""
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    movie = spec.add_class("Movie")
    movie.slot("emb_l2", types.VECTOR(3, metric="l2"))
    spec.add_source("imdb").bind(movie)
    return spec, movie


# ---------------------------------------------------------------------------
# 1. Literal vector — regression
# ---------------------------------------------------------------------------


def test_literal_vector_renders_with_cast():
    spec, movie = _make_spec_one_class()
    target = [0.1, 0.2, 0.3, 0.4]
    q = movie.resolved.order_by(
        movie.col.title_embedding.distance_to(target), "asc"
    ).limit(5)
    sql = q.sql()
    # Must contain the cast form, not a bare column reference
    assert "::vector(4)" in sql
    assert "[0.1, 0.2, 0.3, 0.4]" in sql
    assert "<=>" in sql  # cosine operator


def test_literal_vector_renders_full_fragment():
    spec, movie = _make_spec_one_class()
    target = [0.1, 0.2, 0.3, 0.4]
    q = movie.resolved.order_by(
        movie.col.title_embedding.distance_to(target), "asc"
    ).limit(5)
    sql = q.sql()
    assert (
        "(knot_data.movie_resolved.title_embedding <=> '[0.1, 0.2, 0.3, 0.4]'::vector(4))"
        in sql
    )


# ---------------------------------------------------------------------------
# 2. Cross-row — renders col_a <op> col_b (no cast)
# ---------------------------------------------------------------------------


def test_cross_row_same_class_renders_col_op_col():
    """distance_to(VectorRef) → col <=> col, no ::vector cast."""
    spec, movie = _make_spec_one_class()
    emb = movie.col.title_embedding
    # distance from one slot ref to itself (self-distance; contrived but valid for compile test)
    dist = emb.distance_to(emb)
    q = movie.resolved.order_by(dist, "asc").limit(10)
    sql = q.sql()
    assert "::vector" not in sql
    assert (
        "knot_data.movie_resolved.title_embedding <=> knot_data.movie_resolved.title_embedding"
        in sql
    )


def test_cross_row_two_classes_renders_correct_tables():
    spec, movie, doc = _make_spec_two_classes()
    movie_emb = movie.col.title_embedding
    doc_emb = doc.col.body_embedding
    dist = movie_emb.distance_to(doc_emb)
    q = movie.resolved.order_by(dist, "asc").limit(10)
    sql = q.sql()
    assert "::vector" not in sql
    assert "knot_data.movie_resolved.title_embedding" in sql
    assert "knot_data.document_resolved.body_embedding" in sql
    assert "<=>" in sql


def test_cross_row_no_vector_cast():
    """Explicit: the cast must be absent in the cross-row form."""
    spec, movie, doc = _make_spec_two_classes()
    dist = movie.col.title_embedding.distance_to(doc.col.body_embedding)
    q = movie.resolved.select(dist)
    sql = q.sql()
    assert "::vector" not in sql


# ---------------------------------------------------------------------------
# 3. Metric mismatch raises ValueError at construction
# ---------------------------------------------------------------------------


def test_metric_mismatch_raises():
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    movie = spec.add_class("Movie")
    movie.slot("emb_cosine", types.VECTOR(4, metric="cosine"))
    movie.slot("emb_l2", types.VECTOR(4, metric="l2"))
    spec.add_source("s").bind(movie)

    cosine_ref = movie.col.emb_cosine
    l2_ref = movie.col.emb_l2

    assert isinstance(cosine_ref, VectorRef)
    assert isinstance(l2_ref, VectorRef)

    with pytest.raises(ValueError, match="metric mismatch"):
        cosine_ref.distance_to(l2_ref)


# ---------------------------------------------------------------------------
# 4. Dim mismatch raises ValueError at construction
# ---------------------------------------------------------------------------


def test_dim_mismatch_raises():
    spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")
    movie = spec.add_class("Movie")
    movie.slot("emb4", types.VECTOR(4))
    movie.slot("emb8", types.VECTOR(8))
    spec.add_source("s").bind(movie)

    ref4 = movie.col.emb4
    ref8 = movie.col.emb8

    assert isinstance(ref4, VectorRef)
    assert isinstance(ref8, VectorRef)

    with pytest.raises(ValueError, match="dim mismatch"):
        ref4.distance_to(ref8)


# ---------------------------------------------------------------------------
# 5. Cross-row composes in .order_by().limit(k) (the k-NN shape)
# ---------------------------------------------------------------------------


def test_cross_row_knn_shape():
    spec, movie, doc = _make_spec_two_classes()
    dist = movie.col.title_embedding.distance_to(doc.col.body_embedding)
    q = movie.resolved.order_by(dist, "asc").limit(10)
    sql = q.sql()
    assert "ORDER BY" in sql
    assert "LIMIT 10" in sql
    assert "<=>" in sql
    assert "::vector" not in sql


def test_cross_row_knn_order_direction():
    spec, movie = _make_spec_one_class()
    emb = movie.col.title_embedding
    q = movie.resolved.order_by(emb.distance_to(emb), "asc").limit(5)
    sql = q.sql()
    assert "ASC" in sql
    assert "LIMIT 5" in sql


# ---------------------------------------------------------------------------
# 6. Cross-row composes in .where(... < 0.3)
# ---------------------------------------------------------------------------


def test_cross_row_in_where_comparison():
    spec, movie, doc = _make_spec_two_classes()
    dist = movie.col.title_embedding.distance_to(doc.col.body_embedding)
    q = movie.resolved.where(dist < 0.3)
    sql = q.sql()
    assert "WHERE" in sql
    assert "<=>" in sql
    assert "0.3" in sql
    assert "::vector" not in sql


def test_cross_row_where_and_order_by():
    """Both filter and sort on cross-row distance."""
    spec, movie, doc = _make_spec_two_classes()
    dist = movie.col.title_embedding.distance_to(doc.col.body_embedding)
    q = movie.resolved.where(dist < 0.5).order_by(dist, "asc").limit(20)
    sql = q.sql()
    assert "WHERE" in sql
    assert "ORDER BY" in sql
    assert "LIMIT 20" in sql
    assert "::vector" not in sql


# ---------------------------------------------------------------------------
# 7. Sqlglot parses emitted SQL as postgres
# ---------------------------------------------------------------------------


def test_literal_vector_parses_postgres():
    spec, movie = _make_spec_one_class()
    queries = [
        movie.resolved.order_by(
            movie.col.title_embedding.distance_to([0.1, 0.2, 0.3, 0.4]), "asc"
        ).limit(5),
        movie.resolved.where(
            movie.col.title_embedding.distance_to([0.1, 0.2, 0.3, 0.4]) < 0.3
        ),
        movie.resolved.select(
            movie.col.title_embedding.distance_to([0.1, 0.2, 0.3, 0.4])
        ),
    ]
    for q in queries:
        sqlglot.parse_one(q.sql(), dialect="postgres")


def test_cross_row_parses_postgres():
    spec, movie, doc = _make_spec_two_classes()
    movie_emb = movie.col.title_embedding
    doc_emb = doc.col.body_embedding
    dist = movie_emb.distance_to(doc_emb)
    queries = [
        movie.resolved.order_by(dist, "asc").limit(10),
        movie.resolved.where(dist < 0.3),
        movie.resolved.select(dist),
        movie.resolved.where(dist < 0.5).order_by(dist, "asc").limit(20),
    ]
    for q in queries:
        sqlglot.parse_one(q.sql(), dialect="postgres")


def test_cross_row_same_class_parses_postgres():
    spec, movie = _make_spec_one_class()
    emb = movie.col.title_embedding
    q = movie.resolved.order_by(emb.distance_to(emb), "asc").limit(10)
    sqlglot.parse_one(q.sql(), dialect="postgres")
