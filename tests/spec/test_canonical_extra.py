"""Extra canonical-form + spec round-trip tests.

Tests cover:
  - spec_to_dict / spec_from_dict round-trip with object identity preserved
  - same-name-Slot collision: Movie.imdb_id vs Person.imdb_id as distinct objects
  - content_hash invariant across round-trip
"""

from __future__ import annotations

from knot.db.spec_store import spec_from_dict, spec_to_dict
from knot.spec import (
    OntologyClass,
    Slot,
    Source,
    Spec,
    TypeDefinition,
    compute_content_hash,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_two_class_spec() -> Spec:
    """Spec with Movie and Person classes sharing the slot name 'imdb_id'
    but as *distinct* Slot objects."""
    st = TypeDefinition(name="string", base="str")
    movie_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    person_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st, required=True)
    name = Slot(name="name", range=st, required=True)
    movie = OntologyClass(name="Movie", slots=[movie_id, title])
    person = OntologyClass(name="Person", slots=[person_id, name])
    movie_src = Source(name="imdb_movies", entity_class=movie, identifier_slot=movie_id)
    person_src = Source(name="imdb_people", entity_class=person, identifier_slot=person_id)
    return Spec(
        id="two-class",
        version="1.0.0",
        types=[st],
        slots=[movie_id, person_id, title, name],
        classes=[movie, person],
        sources=[movie_src, person_src],
    )


def _build_simple_spec() -> Spec:
    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id="simple",
        version="1.0.0",
        types=[st],
        slots=[imdb_id, title],
        classes=[movie],
        sources=[src],
    )


# ---------------------------------------------------------------------------
# 1. Round-trip: spec → dict → spec preserves field values
# ---------------------------------------------------------------------------


def test_round_trip_preserves_spec_id():
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    assert recovered.id == spec.id


def test_round_trip_preserves_spec_version():
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    assert recovered.version == spec.version


def test_round_trip_preserves_class_count():
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    assert len(recovered.classes) == len(spec.classes)


def test_round_trip_preserves_slot_count():
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    assert len(recovered.slots) == len(spec.slots)


def test_round_trip_preserves_source_count():
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    assert len(recovered.sources) == len(spec.sources)


def test_round_trip_preserves_class_names():
    spec = _build_two_class_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    orig_names = {c.name for c in spec.classes}
    rec_names = {c.name for c in recovered.classes}
    assert orig_names == rec_names


# ---------------------------------------------------------------------------
# 2. Object identity: source.entity_class is the same object as classes[n]
# ---------------------------------------------------------------------------


def test_round_trip_source_entity_class_identity():
    """After round-trip, source.entity_class must be the same Python object
    as the corresponding entry in spec.classes (not a copy)."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie_class = recovered.classes[0]
    movie_src = recovered.sources[0]
    assert movie_src.entity_class is movie_class


def test_round_trip_source_identifier_slot_identity():
    """After round-trip, source.identifier_slot must be the same Python object
    as the corresponding entry in spec.slots."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    imdb_slot = next(s for s in recovered.slots if s.name == "imdb_id")
    movie_src = recovered.sources[0]
    assert movie_src.identifier_slot is imdb_slot


def test_round_trip_slot_range_identity():
    """slot.range must be the same TypeDefinition object as spec.types[n]."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    string_type = recovered.types[0]
    for slot in recovered.slots:
        if slot.range is not None and isinstance(slot.range, TypeDefinition):
            assert slot.range is string_type


# ---------------------------------------------------------------------------
# 3. Same-name-Slot collision: Movie.imdb_id ≠ Person.imdb_id after round-trip
# ---------------------------------------------------------------------------


def test_same_name_slots_on_different_classes_are_distinct_objects():
    """Movie.imdb_id and Person.imdb_id share the name 'imdb_id' but must
    deserialize as distinct Python objects."""
    spec = _build_two_class_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie = next(c for c in recovered.classes if c.name == "Movie")
    person = next(c for c in recovered.classes if c.name == "Person")
    movie_imdb = next(s for s in movie.slots if s.name == "imdb_id")
    person_imdb = next(s for s in person.slots if s.name == "imdb_id")
    assert movie_imdb is not person_imdb


def test_same_name_slots_on_different_classes_preserved_in_spec_slots():
    """spec.slots should contain both imdb_id slot objects as separate entries."""
    spec = _build_two_class_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    imdb_slots = [s for s in recovered.slots if s.name == "imdb_id"]
    assert len(imdb_slots) == 2
    assert imdb_slots[0] is not imdb_slots[1]


def test_source_identifier_slot_identity_two_class_spec():
    """After round-trip, each source's identifier_slot must be the slot
    on its own class, not the other class's same-named slot."""
    spec = _build_two_class_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie = next(c for c in recovered.classes if c.name == "Movie")
    person = next(c for c in recovered.classes if c.name == "Person")
    movie_src = next(s for s in recovered.sources if s.name == "imdb_movies")
    person_src = next(s for s in recovered.sources if s.name == "imdb_people")
    movie_imdb = next(s for s in movie.slots if s.name == "imdb_id")
    person_imdb = next(s for s in person.slots if s.name == "imdb_id")
    assert movie_src.identifier_slot is movie_imdb
    assert person_src.identifier_slot is person_imdb


# ---------------------------------------------------------------------------
# 4. content_hash invariant across round-trip
# ---------------------------------------------------------------------------


def test_content_hash_invariant_across_round_trip():
    """compute_content_hash(spec) == compute_content_hash(spec_from_dict(spec_to_dict(spec)))."""
    spec = _build_simple_spec()
    original_hash = compute_content_hash(spec)
    recovered = spec_from_dict(spec_to_dict(spec))
    recovered_hash = compute_content_hash(recovered)
    assert original_hash == recovered_hash


def test_content_hash_invariant_two_class_spec():
    spec = _build_two_class_spec()
    original_hash = compute_content_hash(spec)
    recovered = spec_from_dict(spec_to_dict(spec))
    recovered_hash = compute_content_hash(recovered)
    assert original_hash == recovered_hash


def test_content_hash_changes_when_spec_changes():
    """Different spec content must produce a different hash."""
    spec_a = _build_simple_spec()
    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec_b = Spec(
        id="different_id",
        version="2.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[movie],
        sources=[src],
    )
    assert compute_content_hash(spec_a) != compute_content_hash(spec_b)
