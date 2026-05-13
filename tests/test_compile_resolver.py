"""knot.compile.resolver — per-class resolved-view emission."""

import pytest
import sqlglot

from knot import OntologyClass, Primitive, Spec
from knot.compile import emit_resolved_view, emit_resolved_views


def test_resolved_view_per_concrete_class(movie_spec):
    views = emit_resolved_views(movie_spec)
    # Title is abstract → no view; Movie, Person, Credit are concrete.
    # DirectedMovie is a VirtualClass — not handled by the resolver.
    assert len(views) == 3
    for v in views:
        assert "CREATE VIEW knot_data." in v
        assert "_resolved AS" in v


def test_resolved_view_targets_bindings_table_with_valid_to_null(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(movie_spec, movie)
    assert "FROM knot_data.movie_bindings" in v
    assert "b.valid_to IS NULL" in v


def test_resolved_view_argmax_uses_binding_accuracy(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(movie_spec, movie)
    # movie_spec binds imdb with accuracy 0.85
    assert "b.source_name = 'imdb'" in v
    assert "0.85" in v


def test_resolved_view_multi_source_accuracy_case():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)
    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    spec.bind(tmdb, movie, identifier=movie["canonical_id"], accuracy=0.7)
    v = emit_resolved_view(spec, movie)
    assert "WHEN b.source_name = 'imdb' THEN 0.85" in v
    assert "WHEN b.source_name = 'tmdb' THEN 0.7" in v
    # Unknown sources fall to 0 — tie-break still deterministic.
    assert "ELSE 0" in v


def test_resolved_view_one_row_per_canonical_id(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(movie_spec, movie)
    # Outer FROM enumerates DISTINCT canonical_ids with at least one
    # currently-open binding.
    assert "SELECT DISTINCT canonical_id" in v


def test_resolved_view_inherited_slots_present(movie_spec):
    # Movie inherits `name` from Title; the resolved view should
    # project it.
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(movie_spec, movie)
    assert " AS name" in v
    assert " AS year" in v
    assert " AS genres" in v


def test_resolved_view_skips_identifier_in_select_list(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(movie_spec, movie)
    # canonical_id appears as the outer projection but NOT as a
    # winner subquery (no `AS canonical_id` on a SELECT b.canonical_id…).
    assert v.count("AS canonical_id") == 0  # outer projection has no AS rename
    assert "cb.canonical_id" in v


def test_resolved_view_parses_postgres(movie_spec):
    for v in emit_resolved_views(movie_spec):
        sqlglot.parse_one(v, dialect="postgres")


def test_resolved_view_if_not_exists_swaps_create(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(movie_spec, movie, if_not_exists=True)
    assert v.startswith("CREATE OR REPLACE VIEW")


def test_resolved_view_kwargs_threading(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    v = emit_resolved_view(
        movie_spec, movie,
        schema="alt", bindings_suffix="__s", resolved_suffix="__r",
    )
    assert v.startswith("CREATE VIEW alt.movie__r AS")
    assert "FROM alt.movie__s" in v


def test_resolved_view_rejects_abstract_class():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", Primitive.TEXT, identifier=True)
    with pytest.raises(ValueError, match="concrete classes"):
        emit_resolved_view(spec, title)


def test_resolved_view_no_bindings_yields_zero_accuracy_case():
    # Class with NO source bindings — accuracy CASE has no WHEN branches.
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)
    v = emit_resolved_view(spec, movie)
    # With no bindings the CASE collapses to literal 0::double precision.
    assert "0::double precision" in v
    sqlglot.parse_one(v, dialect="postgres")


def test_emit_ddl_appends_resolved_views(movie_spec):
    from knot.compile import emit_ddl

    stmts = emit_ddl(movie_spec)
    resolved = [s for s in stmts if "_resolved AS" in s]
    assert len(resolved) == 3  # Movie, Person, Credit


def test_emit_ddl_can_disable_resolved_views(movie_spec):
    from knot.compile import emit_ddl

    stmts = emit_ddl(movie_spec, emit_resolved_views=False)
    assert not any("_resolved AS" in s for s in stmts)
