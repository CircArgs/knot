"""Builder + entity-local + cross-entity validation for knot.spec."""

import pytest

from knot import (
    Array,
    ClassKind,
    ClassRef,
    Constraint,
    OntologyClass,
    Primitive,
    Severity,
    Slot,
    Source,
    SourceBinding,
    Spec,
    SpecError,
    VirtualClass,
)


# ---------------------------------------------------------------------------
# Entity-local validation (__post_init__)
# ---------------------------------------------------------------------------


def test_slot_rejects_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        Slot(name="", type=Primitive.TEXT)


def test_slot_rejects_bad_name_shape():
    with pytest.raises(ValueError, match="must match"):
        Slot(name="123bad", type=Primitive.TEXT)


def test_slot_identifier_promotes_required():
    s = Slot(name="canonical_id", type=Primitive.TEXT, identifier=True)
    assert s.required is True  # silently promoted


def test_ontology_class_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        OntologyClass(name="Movie", kind="nope")


def test_ontology_class_string_kind_coerces_to_enum():
    cls = OntologyClass(name="Movie", kind="abstract")
    assert cls.kind is ClassKind.ABSTRACT


def test_constraint_rejects_empty_body():
    cls = OntologyClass(name="Movie")
    with pytest.raises(ValueError, match="non-empty"):
        Constraint(name="c", primary=cls, body="")


def test_constraint_rejects_bad_severity():
    cls = OntologyClass(name="Movie")
    with pytest.raises(ValueError, match="severity"):
        Constraint(name="c", primary=cls, body="x", severity="loud")


def test_constraint_severity_default_is_enum():
    cls = OntologyClass(name="Movie")
    c = Constraint(name="c", primary=cls, body="x")
    assert c.severity is Severity.ERROR


def test_virtual_class_rejects_empty_definition():
    parent = OntologyClass(name="Movie")
    with pytest.raises(ValueError, match="non-empty"):
        VirtualClass(name="DirectedMovie", is_a=parent, definition="")


def test_source_binding_rejects_accuracy_out_of_range():
    s = Source(name="imdb")
    cls = OntologyClass(name="Movie")
    cls.slot("canonical_id", Primitive.TEXT, identifier=True)
    with pytest.raises(ValueError, match="accuracy"):
        SourceBinding(
            source=s, class_=cls, identifier_slot=cls["canonical_id"], accuracy=1.5
        )


def test_spec_id_must_be_non_empty():
    with pytest.raises(ValueError):
        Spec(id="", version="0.1")


def test_spec_version_must_be_non_empty():
    with pytest.raises(ValueError):
        Spec(id="m", version="")


# ---------------------------------------------------------------------------
# Builder-time uniqueness
# ---------------------------------------------------------------------------


def test_duplicate_class_name_rejected():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie")
    with pytest.raises(ValueError, match="already has a class"):
        spec.add_class("Movie")


def test_duplicate_slot_name_within_class_rejected():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("year", Primitive.INTEGER)
    with pytest.raises(ValueError, match="already has a slot"):
        movie.slot("year", Primitive.TEXT)


def test_duplicate_source_name_rejected():
    spec = Spec(id="m", version="0.1")
    spec.add_source("imdb")
    with pytest.raises(ValueError, match="already has a source"):
        spec.add_source("imdb")


def test_duplicate_constraint_name_rejected(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    with pytest.raises(ValueError, match="already has a constraint"):
        movie_spec.add_constraint("year_sane", primary=movie, body="year > 0")


def test_duplicate_binding_pair_rejected(movie_spec):
    imdb = movie_spec.sources[0]
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    with pytest.raises(ValueError, match="already has a binding"):
        movie_spec.bind(imdb, movie, identifier=movie["canonical_id"])


# ---------------------------------------------------------------------------
# Inheritance + lookup
# ---------------------------------------------------------------------------


def test_chain_walks_is_a(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    assert [c.name for c in movie.chain()] == ["Movie", "Title"]


def test_get_slot_walks_inheritance(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    # 'name' is on Title, not Movie's own slots
    assert movie["name"].type is Primitive.TEXT
    assert movie["name"].required is True


def test_identifier_slot_walks_inheritance(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    # canonical_id is on Title (abstract); Movie inherits it
    assert movie.identifier_slot().name == "canonical_id"


def test_effective_slots_dedupe_first_seen():
    spec = Spec(id="m", version="0.1")
    parent = spec.add_class("Parent")
    parent.slot("id", Primitive.TEXT, identifier=True)
    parent.slot("name", Primitive.TEXT)
    child = spec.add_class("Child", is_a=parent)
    child.slot("name", Primitive.INTEGER)  # shadows parent.name
    names = [s.name for s in child.effective_slots()]
    assert names == ["name", "id"]  # child's own first, then parent's id
    # child's `name` wins (Primitive.INTEGER, not parent's TEXT)
    name_slot = next(s for s in child.effective_slots() if s.name == "name")
    assert name_slot.type is Primitive.INTEGER


def test_fk_detection():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit.fk("movie", to=movie)
    assert credit["movie"].is_fk is True
    assert isinstance(credit["movie"].type, ClassRef)
    assert credit["movie"].type.target is movie


# ---------------------------------------------------------------------------
# Cross-entity Spec.validate()
# ---------------------------------------------------------------------------


def test_validate_clean_spec(movie_spec):
    assert movie_spec.validate() == []


def test_validate_strict_no_raise(movie_spec):
    movie_spec.validate_strict()  # should not raise


def test_validate_orphan_constraint_primary():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie").slot("canonical_id", Primitive.TEXT, identifier=True)
    ghost = OntologyClass(name="Ghost")
    spec.add_constraint("c", primary=ghost, body="x = 1")
    errs = spec.validate()
    assert any("Ghost" in e for e in errs)


def test_validate_concrete_missing_identifier():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie").slot("name", Primitive.TEXT)  # no identifier
    errs = spec.validate()
    assert any("no identifier" in e for e in errs)


def test_validate_concrete_multiple_identifiers():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("a", Primitive.TEXT, identifier=True)
    movie.slot("b", Primitive.TEXT, identifier=True)
    errs = spec.validate()
    assert any("multiple" in e for e in errs)


def test_validate_classref_target_missing():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    ghost = OntologyClass(name="Ghost")
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit.fk("movie", to=ghost)  # Ghost is NOT in spec.classes
    errs = spec.validate()
    assert any("ClassRef" in e and "Ghost" in e for e in errs)


def test_validate_binding_to_abstract():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", Primitive.TEXT, identifier=True)
    imdb = spec.add_source("imdb")
    spec.bind(imdb, title, identifier=title["canonical_id"])
    errs = spec.validate()
    assert any("abstract" in e for e in errs)


def test_validate_mapping_slot_not_on_class(movie_spec):
    movie_spec.source_bindings[0].mappings["nonexistent_slot"] = "raw_field"
    errs = movie_spec.validate()
    assert any("nonexistent_slot" in e for e in errs)


def test_validate_virtual_class_is_a_missing():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    # virtual references a class that's NOT in spec
    ghost = OntologyClass(name="Ghost")
    ghost.slot("canonical_id", Primitive.TEXT, identifier=True)
    spec.add_virtual_class("Variant", base=ghost, where="x = 1")
    errs = spec.validate()
    assert any("virtual" in e and "Ghost" in e for e in errs)


def test_validate_strict_raises_with_all_errors():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie").slot("name", Primitive.TEXT)
    spec.add_constraint("c", primary=OntologyClass(name="Ghost"), body="x")
    with pytest.raises(SpecError) as ei:
        spec.validate_strict()
    msg = str(ei.value)
    assert "no identifier" in msg
    assert "Ghost" in msg
