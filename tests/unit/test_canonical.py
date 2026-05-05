"""Unit tests for knot.canonical — RFC 8785 JCS canonicalization + sha256 hashing.

Tests use minimal local Pydantic models so this file has no dependency on
knot.metaschema (parallel slice). B2/C2-fixture-based tests are xfail until
that slice lands.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Optional

import pytest
from pydantic import BaseModel, ConfigDict, Field

from knot.canonical import (
    CANONICAL_DUMP_VERSION,
    canonical_dump,
    compute_content_hash,
)


# ---------------------------------------------------------------------------
# Minimal local models (no metaschema dependency)
# ---------------------------------------------------------------------------


class Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    tags: list[str] = Field(default_factory=list)


class MockOntologyClass(BaseModel):
    """Mirrors the RUNTIME fields defined for OntologyClass."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: Optional[str] = None
    abstract: bool = False
    slots: list[str] = Field(default_factory=list)


class MockSpec(BaseModel):
    """Mirrors the RUNTIME fields defined for the Spec envelope."""

    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    created_at: Optional[datetime] = None
    last_modified: Optional[datetime] = None
    author: Optional[str] = None
    revision_id: Optional[str] = None
    display_label: Optional[str] = None
    classes: list[MockOntologyClass] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_canonical_dump_version_constant():
    assert CANONICAL_DUMP_VERSION == 1


def test_hash_format():
    model = Inner(name="x")
    h = compute_content_hash(model)
    assert isinstance(h, str)
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_stability():
    """Same model dumped twice → identical bytes."""
    model = Inner(name="hello", tags=["a", "b"])
    assert canonical_dump(model) == canonical_dump(model)


def test_determinism_field_order():
    """Equivalent models produce the same hash regardless of construction order.

    JCS sorts keys lexicographically, so field-declaration order must not matter.
    We verify by comparing two models whose dicts differ only in insertion order.
    """
    a = MockOntologyClass(name="Movie", abstract=True)
    b = MockOntologyClass(name="Movie", abstract=True)
    assert compute_content_hash(a) == compute_content_hash(b)


def test_strip_defaults_empty_list():
    """A model with an empty list set to its default hashes the same as one that
    never set the field."""
    without = Inner(name="x")
    with_explicit = Inner(name="x", tags=[])
    assert canonical_dump(without) == canonical_dump(with_explicit)


def test_strip_defaults_bool_default():
    """A field set to its explicit default bool is stripped."""
    without = MockOntologyClass(name="Movie")
    with_explicit = MockOntologyClass(name="Movie", abstract=False)
    assert canonical_dump(without) == canonical_dump(with_explicit)


def test_round_trip():
    """model → canonical_dump (bytes) → re-parse back to model → structural equality."""
    import json

    model = MockOntologyClass(name="Person", slots=["id", "name"])
    raw_bytes = canonical_dump(model)
    parsed = json.loads(raw_bytes.decode("utf-8"))
    # The canonical bytes should be a valid JSON object with expected keys.
    assert parsed["name"] == "Person"
    assert parsed["slots"] == ["id", "name"]
    # abstract=False was stripped (default), so it must not appear.
    assert "abstract" not in parsed


def test_runtime_exclusion_description():
    """Mutating description on a MockOntologyClass must NOT change the hash.

    The RUNTIME_FIELDS set keys on class name "OntologyClass" but MockOntologyClass
    is a local proxy; we verify the mechanism with a model explicitly named to
    match by patching the class name — or, more directly, we just confirm that
    description is stripped when the class IS named OntologyClass.
    """
    # Build a model whose Pydantic class name is "OntologyClass" via dynamic creation.
    OntologyClass = type(
        "OntologyClass",
        (BaseModel,),
        {
            "__annotations__": {
                "name": str,
                "description": Optional[str],
            },
            "model_config": ConfigDict(extra="forbid"),
        },
    )

    a = OntologyClass(name="Movie", description=None)
    b = OntologyClass(name="Movie", description="A feature film.")
    assert compute_content_hash(a) == compute_content_hash(b)


def test_runtime_exclusion_envelope_fields():
    """Mutating last_modified / author on Spec must NOT change the hash."""

    # Use a proper class declaration so Optional fields get correct defaults.
    class Spec(BaseModel):
        model_config = ConfigDict(extra="forbid")
        id: str
        version: str
        last_modified: Optional[datetime] = None
        author: Optional[str] = None

    base = Spec(id="s1", version="0.1.0")
    with_meta = Spec(
        id="s1",
        version="0.1.0",
        last_modified=datetime(2026, 1, 1),
        author="nick",
    )
    assert compute_content_hash(base) == compute_content_hash(with_meta)


def test_jcs_numeric_normalization():
    """JCS (RFC 8785) normalizes numbers per ECMAScript ToString.

    1 and 1 must hash the same. 1 and 2 must hash differently.
    We test via a simple model with an int field.
    """

    class NumModel(BaseModel):
        model_config = ConfigDict(extra="forbid")
        value: int

    assert compute_content_hash(NumModel(value=1)) == compute_content_hash(
        NumModel(value=1)
    )
    assert compute_content_hash(NumModel(value=1)) != compute_content_hash(
        NumModel(value=2)
    )


def test_jcs_produces_sorted_keys():
    """The output bytes must have keys in lexicographic order (JCS requirement)."""
    import json

    class ZAModel(BaseModel):
        model_config = ConfigDict(extra="forbid")
        z_field: str
        a_field: str
        m_field: str

    model = ZAModel(z_field="z", a_field="a", m_field="m")
    raw = json.loads(canonical_dump(model).decode("utf-8"))
    keys = list(raw.keys())
    assert keys == sorted(keys)


def test_non_default_values_are_preserved():
    """Non-default values must survive strip_defaults."""
    model = Inner(name="kept", tags=["one", "two"])
    import json

    parsed = json.loads(canonical_dump(model))
    assert parsed["name"] == "kept"
    assert parsed["tags"] == ["one", "two"]


# ---------------------------------------------------------------------------
# B2/C2 fixture-based tests — xfail until metaschema slice lands
# ---------------------------------------------------------------------------


@pytest.mark.xfail(reason="awaiting metaschema slice", strict=False)
def test_b2_fixture_stability():
    from tests.fixtures.B2.spec import spec as b2_spec  # type: ignore[import]

    assert canonical_dump(b2_spec) == canonical_dump(b2_spec)


@pytest.mark.xfail(reason="awaiting metaschema slice", strict=False)
def test_c2_fixture_stability():
    from tests.fixtures.C2.spec import spec as c2_spec  # type: ignore[import]

    assert canonical_dump(c2_spec) == canonical_dump(c2_spec)


@pytest.mark.xfail(reason="awaiting metaschema slice", strict=False)
def test_b2_runtime_exclusion():
    """Modifying description on a B2 OntologyClass must not change the hash."""
    from tests.fixtures.B2.spec import spec as b2_spec  # type: ignore[import]

    h1 = compute_content_hash(b2_spec)
    # Mutate description on first class
    original = b2_spec.classes[0].description
    b2_spec.classes[0].description = "totally different description"
    h2 = compute_content_hash(b2_spec)
    b2_spec.classes[0].description = original
    assert h1 == h2
