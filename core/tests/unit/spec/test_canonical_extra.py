"""Extra canonical-form + spec round-trip tests.

Tests cover:
  - spec_to_dict / spec_from_dict round-trip with object identity preserved
  - same-name-Slot collision: Movie.imdb_id vs Person.imdb_id as distinct objects
  - content_hash invariant across round-trip

Slots are now inline on each OntologyClass; there is no top-level spec.slots list.
"""

from __future__ import annotations

from knot.spec import (
    OntologyClass,
    Primitive,
    Slot,
    Source,
    SourceBinding,
    Spec,
    compute_content_hash,
)
from knot.spec.serialization import spec_from_dict, spec_to_dict

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_two_class_spec() -> Spec:
    """Spec with Movie and Person classes sharing the slot name 'imdb_id'
    but as *distinct* Slot objects."""
    string = Primitive(name="string")
    movie_id = Slot(name="imdb_id", type=string, identifier=True, required=True)
    person_id = Slot(name="imdb_id", type=string, identifier=True, required=True)
    title = Slot(name="title", type=string, required=True)
    name = Slot(name="name", type=string, required=True)
    movie = OntologyClass(name="Movie", slots=[movie_id, title])
    person = OntologyClass(name="Person", slots=[person_id, name])
    movie_src = Source(name="imdb_movies")
    person_src = Source(name="imdb_people")
    movie_binding = SourceBinding(source=movie_src, class_=movie, identifier_slot=movie_id)
    person_binding = SourceBinding(source=person_src, class_=person, identifier_slot=person_id)
    return Spec(
        id="two-class",
        version="1.0.0",
        classes=[movie, person],
        sources=[movie_src, person_src],
        source_bindings=[movie_binding, person_binding],
    )


def _build_simple_spec() -> Spec:
    string = Primitive(name="string")
    imdb_id = Slot(name="imdb_id", type=string, identifier=True, required=True)
    title = Slot(name="title", type=string, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)
    return Spec(
        id="simple",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
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
    """Slots are inline on each class; total slot count matches sum over all classes."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    orig_count = sum(len(c.slots) for c in spec.classes)
    rec_count = sum(len(c.slots) for c in recovered.classes)
    assert rec_count == orig_count


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
# 2. Object identity: binding.class_ is the same object as classes[n]
# ---------------------------------------------------------------------------


def test_round_trip_binding_class_identity():
    """After round-trip, binding.class_ must be the same Python object
    as the corresponding entry in spec.classes (not a copy)."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie_class = recovered.classes[0]
    movie_binding = recovered.source_bindings[0]
    assert movie_binding.class_ is movie_class


def test_round_trip_binding_identifier_slot_identity():
    """After round-trip, binding.identifier_slot must be the same Python object
    as the corresponding slot on its class."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie_class = recovered.classes[0]
    imdb_slot = next(s for s in movie_class.slots if s.name == "imdb_id")
    movie_binding = recovered.source_bindings[0]
    assert movie_binding.identifier_slot is imdb_slot


def test_round_trip_slot_type_preserved():
    """After round-trip, slot.type is a Primitive with the correct name."""
    spec = _build_simple_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    for cls in recovered.classes:
        for slot in cls.slots:
            assert slot.type is not None
            assert isinstance(slot.type, Primitive)
            assert slot.type.name == "string"


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


def test_same_name_slots_on_different_classes_are_separate_across_classes():
    """Both Movie.imdb_id and Person.imdb_id exist on their respective classes."""
    spec = _build_two_class_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie = next(c for c in recovered.classes if c.name == "Movie")
    person = next(c for c in recovered.classes if c.name == "Person")
    movie_imdb_slots = [s for s in movie.slots if s.name == "imdb_id"]
    person_imdb_slots = [s for s in person.slots if s.name == "imdb_id"]
    assert len(movie_imdb_slots) == 1
    assert len(person_imdb_slots) == 1
    assert movie_imdb_slots[0] is not person_imdb_slots[0]


def test_source_identifier_slot_identity_two_class_spec():
    """After round-trip, each binding's identifier_slot must be the slot
    on its own class, not the other class's same-named slot."""
    spec = _build_two_class_spec()
    recovered = spec_from_dict(spec_to_dict(spec))
    movie = next(c for c in recovered.classes if c.name == "Movie")
    person = next(c for c in recovered.classes if c.name == "Person")
    movie_binding = next(b for b in recovered.source_bindings if b.source.name == "imdb_movies")
    person_binding = next(b for b in recovered.source_bindings if b.source.name == "imdb_people")
    movie_imdb = next(s for s in movie.slots if s.name == "imdb_id")
    person_imdb = next(s for s in person.slots if s.name == "imdb_id")
    assert movie_binding.identifier_slot is movie_imdb
    assert person_binding.identifier_slot is person_imdb


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
    string = Primitive(name="string")
    imdb_id = Slot(name="imdb_id", type=string, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)
    spec_b = Spec(
        id="different_id",
        version="2.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )
    assert compute_content_hash(spec_a) != compute_content_hash(spec_b)
