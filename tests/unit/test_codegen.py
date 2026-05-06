"""Unit tests for knot.codegen.

Tests cover:
- RESOLVED lens: bare comparisons, between, within, relation descriptors
- DISAGREEMENT_AWARE lens: bare comparison absent; from_source/winner/all_ work
- Spec change: renamed slot causes AttributeError on old name
- Module-level __spec_classes__ and __spec_ref__ hooks
- write_sdk_module: file is writable and importable
- ER_DECISION_LENS and TARGET_DIRECT: comparisons work (opaque accessor)
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from knot.codegen import generate_sdk, write_sdk_module
from knot.metaschema import (
    Between,
    BoolExpr,
    BoolOp,
    Compare,
    CompareOp,
    FilteredRelation,
    Literal_,
    OntologyClass,
    RelationAggregate,
    RelationCount,
    Slot,
    SlotPath,
    Spec,
    TypeDefinition,
    Within,
)
from knot.protocols import ProtocolKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_sdk(spec: Spec, kind: ProtocolKind, tmp_path: Path) -> ModuleType:
    """Generate SDK, write to tmp_path, import and return module."""
    dst = tmp_path / f"ontology_{kind.value}.py"
    write_sdk_module(spec, kind, dst)
    module_name = f"_test_sdk_{kind.value}_{spec.id}"
    mod_spec = importlib.util.spec_from_file_location(module_name, dst)
    assert mod_spec is not None
    mod = importlib.util.module_from_spec(mod_spec)
    assert mod_spec.loader is not None
    mod_spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# RESOLVED lens — B2 spec
# ---------------------------------------------------------------------------

class TestResolvedLens:
    """Generate SDK for B2 spec with ProtocolKind.RESOLVED."""

    def test_bare_gt_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.year > 1900
        assert isinstance(result, Compare)
        assert result.op == CompareOp.GT
        assert isinstance(result.right, Literal_)
        assert result.right.value == 1900

    def test_bare_lt_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.year < 2020
        assert isinstance(result, Compare)
        assert result.op == CompareOp.LT

    def test_bare_eq_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.title == "Godfather"
        assert isinstance(result, Compare)
        assert result.op == CompareOp.EQ
        assert result.right.value == "Godfather"

    def test_between_returns_between(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.year.between(1990, 2000)
        assert isinstance(result, Between)
        assert result.lower.value == 1990
        assert result.upper.value == 2000
        assert result.inclusive is True

    def test_within_returns_within(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.genres.within(["Action", "Drama"])
        assert isinstance(result, Within)
        assert len(result.values) == 2

    def test_boolean_composition(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        expr = (mod.Movie.year > 1990) & (mod.Movie.year < 2010)
        assert isinstance(expr, BoolExpr)
        assert expr.op == BoolOp.AND
        assert len(expr.operands) == 2

    def test_relation_descriptor_where(self, tmp_path: Path) -> None:
        """Movie.director is a relation slot (range=Person); .where() returns FilteredRelation."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        # director has range=Person (OntologyClass), so it gets a relation descriptor
        # Use a Compare from the same module's Person.name
        name_compare = mod.Person.name == "Coppola"
        result = mod.Movie.director.where(name_compare)
        assert isinstance(result, FilteredRelation)

    def test_relation_descriptor_count(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.director.count()
        assert isinstance(result, RelationCount)

    def test_relation_descriptor_collect(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.director.collect()
        assert isinstance(result, RelationAggregate)

    def test_spec_classes_export(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        assert hasattr(mod, "__spec_classes__")
        assert set(mod.__spec_classes__.keys()) == {"Movie", "Person", "Credit"}
        assert mod.__spec_classes__["Movie"] is mod.Movie

    def test_spec_ref_classvar(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        assert mod.Movie.__spec_ref__ == "Movie"
        assert mod.Person.__spec_ref__ == "Person"

    def test_slot_ref_classvar_on_descriptor(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        # Access the descriptor class directly via type(Movie.year)
        desc = type(mod.Movie.year)
        assert desc.__slot_ref__ == ("Movie", "year")

    def test_from_source_accessor_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        result = mod.Movie.year.from_source("imdb") > 1900
        assert isinstance(result, Compare)
        assert result.op == CompareOp.GT


# ---------------------------------------------------------------------------
# DISAGREEMENT_AWARE lens — B2 spec
# ---------------------------------------------------------------------------

class TestDisagreementAwareLens:
    """Generate SDK for B2 spec with ProtocolKind.DISAGREEMENT_AWARE."""

    def test_bare_gt_raises_type_error(self, tmp_path: Path) -> None:
        """Movie.year > 1900 must raise TypeError — no __gt__ on MultiValued."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        with pytest.raises(TypeError):
            _ = mod.Movie.year > 1900

    def test_bare_lt_raises_type_error(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        with pytest.raises(TypeError):
            _ = mod.Movie.year < 2020

    def test_from_source_gt_returns_compare(self, tmp_path: Path) -> None:
        """Movie.year.from_source(imdb) > 1900 must work."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        result = mod.Movie.year.from_source("imdb") > 1900
        assert isinstance(result, Compare)
        assert result.op == CompareOp.GT
        assert result.right.value == 1900

    def test_winner_gt_returns_compare(self, tmp_path: Path) -> None:
        """Movie.year.winner() > 1900 is the explicit opt-in to RESOLVED behavior."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        result = mod.Movie.year.winner() > 1900
        assert isinstance(result, Compare)

    def test_all__gt_returns_compare(self, tmp_path: Path) -> None:
        """Movie.year.all_() > 1900 — forall — should return a Compare."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        result = mod.Movie.year.all_() > 1900
        assert isinstance(result, Compare)

    def test_any__gt_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        result = mod.Movie.year.any_() > 1900
        assert isinstance(result, Compare)

    def test_contributions_returns_accessor(self, tmp_path: Path) -> None:
        """Movie.year.contributions() returns an accessor (not a Compare)."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        acc = mod.Movie.year.contributions()
        # contributions() returns a _ResolvedAccessor, not a Compare
        assert hasattr(acc, "_reduction")
        assert acc._reduction == "contributions"

    def test_between_works(self, tmp_path: Path) -> None:
        """between() is available on MultiValued descriptors."""
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        result = mod.Movie.year.between(1990, 2000)
        assert isinstance(result, Between)

    def test_spec_classes_export(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE, tmp_path)
        assert set(mod.__spec_classes__.keys()) == {"Movie", "Person", "Credit"}


# ---------------------------------------------------------------------------
# Spec change invalidates old slot name
# ---------------------------------------------------------------------------

class TestSpecChangeInvalidation:
    """Re-generating after a slot rename causes AttributeError for old name."""

    def test_renamed_slot_raises_attribute_error(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        import copy

        # Build a modified spec with 'year' renamed to 'release_year'
        orig_movie = next(c for c in b2.spec.classes if c.name == "Movie")
        new_year_slot = Slot(
            name="release_year",
            range=next(t for t in b2.spec.types if t.name == "integer"),
        )
        new_movie = OntologyClass(
            name="Movie",
            slots=[
                s if s.name != "year" else new_year_slot
                for s in orig_movie.slots
            ],
        )
        new_classes = [
            new_movie if c.name == "Movie" else c
            for c in b2.spec.classes
        ]
        new_spec = Spec(
            id="b2_modified",
            version="0.2.0",
            classes=new_classes,
            slots=b2.spec.slots,
            types=b2.spec.types,
        )

        mod = _load_sdk(new_spec, ProtocolKind.RESOLVED, tmp_path)

        # New name works
        result = mod.Movie.release_year > 2000
        assert isinstance(result, Compare)

        # Old name raises AttributeError
        with pytest.raises(AttributeError):
            _ = mod.Movie.year


# ---------------------------------------------------------------------------
# ER_DECISION_LENS and TARGET_DIRECT
# ---------------------------------------------------------------------------

class TestErDecisionLens:
    """ER_DECISION_LENS: comparisons available (opaque accessor shape)."""

    def test_bare_gt_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.ER_DECISION_LENS, tmp_path)
        result = mod.Movie.year > 1990
        assert isinstance(result, Compare)

    def test_spec_classes_export(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.ER_DECISION_LENS, tmp_path)
        assert "Movie" in mod.__spec_classes__


class TestTargetDirectLens:
    """TARGET_DIRECT: comparisons available."""

    def test_bare_gt_returns_compare(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.TARGET_DIRECT, tmp_path)
        result = mod.Movie.year > 1990
        assert isinstance(result, Compare)


# ---------------------------------------------------------------------------
# generate_sdk: source code properties
# ---------------------------------------------------------------------------

class TestGenerateSdkSource:
    """generate_sdk() produces correct source strings."""

    def test_header_comment_contains_spec_id(self) -> None:
        from tests.fixtures.B2 import spec as b2
        src = generate_sdk(b2.spec, ProtocolKind.RESOLVED)
        assert "b2_integration" in src

    def test_header_contains_protocol(self) -> None:
        from tests.fixtures.B2 import spec as b2
        src = generate_sdk(b2.spec, ProtocolKind.RESOLVED)
        assert "resolved" in src

    def test_disagreement_aware_has_no_gt_method(self) -> None:
        from tests.fixtures.B2 import spec as b2
        src = generate_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE)
        # The scalar descriptor for year should NOT contain __gt__ directly
        # (only from_source/winner/all_ wrappers provide it)
        # Find the _Movie_year_Descriptor block and check no __gt__ before from_source
        year_block_start = src.index("class _Movie_year_Descriptor")
        # next class starts at next "class _Movie_" or "class _Person_"
        next_class = src.find("\nclass _", year_block_start + 1)
        year_block = src[year_block_start:next_class] if next_class != -1 else src[year_block_start:]
        assert "__gt__" not in year_block

    def test_resolved_has_gt_method(self) -> None:
        from tests.fixtures.B2 import spec as b2
        src = generate_sdk(b2.spec, ProtocolKind.RESOLVED)
        year_block_start = src.index("class _Movie_year_Descriptor")
        next_class = src.find("\nclass _", year_block_start + 1)
        year_block = src[year_block_start:next_class] if next_class != -1 else src[year_block_start:]
        assert "__gt__" in year_block

    def test_source_compiles_resolved(self) -> None:
        from tests.fixtures.B2 import spec as b2
        src = generate_sdk(b2.spec, ProtocolKind.RESOLVED)
        compile(src, "<generated_resolved>", "exec")  # must not raise

    def test_source_compiles_disagreement_aware(self) -> None:
        from tests.fixtures.B2 import spec as b2
        src = generate_sdk(b2.spec, ProtocolKind.DISAGREEMENT_AWARE)
        compile(src, "<generated_mv>", "exec")  # must not raise

    def test_source_under_500_lines_resolved(self) -> None:
        """Codegen output stays human-scale; codegen.py itself <= 500 lines."""
        from tests.fixtures.B2 import spec as b2
        codegen_path = Path(__file__).parent.parent.parent / "src" / "knot" / "codegen.py"
        lines = codegen_path.read_text().splitlines()
        assert len(lines) <= 500, f"codegen.py has {len(lines)} lines (limit 500)"


# ---------------------------------------------------------------------------
# write_sdk_module: file written and importable
# ---------------------------------------------------------------------------

class TestWriteSdkModule:
    def test_file_is_created(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        dst = tmp_path / "sub" / "ontology.py"
        write_sdk_module(b2.spec, ProtocolKind.RESOLVED, dst)
        assert dst.exists()

    def test_file_is_importable(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        mod = _load_sdk(b2.spec, ProtocolKind.RESOLVED, tmp_path)
        assert hasattr(mod, "Movie")
        assert hasattr(mod, "Person")
        assert hasattr(mod, "Credit")

    def test_overwrite_is_idempotent(self, tmp_path: Path) -> None:
        from tests.fixtures.B2 import spec as b2
        dst = tmp_path / "ontology.py"
        write_sdk_module(b2.spec, ProtocolKind.RESOLVED, dst)
        content1 = dst.read_text()
        write_sdk_module(b2.spec, ProtocolKind.RESOLVED, dst)
        content2 = dst.read_text()
        assert content1 == content2
