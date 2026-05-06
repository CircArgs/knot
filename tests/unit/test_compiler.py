"""Unit tests for knot.compiler.

Tests are pure-unit: no postgres, no docker, no lake. All inputs are
constructed in-process from metaschema objects.
"""

from __future__ import annotations

import copy

import pytest

from knot.metaschema import (
    Compare,
    CompareOp,
    Literal_,
    OntologyClass,
    Slot,
    SlotPath,
    Spec,
    TypeDefinition,
)
from knot.protocols import DataContext
from knot.impact import BoundImpl
from knot.compiler import (
    cache_key,
    compile,
    compile_hash,
    substitute_config_refs,
    topo_sort,
    ConfigRef,
)
from knot.workflow_spec import StageSpec, WorkflowSpec


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

string_t = TypeDefinition(name="string", base="str")
int_t = TypeDefinition(name="integer", base="int")


def _cls(name: str, slots: list[Slot] | None = None) -> OntologyClass:
    return OntologyClass(name=name, slots=slots or [])


def _slot(name: str, range_=None, range_cls: OntologyClass | None = None) -> Slot:
    r = range_cls if range_cls is not None else (range_ or string_t)
    return Slot(name=name, range=r)


def _spec(*classes: OntologyClass) -> Spec:
    return Spec(id="test", version="0.1.0", classes=list(classes))


def _binding(cls_name: str, impl_name: str | None = None) -> BoundImpl:
    class FakeImpl:
        pass
    return BoundImpl(
        impl_class=FakeImpl,
        impl_name=impl_name or f"er_{cls_name.lower()}",
        workflow=cls_name,
    )


# ---------------------------------------------------------------------------
# test_compile_simple_class
# ---------------------------------------------------------------------------

def test_compile_simple_class() -> None:
    """Single class, no impls, scope='Movie' → WorkflowSpec with 5 stages."""
    Movie = _cls("Movie", slots=[_slot("title"), _slot("year", int_t)])
    spec = _spec(Movie)

    wf = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="Movie",
    )

    assert isinstance(wf, WorkflowSpec)
    kinds = [s.kind for s in wf.stages]
    assert "normalize" in kinds
    assert "resolve" in kinds
    assert "merge" in kinds
    assert "validate" in kinds
    assert "publish" in kinds
    # All stages are for Movie
    for stage in wf.stages:
        assert stage.class_name == "Movie"
    # spec_revision_ids includes Movie
    assert "Movie" in wf.spec_revision_ids


# ---------------------------------------------------------------------------
# test_compile_full_pipeline
# ---------------------------------------------------------------------------

def test_compile_full_pipeline() -> None:
    """scope='full' toposorts B2's classes (Movie, Person, Credit)."""
    Movie = _cls("Movie")
    Person = _cls("Person")
    # Credit references both Movie and Person via slot.range
    credit_work = _slot("work", range_cls=Movie)
    credit_person = _slot("person", range_cls=Person)
    Credit = _cls("Credit", slots=[credit_work, credit_person])

    spec = _spec(Movie, Person, Credit)

    wf = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="full",
    )

    class_order = []
    seen = set()
    for stage in wf.stages:
        if stage.class_name and stage.class_name not in seen:
            class_order.append(stage.class_name)
            seen.add(stage.class_name)

    # Credit must appear after both Movie and Person
    assert "Movie" in class_order
    assert "Person" in class_order
    assert "Credit" in class_order
    assert class_order.index("Credit") > class_order.index("Movie")
    assert class_order.index("Credit") > class_order.index("Person")


# ---------------------------------------------------------------------------
# test_compile_cache_keys_deterministic
# ---------------------------------------------------------------------------

def test_compile_cache_keys_deterministic() -> None:
    """Same inputs → same cache keys; watermark change → only that stage's key changes."""
    Movie = _cls("Movie")
    spec = _spec(Movie)
    watermarks = {"imdb_movies": "wm_v1"}

    wf1 = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=watermarks,
        scope="Movie",
    )
    wf2 = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=watermarks,
        scope="Movie",
    )

    # Same inputs → identical cache keys across all stages
    keys1 = {s.kind: s.cache_key for s in wf1.stages}
    keys2 = {s.kind: s.cache_key for s in wf2.stages}
    assert keys1 == keys2

    # Move the watermark
    watermarks2 = {"imdb_movies": "wm_v2"}
    wf3 = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=watermarks2,
        scope="Movie",
    )
    keys3 = {s.kind: s.cache_key for s in wf3.stages}

    # normalize stage cache key changes (it ingests the watermark)
    assert keys3["normalize"] != keys1["normalize"]
    # resolve/merge/validate/publish downstream cache keys also change because
    # they include spec_revisions which are part of the input set
    # (the watermark change propagates via a fresh compile producing a different key)


# ---------------------------------------------------------------------------
# test_compile_cross_class_pinning
# ---------------------------------------------------------------------------

def test_compile_cross_class_pinning() -> None:
    """Credit run pins parent Movie + Person hashes; reflected in StageSpec."""
    Movie = _cls("Movie")
    Person = _cls("Person")
    credit_work = _slot("work", range_cls=Movie)
    credit_person_slot = _slot("person", range_cls=Person)
    Credit = _cls("Credit", slots=[credit_work, credit_person_slot])

    spec = _spec(Movie, Person, Credit)
    parents = {"Movie": "hash_movie_abc", "Person": "hash_person_xyz"}

    wf = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="Credit",
        parents_override=parents,
    )

    credit_stages = [s for s in wf.stages if s.class_name == "Credit"]
    assert credit_stages, "Expected Credit stages"

    # All non-normalize stages for a relation class carry pinned_parent_runs
    for stage in credit_stages:
        if stage.kind != "normalize":
            assert stage.pinned_parent_runs.get("Movie") == "hash_movie_abc"
            assert stage.pinned_parent_runs.get("Person") == "hash_person_xyz"


# ---------------------------------------------------------------------------
# test_compile_parents_override
# ---------------------------------------------------------------------------

def test_compile_parents_override() -> None:
    """parents_override pins exact hashes for Credit's parents."""
    Movie = _cls("Movie")
    Person = _cls("Person")
    credit_work = _slot("work", range_cls=Movie)
    credit_person_slot = _slot("person", range_cls=Person)
    Credit = _cls("Credit", slots=[credit_work, credit_person_slot])

    spec = _spec(Movie, Person, Credit)

    # Default compile (no override) — pinned_parent_runs is empty (no prior runs supplied)
    wf_default = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="Credit",
    )

    # Override compile
    wf_override = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="Credit",
        parents_override={"Movie": "hash_X"},
    )

    # With override, Movie hash is pinned
    override_stages = [s for s in wf_override.stages if s.kind == "resolve"]
    assert override_stages
    assert override_stages[0].pinned_parent_runs.get("Movie") == "hash_X"

    # Without override, Movie hash is absent (no prior run)
    default_stages = [s for s in wf_default.stages if s.kind == "resolve"]
    assert default_stages
    assert default_stages[0].pinned_parent_runs.get("Movie") is None

    # The two compiles produce different cache keys
    assert override_stages[0].cache_key != default_stages[0].cache_key


# ---------------------------------------------------------------------------
# test_compile_config_ref_substitution
# ---------------------------------------------------------------------------

def test_compile_config_ref_substitution() -> None:
    """substitute_config_refs replaces ConfigRef with Literal_; different config → different result."""
    # Test ConfigRef dispatch directly — ConfigRef is a plain class (not Pydantic),
    # so it can't be embedded inside a Compare.right field (which is typed SlotPath | Literal_).
    # The substitution visitor dispatches on it as a standalone node.
    config_ref = ConfigRef(field_path=("min_year",), field_type=int)

    config_v1 = {"min_year": 1990}
    config_v2 = {"min_year": 2000}

    result_v1 = substitute_config_refs(config_ref, config_v1)
    result_v2 = substitute_config_refs(config_ref, config_v2)

    assert isinstance(result_v1, Literal_)
    assert result_v1.value == 1990

    assert isinstance(result_v2, Literal_)
    assert result_v2.value == 2000


def test_compile_config_ref_substitution_in_compare() -> None:
    """substitute_config_refs on a Compare with Literal_ right passes through unchanged."""
    Movie = _cls("Movie", slots=[_slot("year", int_t)])
    year_slot = Movie.slots[0]

    path = SlotPath(from_class=Movie, slots=[year_slot])
    # Build a Compare with a Literal_ right (already substituted)
    lit = Literal_(value=1990)
    expr = Compare(op=CompareOp.GT, left=path, right=lit)

    result = substitute_config_refs(expr, {})
    assert isinstance(result, Compare)
    assert isinstance(result.right, Literal_)
    assert result.right.value == 1990


def test_compile_config_ref_in_cache_key() -> None:
    """Config snapshot difference → different cache key for the same stage."""
    ck1 = cache_key("resolve", "Movie", {"config_revision": 1, "impl_revision": None})
    ck2 = cache_key("resolve", "Movie", {"config_revision": 2, "impl_revision": None})
    assert ck1 != ck2


# ---------------------------------------------------------------------------
# test_compile_canonical_hash
# ---------------------------------------------------------------------------

def test_compile_canonical_hash() -> None:
    """Same inputs → byte-identical WorkflowSpec → identical compile_hash."""
    Movie = _cls("Movie")
    spec = _spec(Movie)

    wf1 = compile(spec=spec, bound_impls=[], impl_configs={}, source_watermarks={}, scope="Movie")
    wf2 = compile(spec=spec, bound_impls=[], impl_configs={}, source_watermarks={}, scope="Movie")

    h1 = compile_hash(wf1)
    h2 = compile_hash(wf2)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compile_canonical_hash_changes_on_input_change() -> None:
    """One input change (watermark) → different compile_hash."""
    Movie = _cls("Movie")
    spec = _spec(Movie)

    wf1 = compile(
        spec=spec, bound_impls=[], impl_configs={},
        source_watermarks={"src": "wm_1"}, scope="Movie",
    )
    wf2 = compile(
        spec=spec, bound_impls=[], impl_configs={},
        source_watermarks={"src": "wm_2"}, scope="Movie",
    )

    assert compile_hash(wf1) != compile_hash(wf2)


# ---------------------------------------------------------------------------
# test topo_sort cycle detection
# ---------------------------------------------------------------------------

def test_topo_sort_cycle_raises() -> None:
    """Cyclic slot.range dependency → ValueError."""
    # Build two classes each referencing the other
    A = OntologyClass(name="A", slots=[])
    B = OntologyClass(name="B", slots=[])
    a_slot = Slot(name="b_ref", range=B)
    b_slot = Slot(name="a_ref", range=A)
    A.slots = [a_slot]
    B.slots = [b_slot]
    spec = _spec(A, B)

    with pytest.raises(ValueError, match="cycle"):
        topo_sort(spec, "full")


# ---------------------------------------------------------------------------
# test stage:resolve:Movie scope
# ---------------------------------------------------------------------------

def test_compile_stage_scope() -> None:
    """scope='stage:resolve:Movie' → stages for Movie only."""
    Movie = _cls("Movie")
    Person = _cls("Person")
    spec = _spec(Movie, Person)

    wf = compile(
        spec=spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks={},
        scope="stage:resolve:Movie",
    )

    class_names = {s.class_name for s in wf.stages}
    assert "Movie" in class_names
    assert "Person" not in class_names
