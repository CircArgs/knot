"""Integration test for knot.compiler using the B2 fixture spec.

Uses the B2 spec (Movie, Person, Credit) directly — no docker, no postgres.
The TestEnv integration (with real orchestrator) remains xfail until dispatcher lands.
"""

from __future__ import annotations

import pytest

from knot.impact import BoundImpl
from knot.compiler import compile, compile_hash, topo_sort
from knot.workflow_spec import WorkflowSpec, StageSpec
from tests.fixtures.B2.spec import spec as b2_spec, Movie, Person, Credit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_binding(class_name: str, impl_name: str) -> BoundImpl:
    class FakeImpl:
        pass
    return BoundImpl(impl_class=FakeImpl, impl_name=impl_name, workflow=class_name)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_b2_full_pipeline_well_formed() -> None:
    """B2 spec full compile produces a well-formed WorkflowSpec."""
    wf = compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={
            "imdb_movies": "wm_movies_1",
            "tmdb_movies": "wm_movies_2",
            "wikidata_movies": "wm_movies_3",
            "imdb_persons": "wm_persons_1",
            "tmdb_persons": "wm_persons_2",
            "imdb_credits": "wm_credits_1",
            "tmdb_credits": "wm_credits_2",
            "wikidata_credits": "wm_credits_3",
        },
        scope="full",
    )

    assert isinstance(wf, WorkflowSpec)
    assert len(wf.stages) > 0

    # All 3 classes in scope
    class_names = {s.class_name for s in wf.stages}
    assert "Movie" in class_names
    assert "Person" in class_names
    assert "Credit" in class_names

    # Each class has normalize/resolve/merge/validate/publish
    for cls_name in ("Movie", "Person", "Credit"):
        cls_stages = [s for s in wf.stages if s.class_name == cls_name]
        kinds = {s.kind for s in cls_stages}
        assert "normalize" in kinds, f"{cls_name} missing normalize"
        assert "resolve" in kinds, f"{cls_name} missing resolve"
        assert "merge" in kinds, f"{cls_name} missing merge"
        assert "validate" in kinds, f"{cls_name} missing validate"
        assert "publish" in kinds, f"{cls_name} missing publish"


def test_b2_topo_order_credit_after_parents() -> None:
    """B2 topo_sort places Credit after Movie and Person."""
    sorted_classes = topo_sort(b2_spec, "full")
    names = [c.name for c in sorted_classes]

    assert "Movie" in names
    assert "Person" in names
    assert "Credit" in names
    assert names.index("Credit") > names.index("Movie")
    assert names.index("Credit") > names.index("Person")


def test_b2_credit_pinned_parent_runs() -> None:
    """Credit compile with parents_override reflects pinned hashes."""
    parents = {"Movie": "hash_movie_123", "Person": "hash_person_456"}
    wf = compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="Credit",
        parents_override=parents,
    )

    credit_resolve = next(
        (s for s in wf.stages if s.class_name == "Credit" and s.kind == "resolve"),
        None,
    )
    assert credit_resolve is not None
    assert credit_resolve.pinned_parent_runs.get("Movie") == "hash_movie_123"
    assert credit_resolve.pinned_parent_runs.get("Person") == "hash_person_456"


def test_b2_compile_hash_deterministic() -> None:
    """Same B2 inputs → identical compile hash."""
    wf1 = compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={"imdb_movies": "wm1"},
        scope="Movie",
    )
    wf2 = compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={"imdb_movies": "wm1"},
        scope="Movie",
    )
    assert compile_hash(wf1) == compile_hash(wf2)


def test_b2_compile_with_fake_impl() -> None:
    """Register a fake impl for Movie; impl_name appears on resolve stage."""
    binding = _fake_binding("Movie", "er_movie_fake")
    impl_configs = {
        "er_movie_fake": {"_revision": 42, "_impl_revision": 7, "threshold": 0.8},
    }

    wf = compile(
        spec=b2_spec,
        bound_impls=[binding],
        impl_configs=impl_configs,
        source_watermarks={},
        scope="Movie",
    )

    resolve_stage = next(
        (s for s in wf.stages if s.class_name == "Movie" and s.kind == "resolve"),
        None,
    )
    assert resolve_stage is not None
    assert resolve_stage.impl_name == "er_movie_fake"
    assert resolve_stage.impl_revision == 7
    assert resolve_stage.config_revision == 42


def test_b2_spec_revision_ids_present() -> None:
    """WorkflowSpec.spec_revision_ids keys include all compiled classes."""
    wf = compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="full",
    )
    for cls_name in ("Movie", "Person", "Credit"):
        assert cls_name in wf.spec_revision_ids


def test_b2_all_cache_keys_distinct_within_class() -> None:
    """Each stage for the same class has a distinct cache key (stage_kind differs)."""
    wf = compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="Movie",
    )
    keys = [s.cache_key for s in wf.stages if s.class_name == "Movie"]
    assert len(keys) == len(set(keys)), "Cache keys within a class must be distinct"
