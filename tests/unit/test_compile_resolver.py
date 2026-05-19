"""knot.compile.resolver — per-class resolved-view emission."""

import pytest
import sqlglot

from knot import Spec, types
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
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(movie_spec, movie)
    assert "FROM knot_data.movie_bindings" in v
    assert "b.valid_to IS NULL" in v


def test_resolved_view_left_joins_weight_table(movie_spec):
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(movie_spec, movie)
    # Resolver reads weight from a runtime table via LEFT JOIN on the
    # (source, class, slot) triple. The class/slot filters scope to
    # the per-slot weight row.
    assert "LEFT JOIN knot_data.source_weight w" in v
    assert "w.source_name = b.source_name" in v
    assert "w.class_name = 'Movie'" in v
    assert "w.slot_name = 'year'" in v
    assert "COALESCE(w.weight, 0) DESC" in v
    # Weight literals are NOT baked into the view anymore.
    assert "0.85" not in v


def test_resolved_view_no_inline_case_when():
    """The pre-weight-table inline CASE WHEN shape is gone — refactor
    moved score values into source_weight at runtime."""
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    imdb.bind(movie).set_default_weight(0.85)
    tmdb.bind(movie).set_default_weight(0.7)
    v = emit_resolved_view(spec, movie)
    assert "WHEN b.source_name" not in v
    assert "LEFT JOIN" in v
    assert "COALESCE(w.weight, 0)" in v


def test_resolved_view_one_row_per_canonical_id(movie_spec):
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(movie_spec, movie)
    # Outer FROM enumerates DISTINCT canonical_ids with at least one
    # currently-open binding.
    assert "SELECT DISTINCT canonical_id" in v


def test_resolved_view_inherited_slots_present(movie_spec):
    # Movie inherits `name` from Title; the resolved view should
    # project it.
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(movie_spec, movie)
    assert " AS name" in v
    assert " AS year" in v
    assert " AS genres" in v


def test_resolved_view_skips_identifier_in_select_list(movie_spec):
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(movie_spec, movie)
    # canonical_id appears as the outer projection but NOT as a
    # winner subquery (no `AS canonical_id` on a SELECT b.canonical_id…).
    assert v.count("AS canonical_id") == 0  # outer projection has no AS rename
    assert "cb.canonical_id" in v


def test_resolved_view_parses_postgres(movie_spec):
    for v in emit_resolved_views(movie_spec):
        sqlglot.parse_one(v, dialect="postgres")


def test_resolved_view_if_not_exists_swaps_create(movie_spec):
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(movie_spec, movie, if_not_exists=True)
    assert v.startswith("CREATE OR REPLACE VIEW")


def test_resolved_view_kwargs_threading(movie_spec):
    movie = movie_spec.classes["Movie"]
    v = emit_resolved_view(
        movie_spec,
        movie,
        schema="alt",
        bindings_suffix="__s",
        resolved_suffix="__r",
    )
    assert v.startswith("CREATE VIEW alt.movie__r AS")
    assert "FROM alt.movie__s" in v


def test_resolved_view_rejects_abstract_class():
    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind="abstract")
    with pytest.raises(ValueError, match="concrete classes"):
        emit_resolved_view(spec, title)


def test_resolved_view_unbound_sources_fall_to_zero_via_coalesce():
    # Sources not present in source_weight LEFT JOIN to NULL; the
    # COALESCE collapses them to 0 and the tie-break loses against any
    # source that DOES have a row.
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    v = emit_resolved_view(spec, movie)
    assert "COALESCE(w.weight, 0)" in v
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


# ---------------------------------------------------------------------------
# All-sources / provenance view
# ---------------------------------------------------------------------------


def test_all_sources_view_per_concrete_class(movie_spec):
    from knot.compile import emit_all_sources_views

    views = emit_all_sources_views(movie_spec)
    assert len(views) == 3  # Movie, Person, Credit
    for v in views:
        assert "CREATE VIEW knot_data." in v
        assert "_all_sources AS" in v


def test_all_sources_view_uses_jsonb_object_agg(movie_spec):
    from knot.compile import emit_all_sources_view

    movie = movie_spec.classes["Movie"]
    v = emit_all_sources_view(movie_spec, movie)
    assert "jsonb_object_agg" in v
    assert "jsonb_build_object('value', b.year, 'weight'," in v
    # Per-slot filter excludes sources contributing NULL.
    assert "FILTER (WHERE b.year IS NOT NULL) AS year" in v


def test_all_sources_view_per_slot_weight_join(movie_spec):
    from knot.compile import emit_all_sources_view

    movie = movie_spec.classes["Movie"]
    v = emit_all_sources_view(movie_spec, movie)
    # Each non-identifier slot gets its own LEFT JOIN aliased w_<slot>.
    assert "LEFT JOIN knot_data.source_weight w_year" in v
    assert "w_year.source_name = b.source_name" in v
    assert "w_year.class_name = 'Movie'" in v
    assert "w_year.slot_name = 'year'" in v
    assert "COALESCE(w_year.weight, 0)" in v


def test_all_sources_view_groups_by_identifier(movie_spec):
    from knot.compile import emit_all_sources_view

    movie = movie_spec.classes["Movie"]
    v = emit_all_sources_view(movie_spec, movie)
    assert "GROUP BY b.canonical_id" in v
    assert "WHERE b.valid_to IS NULL AND b.canonical_id IS NOT NULL" in v


def test_all_sources_view_parses_postgres(movie_spec):
    from knot.compile import emit_all_sources_views

    for v in emit_all_sources_views(movie_spec):
        sqlglot.parse_one(v, dialect="postgres")


def test_all_sources_view_if_not_exists_swaps_create(movie_spec):
    from knot.compile import emit_all_sources_view

    movie = movie_spec.classes["Movie"]
    v = emit_all_sources_view(movie_spec, movie, if_not_exists=True)
    assert v.startswith("CREATE OR REPLACE VIEW")


def test_all_sources_view_kwargs_threading(movie_spec):
    from knot.compile import emit_all_sources_view

    movie = movie_spec.classes["Movie"]
    v = emit_all_sources_view(
        movie_spec,
        movie,
        schema="alt",
        bindings_suffix="__s",
        all_sources_suffix="__p",
    )
    assert v.startswith("CREATE VIEW alt.movie__p AS")
    assert "FROM alt.movie__s b" in v


def test_all_sources_view_rejects_abstract_class():
    from knot.compile import emit_all_sources_view

    spec = Spec(identifier_slot_name="canonical_id")
    title = spec.add_class("Title", kind="abstract")
    with pytest.raises(ValueError, match="concrete classes"):
        emit_all_sources_view(spec, title)


def test_emit_ddl_appends_all_sources_views(movie_spec):
    from knot.compile import emit_ddl

    stmts = emit_ddl(movie_spec)
    all_sources = [s for s in stmts if "_all_sources AS" in s]
    assert len(all_sources) == 3  # Movie, Person, Credit


def test_emit_ddl_can_disable_all_sources_views(movie_spec):
    from knot.compile import emit_ddl

    stmts = emit_ddl(movie_spec, emit_all_sources_views=False)
    assert not any("_all_sources AS" in s for s in stmts)
