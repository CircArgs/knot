"""Builder + entity-local + cross-entity validation for knot.spec."""

import pytest

from knot import (
    ClassKind,
    Constraint,
    OntologyClass,
    Severity,
    Slot,
    Source,
    SourceBinding,
    Spec,
    SpecError,
    VirtualClass,
    raw,
    types,
)

# ---------------------------------------------------------------------------
# Entity-local validation (__post_init__)
# ---------------------------------------------------------------------------


def test_slot_rejects_empty_name():
    with pytest.raises(ValueError, match="non-empty"):
        Slot(name="", type=types.TEXT)


def test_slot_rejects_bad_name_shape():
    with pytest.raises(ValueError, match="must match"):
        Slot(name="123bad", type=types.TEXT)


def test_slot_identifier_promotes_required():
    s = Slot(name="canonical_id", type=types.TEXT, identifier=True)
    assert s.required is True  # silently promoted


def test_ontology_class_rejects_bad_kind():
    with pytest.raises(ValueError, match="kind"):
        OntologyClass(name="Movie", kind="nope")


def test_ontology_class_string_kind_coerces_to_enum():
    cls = OntologyClass(name="Movie", kind="abstract")
    assert cls.kind is ClassKind.ABSTRACT


def test_constraint_rejects_non_expr_body():
    cls = OntologyClass(name="Movie")
    with pytest.raises(TypeError, match="must be an Expr"):
        Constraint(name="c", primary=cls, body="year >= 1888")


def test_constraint_rejects_bad_severity():
    cls = OntologyClass(name="Movie")
    cls.slot("canonical_id", types.TEXT, identifier=True)
    cls.slot("year", types.INTEGER)
    with pytest.raises(ValueError, match="severity"):
        Constraint(
            name="c",
            primary=cls,
            body=cls.col.year >= 1888,
            severity="loud",
        )


def test_constraint_severity_default_is_enum():
    cls = OntologyClass(name="Movie")
    cls.slot("canonical_id", types.TEXT, identifier=True)
    cls.slot("year", types.INTEGER)
    c = Constraint(name="c", primary=cls, body=cls.col.year >= 1888)
    assert c.severity is Severity.ERROR


def test_virtual_class_rejects_non_expr_definition():
    parent = OntologyClass(name="Movie")
    with pytest.raises(TypeError, match="must be an Expr"):
        VirtualClass(name="DirectedMovie", is_a=parent, definition="raw sql string")


def test_source_binding_rejects_default_trust_out_of_range():
    s = Source(name="imdb")
    cls = OntologyClass(name="Movie")
    cls.slot("canonical_id", types.TEXT, identifier=True)
    with pytest.raises(ValueError, match="default_trust"):
        SourceBinding(source=s, class_=cls, default_trust=1.5)


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
    movie.slot("year", types.INTEGER)
    with pytest.raises(ValueError, match="already has a slot"):
        movie.slot("year", types.TEXT)


def test_duplicate_source_name_rejected():
    spec = Spec(id="m", version="0.1")
    spec.add_source("imdb")
    with pytest.raises(ValueError, match="already has a source"):
        spec.add_source("imdb")


def test_duplicate_constraint_name_rejected(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    with pytest.raises(ValueError, match="already has a constraint"):
        movie.add_constraint("year_sane", body=movie.col.year > 0)


def test_duplicate_binding_pair_rejected(movie_spec):
    imdb = movie_spec.sources[0]
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    with pytest.raises(ValueError, match="already has a binding"):
        imdb.bind(movie)


# ---------------------------------------------------------------------------
# Inheritance + lookup
# ---------------------------------------------------------------------------


def test_chain_walks_is_a(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    assert [c.name for c in movie.chain()] == ["Movie", "Title"]


def test_get_slot_walks_inheritance(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    # 'name' is on Title, not Movie's own slots
    assert movie["name"].type is types.TEXT
    assert movie["name"].required is True


def test_identifier_slot_walks_inheritance(movie_spec):
    movie = next(c for c in movie_spec.classes if c.name == "Movie")
    # canonical_id is on Title (abstract); Movie inherits it
    assert movie.identifier_slot().name == "canonical_id"


def test_effective_slots_dedupe_first_seen():
    spec = Spec(id="m", version="0.1")
    parent = spec.add_class("Parent")
    parent.slot("id", types.TEXT, identifier=True)
    parent.slot("name", types.TEXT)
    child = spec.add_class("Child", is_a=parent)
    child.slot("name", types.INTEGER)  # shadows parent.name
    names = [s.name for s in child.effective_slots()]
    assert names == ["name", "id"]  # child's own first, then parent's id
    # child's `name` wins (types.INTEGER, not parent's TEXT)
    name_slot = next(s for s in child.effective_slots() if s.name == "name")
    assert name_slot.type is types.INTEGER


def test_fk_detection():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("movie", movie)
    assert credit["movie"].is_fk is True
    from knot.ast.types import ClassRef

    assert isinstance(credit["movie"].type, ClassRef)
    assert credit["movie"].type.target is movie


# ---------------------------------------------------------------------------
# Cross-entity Spec.validate()
# ---------------------------------------------------------------------------


def test_validate_clean_spec(movie_spec):
    movie_spec.validate()  # raises if invalid


def test_validate_strict_no_raise(movie_spec):
    movie_spec.validate()  # should not raise


def test_validate_orphan_constraint_primary():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie").slot("canonical_id", types.TEXT, identifier=True)
    ghost = OntologyClass(name="Ghost")
    # Construct an orphan-primary constraint directly (bypassing the
    # builder method, which would refuse) so the validator sees the
    # invalid state.
    spec.constraints.append(Constraint(name="c", primary=ghost, body=raw("1 = 1")))
    errs = spec._validation_errors()
    assert any("Ghost" in e for e in errs)


def test_validate_concrete_missing_identifier():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie").slot("name", types.TEXT)  # no identifier
    errs = spec._validation_errors()
    assert any("no identifier" in e for e in errs)


def test_validate_concrete_multiple_identifiers():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("a", types.TEXT, identifier=True)
    movie.slot("b", types.TEXT, identifier=True)
    errs = spec._validation_errors()
    assert any("multiple" in e for e in errs)


def test_validate_classref_target_missing():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    ghost = OntologyClass(name="Ghost")
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("movie", ghost)  # Ghost is NOT in spec.classes
    errs = spec._validation_errors()
    assert any("ClassRef" in e and "Ghost" in e for e in errs)


def test_validate_binding_to_abstract():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    imdb = spec.add_source("imdb")
    imdb.bind(title)
    errs = spec._validation_errors()
    assert any("abstract" in e for e in errs)


def test_validate_mapping_slot_not_on_class(movie_spec):
    from knot import SlotMapping

    movie_spec.source_bindings[0].slot_mappings["nonexistent_slot"] = SlotMapping(
        class_slot="nonexistent_slot", source_slot=("raw_field",)
    )
    errs = movie_spec._validation_errors()
    assert any("nonexistent_slot" in e for e in errs)


def test_validate_virtual_class_is_a_missing():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    # virtual references a class that's NOT in spec
    ghost = OntologyClass(name="Ghost")
    ghost.slot("canonical_id", types.TEXT, identifier=True)
    spec.classes.append(
        VirtualClass(name="Variant", is_a=ghost, definition=raw("1 = 1"))
    )
    errs = spec._validation_errors()
    assert any("virtual" in e and "Ghost" in e for e in errs)


def test_validate_strict_raises_with_all_errors():
    spec = Spec(id="m", version="0.1")
    spec.add_class("Movie").slot("name", types.TEXT)
    spec.constraints.append(
        Constraint(name="c", primary=OntologyClass(name="Ghost"), body=raw("1 = 1"))
    )
    with pytest.raises(SpecError) as ei:
        spec.validate()
    msg = str(ei.value)
    assert "no identifier" in msg
    assert "Ghost" in msg


# ---------------------------------------------------------------------------
# Cycle detection on is_a / mixins
# ---------------------------------------------------------------------------


def test_validate_self_is_a_cycle():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.is_a = movie  # direct self-reference
    errs = spec._validation_errors()
    assert any("cycle" in e for e in errs)


def test_validate_mutual_is_a_cycle():
    spec = Spec(id="m", version="0.1")
    a = spec.add_class("A")
    a.slot("canonical_id", types.TEXT, identifier=True)
    b = spec.add_class("B")
    b.slot("canonical_id", types.TEXT, identifier=True)
    a.is_a = b
    b.is_a = a
    errs = spec._validation_errors()
    assert sum(1 for e in errs if "cycle" in e) == 2


def test_validate_mixin_cycle():
    spec = Spec(id="m", version="0.1")
    a = spec.add_class("A")
    a.slot("canonical_id", types.TEXT, identifier=True)
    b = spec.add_class("B")
    b.slot("canonical_id", types.TEXT, identifier=True)
    a.mixins.append(b)
    b.mixins.append(a)
    errs = spec._validation_errors()
    assert any("cycle" in e for e in errs)


def test_validate_no_false_positive_for_chain(movie_spec):
    # Title <- Movie is a legit linear chain — should NOT be flagged
    errs = movie_spec._validation_errors()
    assert not any("cycle" in e for e in errs)


# ---------------------------------------------------------------------------
# Construction-time slot-ref validation (catches typos before validate())
# ---------------------------------------------------------------------------


def test_col_access_rejects_unknown_slot_at_construction():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    # Builder catches the typo at the point of construction — no
    # validation pass needed, no SQL parsing involved.
    with pytest.raises(KeyError, match="nonexistent_slot"):
        movie.col.nonexistent_slot  # noqa: B018 — attribute access triggers the validation


def test_col_access_resolves_inherited_slot():
    spec = Spec(id="m", version="0.1")
    title = spec.add_class("Title", kind="abstract")
    title.slot("canonical_id", types.TEXT, identifier=True)
    title.slot("name", types.TEXT, required=True)
    movie = spec.add_class("Movie", is_a=title)
    # `name` is inherited from Title — resolves via effective_slots.
    ref = movie.col.name
    assert ref.class_name == "Movie"
    assert ref.slot_name == "name"


def test_has_any_unknown_slot_kwarg_raises():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("movie", movie)
    # `role` doesn't exist on Credit — caught at expression build.
    with pytest.raises(KeyError, match="role"):
        movie.has_any(credit, role="director")


def test_has_any_no_fk_back_raises():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    other = spec.add_class("Other")
    other.slot("canonical_id", types.TEXT, identifier=True)
    # Other has no FK back to Movie.
    with pytest.raises(ValueError, match="no FK back"):
        movie.has_any(other)


def test_has_any_ambiguous_fk_requires_via():
    spec = Spec(id="m", version="0.1")
    person = spec.add_class("Person")
    person.slot("canonical_id", types.TEXT, identifier=True)
    membership = spec.add_class("Membership")
    membership.slot("canonical_id", types.TEXT, identifier=True)
    membership.slot("user", person)
    membership.slot("friend", person)
    with pytest.raises(ValueError, match="multiple FKs"):
        person.has_any(membership)
    # Explicit via= resolves the ambiguity.
    via = person.has_any(membership, via="user")
    assert via.fk_slot_name == "user"
