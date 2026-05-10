"""Per-canonical-field diff tests (pure Python).

The bug these guard against: ``diff_specs`` previously emitted Change
records only for adds, drops, slot type, and slot required. Every other
canonical field edit silently shifted the spec content hash without
producing a Change, leaving the destructive gate blind to changes that
are silently destructive at the data plane.

These tests build two Specs differing only in one field and assert the
expected Change record is emitted by ``diff_specs``. No SQL execution —
``diff_specs`` is a pure transform.

Publish-gate tests that drive the full draft → publish flow live in
``tests/integration/spec/compile/test_migration_field_diffs_publish.py``.
"""

from __future__ import annotations

from knot.spec import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
    Literal_,
    OntologyClass,
    PermissibleValue,
    ResolutionPolicy,
    Severity,
    Slot,
    SlotPath,
    Source,
    Spec,
    TypeDefinition,
)
from knot.spec.compile.postgres.migration import (
    AddConstraint,
    AddSource,
    ChangeClassAbstract,
    ChangeClassDefinition,
    ChangeClassIsA,
    ChangeClassMixins,
    ChangeConstraintBody,
    ChangeConstraintPrimary,
    ChangeConstraintSeverity,
    ChangeSlotDerivation,
    ChangeSlotIdentifier,
    ChangeSlotMaximum,
    ChangeSlotMinimum,
    ChangeSlotMultivalued,
    ChangeSlotPattern,
    ChangeSlotPermissibleValues,
    ChangeSlotResolutionPolicy,
    ChangeSourceEntityClass,
    ChangeSourceIdentifierSlot,
    ChangeTypeBase,
    ChangeTypePattern,
    DropConstraint,
    DropSource,
    diff_specs,
    is_destructive,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec_pair(*, mutator):
    """Build (prev, candidate) where ``mutator(spec_kwargs)`` rewires the
    candidate. Both specs share NO objects — full rebuild ensures identity
    matches what serialize/rehydrate would produce."""

    def _build():
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True, required=True)
        movie = OntologyClass(name="Movie", slots=[id_slot])
        src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
        spec = Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot],
            classes=[movie],
            sources=[src],
        )
        return {
            "spec": spec,
            "type": st,
            "id_slot": id_slot,
            "movie": movie,
            "source": src,
        }

    prev = _build()
    cand = _build()
    mutator(cand)
    return prev["spec"], cand["spec"]


# ---------------------------------------------------------------------------
# 1. TypeDefinition field diffs
# ---------------------------------------------------------------------------


def test_diff_change_type_base_emits_record():
    def mutate(s):
        s["type"].base = "int"

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeTypeBase)), None)
    assert rec is not None, f"no ChangeTypeBase in {changes!r}"
    assert rec.type_name == "string"
    assert rec.old_base == "str"
    assert rec.new_base == "int"


def test_change_type_base_is_destructive():
    rec = ChangeTypeBase(type_name="string", old_base="str", new_base="int")
    assert is_destructive(rec)


def test_diff_change_type_pattern_emits_record():
    def mutate(s):
        s["type"].pattern = r"^[a-z]+$"

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeTypePattern)), None)
    assert rec is not None
    assert rec.type_name == "string"
    assert rec.old_pattern is None
    assert rec.new_pattern == r"^[a-z]+$"


def test_change_type_pattern_is_not_destructive():
    rec = ChangeTypePattern(type_name="string", old_pattern=None, new_pattern="^a$")
    assert not is_destructive(rec)


# ---------------------------------------------------------------------------
# 2. Slot field diffs
# ---------------------------------------------------------------------------


def test_diff_change_slot_pattern_emits_record():
    def mutate(s):
        s["id_slot"].pattern = r"^tt[0-9]+$"

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotPattern)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_pattern is None
    assert rec.new_pattern == r"^tt[0-9]+$"


def test_change_slot_pattern_is_not_destructive():
    rec = ChangeSlotPattern(slot_name="id", old_pattern=None, new_pattern="^a$")
    assert not is_destructive(rec)


def test_diff_change_slot_permissible_values_emits_record():
    def mutate(s):
        s["id_slot"].permissible_values = [
            PermissibleValue(text="a"),
            PermissibleValue(text="b"),
        ]

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotPermissibleValues)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_values is None
    assert rec.new_values == ["a", "b"]


def test_change_slot_permissible_values_is_not_destructive():
    rec = ChangeSlotPermissibleValues(slot_name="id", old_values=None, new_values=["a"])
    assert not is_destructive(rec)


def test_diff_change_slot_minimum_emits_record():
    def mutate(s):
        s["id_slot"].minimum_value = 0.0

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotMinimum)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_value is None
    assert rec.new_value == 0.0


def test_change_slot_minimum_is_not_destructive():
    rec = ChangeSlotMinimum(slot_name="id", old_value=None, new_value=0.0)
    assert not is_destructive(rec)


def test_diff_change_slot_maximum_emits_record():
    def mutate(s):
        s["id_slot"].maximum_value = 100.0

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotMaximum)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_value is None
    assert rec.new_value == 100.0


def test_change_slot_maximum_is_not_destructive():
    rec = ChangeSlotMaximum(slot_name="id", old_value=None, new_value=100.0)
    assert not is_destructive(rec)


def test_diff_change_slot_multivalued_emits_record():
    def mutate(s):
        s["id_slot"].multivalued = True

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotMultivalued)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_value is False
    assert rec.new_value is True


def test_change_slot_multivalued_is_destructive():
    st = TypeDefinition(name="string", base="str")
    id_slot = Slot(name="id", range=st, identifier=True)
    cls = OntologyClass(name="Movie", slots=[id_slot])
    rec = ChangeSlotMultivalued(
        cls=cls, slot=id_slot, slot_name="id", old_value=False, new_value=True
    )
    assert is_destructive(rec)


def test_diff_change_slot_identifier_emits_record():
    def mutate(s):
        s["id_slot"].identifier = False

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotIdentifier)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_value is True
    assert rec.new_value is False


def test_change_slot_identifier_is_not_destructive():
    """The storage PK is (_source, _source_row_id), NOT the slot marked
    identifier=True. The flag is advisory at the storage layer (drives ER /
    SCD2 bindings) and emits no DDL. Audit-only record."""
    rec = ChangeSlotIdentifier(slot_name="id", old_value=True, new_value=False)
    assert not is_destructive(rec)


def test_diff_change_slot_resolution_policy_emits_record():
    def mutate(s):
        s["id_slot"].resolution_policy = ResolutionPolicy.LCB

    prev, cand = _make_spec_pair(mutator=mutate)
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotResolutionPolicy)), None)
    assert rec is not None
    assert rec.slot_name == "id"
    assert rec.old_value == "argmax_trust"
    assert rec.new_value == "lcb"


def test_change_slot_resolution_policy_is_not_destructive():
    rec = ChangeSlotResolutionPolicy(slot_name="id", old_value="argmax_trust", new_value="lcb")
    assert not is_destructive(rec)


def test_diff_change_slot_derivation_body_change_emits_record():
    """Derivation body change (both prev and cand have a derivation)."""
    from knot.spec.metaschema import ScalarDerivation

    def build():
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        title = Slot(name="title", range=st)
        derived = Slot(
            name="upper_title",
            range=st,
            derivation=ScalarDerivation(expression=Literal_(value="X")),
        )
        movie = OntologyClass(name="Movie", slots=[id_slot, title, derived])
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot, title, derived],
            classes=[movie],
        ), derived

    prev_spec, _ = build()
    cand_spec, derived = build()
    derived.derivation = ScalarDerivation(expression=Literal_(value="Y"))

    changes = diff_specs(prev_spec, cand_spec)
    rec = next((c for c in changes if isinstance(c, ChangeSlotDerivation)), None)
    assert rec is not None
    assert rec.slot_name == "upper_title"
    assert rec.had_derivation_before is True
    assert rec.has_derivation_now is True
    assert rec.derivation_changed is True


def test_change_slot_derivation_is_not_destructive():
    rec = ChangeSlotDerivation(
        slot_name="x",
        had_derivation_before=True,
        has_derivation_now=True,
        derivation_changed=True,
    )
    assert not is_destructive(rec)


# ---------------------------------------------------------------------------
# 3. OntologyClass field diffs
# ---------------------------------------------------------------------------


def test_diff_change_class_abstract_emits_record():
    def build(*, abstract):
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        cls = OntologyClass(name="Title", slots=[id_slot], abstract=abstract)
        return Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[cls])

    changes = diff_specs(build(abstract=False), build(abstract=True))
    rec = next((c for c in changes if isinstance(c, ChangeClassAbstract)), None)
    assert rec is not None
    assert rec.class_name == "Title"
    assert rec.old_value is False
    assert rec.new_value is True


def test_change_class_abstract_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    rec = ChangeClassAbstract(cls=cls, class_name="Movie", old_value=False, new_value=True)
    assert is_destructive(rec)


def test_diff_change_class_is_a_emits_record():
    def build(*, parent_name):
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        a = OntologyClass(name="A", slots=[id_slot])
        b = OntologyClass(name="B", slots=[id_slot])
        child = OntologyClass(
            name="Child", slots=[id_slot], is_a={"A": a, "B": b, None: None}[parent_name]
        )
        classes = [a, b, child]
        return Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=classes)

    changes = diff_specs(build(parent_name="A"), build(parent_name="B"))
    rec = next((c for c in changes if isinstance(c, ChangeClassIsA)), None)
    assert rec is not None
    assert rec.class_name == "Child"
    assert rec.old_parent == "A"
    assert rec.new_parent == "B"


def test_change_class_is_a_is_destructive():
    cls = OntologyClass(name="Child", slots=[])
    rec = ChangeClassIsA(cls=cls, class_name="Child", old_parent="A", new_parent="B")
    assert is_destructive(rec)


def test_diff_change_class_mixins_emits_record():
    def build(*, mixin_names):
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        a = OntologyClass(name="A", slots=[])
        b = OntologyClass(name="B", slots=[])
        mx_lookup = {"A": a, "B": b}
        cls = OntologyClass(
            name="Child", slots=[id_slot], mixins=[mx_lookup[m] for m in mixin_names]
        )
        return Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[a, b, cls])

    changes = diff_specs(build(mixin_names=["A"]), build(mixin_names=["A", "B"]))
    rec = next((c for c in changes if isinstance(c, ChangeClassMixins)), None)
    assert rec is not None
    assert rec.class_name == "Child"
    assert rec.old_mixins == ["A"]
    assert rec.new_mixins == ["A", "B"]


def test_change_class_mixins_is_not_destructive():
    """The slot-level AddSlot / DropSlot records emitted alongside this one
    do the destructive gating; the mixins record is audit-only."""
    rec = ChangeClassMixins(class_name="Child", old_mixins=["A"], new_mixins=["A", "B"])
    assert not is_destructive(rec)


def test_diff_change_class_definition_body_change_emits_record():
    """Both prev and cand define the same class as a defined class with
    different definition bodies."""

    def build(*, body_value):
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        parent = OntologyClass(name="Person", slots=[id_slot])
        path = SlotPath(from_class=parent, slots=[id_slot])
        body = Compare(op=CompareOp.EQ, left=path, right=Literal_(value=body_value))
        defined = OntologyClass(name="Director", is_a=parent, slots=[], definition=body)
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot],
            classes=[parent, defined],
        )

    changes = diff_specs(build(body_value="A"), build(body_value="B"))
    rec = next((c for c in changes if isinstance(c, ChangeClassDefinition)), None)
    assert rec is not None
    assert rec.class_name == "Director"
    assert rec.had_definition_before is True
    assert rec.has_definition_now is True
    assert rec.definition_changed is True


def test_change_class_definition_is_not_destructive():
    cls = OntologyClass(name="Director", slots=[])
    rec = ChangeClassDefinition(
        cls=cls,
        class_name="Director",
        had_definition_before=True,
        has_definition_now=True,
        definition_changed=True,
    )
    assert not is_destructive(rec)


# ---------------------------------------------------------------------------
# 4. Source field diffs
# ---------------------------------------------------------------------------


def test_diff_add_source_emits_record():
    def build(*, with_source):
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        sources = (
            [Source(name="imdb", entity_class=cls, identifier_slot=id_slot)] if with_source else []
        )
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot],
            classes=[cls],
            sources=sources,
        )

    changes = diff_specs(build(with_source=False), build(with_source=True))
    rec = next((c for c in changes if isinstance(c, AddSource)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.entity_class == "Movie"
    assert rec.identifier_slot == "id"


def test_diff_drop_source_emits_record():
    def build(*, with_source):
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        sources = (
            [Source(name="imdb", entity_class=cls, identifier_slot=id_slot)] if with_source else []
        )
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot],
            classes=[cls],
            sources=sources,
        )

    changes = diff_specs(build(with_source=True), build(with_source=False))
    rec = next((c for c in changes if isinstance(c, DropSource)), None)
    assert rec is not None
    assert rec.source_name == "imdb"


def test_drop_source_is_destructive():
    assert is_destructive(DropSource(source_name="imdb"))


def test_diff_change_source_entity_class_emits_record():
    def build(*, target_class_name):
        st = TypeDefinition(name="string", base="str")
        id_slot_a = Slot(name="id_a", range=st, identifier=True)
        id_slot_b = Slot(name="id_b", range=st, identifier=True)
        movie = OntologyClass(name="Movie", slots=[id_slot_a])
        series = OntologyClass(name="Series", slots=[id_slot_b])
        target = {"Movie": (movie, id_slot_a), "Series": (series, id_slot_b)}[target_class_name]
        src = Source(name="imdb", entity_class=target[0], identifier_slot=target[1])
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot_a, id_slot_b],
            classes=[movie, series],
            sources=[src],
        )

    changes = diff_specs(build(target_class_name="Movie"), build(target_class_name="Series"))
    rec = next((c for c in changes if isinstance(c, ChangeSourceEntityClass)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.old_class == "Movie"
    assert rec.new_class == "Series"


def test_change_source_entity_class_is_destructive():
    rec = ChangeSourceEntityClass(source_name="imdb", old_class="Movie", new_class="Series")
    assert is_destructive(rec)


def test_diff_change_source_identifier_slot_emits_record():
    def build(*, identifier_name):
        st = TypeDefinition(name="string", base="str")
        slot_a = Slot(name="id_a", range=st, identifier=True)
        slot_b = Slot(name="id_b", range=st, identifier=True)
        cls = OntologyClass(name="Movie", slots=[slot_a, slot_b])
        identifier = {"id_a": slot_a, "id_b": slot_b}[identifier_name]
        src = Source(name="imdb", entity_class=cls, identifier_slot=identifier)
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[slot_a, slot_b],
            classes=[cls],
            sources=[src],
        )

    changes = diff_specs(build(identifier_name="id_a"), build(identifier_name="id_b"))
    rec = next((c for c in changes if isinstance(c, ChangeSourceIdentifierSlot)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.old_slot == "id_a"
    assert rec.new_slot == "id_b"


def test_change_source_identifier_slot_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    rec = ChangeSourceIdentifierSlot(cls=cls, source_name="imdb", old_slot="id_a", new_slot="id_b")
    assert is_destructive(rec)


# ---------------------------------------------------------------------------
# 5. Constraint field diffs
# ---------------------------------------------------------------------------


def _build_constraint_spec(*, body_value=1, severity=Severity.ERROR, primary_name="Movie"):
    st = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    id_slot = Slot(name="id", range=st, identifier=True)
    year = Slot(name="year", range=int_t)
    movie = OntologyClass(name="Movie", slots=[id_slot, year])
    series = OntologyClass(name="Series", slots=[id_slot, year])
    primary_cls = {"Movie": movie, "Series": series}[primary_name]
    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    body = Compare(
        op=CompareOp.GT,
        left=SlotPath(from_class=primary_cls, slots=[year]),
        right=Literal_(value=body_value),
    )
    con = Constraint(name="year_positive", primary=primary_cls, body=body, severity=severity)
    return Spec(
        id="t",
        version="1.0.0",
        types=[st, int_t],
        slots=[id_slot, year],
        classes=[movie, series],
        sources=[src],
        constraints=[con],
    )


def _build_constraintless_spec():
    st = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    id_slot = Slot(name="id", range=st, identifier=True)
    year = Slot(name="year", range=int_t)
    movie = OntologyClass(name="Movie", slots=[id_slot, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    return Spec(
        id="t",
        version="1.0.0",
        types=[st, int_t],
        slots=[id_slot, year],
        classes=[movie],
        sources=[src],
    )


def test_diff_add_constraint_emits_record():
    changes = diff_specs(_build_constraintless_spec(), _build_constraint_spec())
    rec = next((c for c in changes if isinstance(c, AddConstraint)), None)
    assert rec is not None
    assert rec.constraint_name == "year_positive"
    assert rec.primary == "Movie"


def test_diff_drop_constraint_emits_record():
    changes = diff_specs(_build_constraint_spec(), _build_constraintless_spec())
    rec = next((c for c in changes if isinstance(c, DropConstraint)), None)
    assert rec is not None
    assert rec.constraint_name == "year_positive"


def test_add_drop_constraint_is_not_destructive():
    """Constraint adds/drops don't reshape storage. The publish-gate
    constraint pass evaluates new constraints over current data."""
    assert not is_destructive(AddConstraint(constraint_name="x", primary="Movie"))
    assert not is_destructive(DropConstraint(constraint_name="x"))


def test_diff_change_constraint_primary_emits_record():
    changes = diff_specs(
        _build_constraint_spec(primary_name="Movie"),
        _build_constraint_spec(primary_name="Series"),
    )
    rec = next((c for c in changes if isinstance(c, ChangeConstraintPrimary)), None)
    assert rec is not None
    assert rec.constraint_name == "year_positive"
    assert rec.old_primary == "Movie"
    assert rec.new_primary == "Series"


def test_change_constraint_primary_is_not_destructive():
    """Bucket B — revalidation handled by publish-gate constraint pass."""
    rec = ChangeConstraintPrimary(constraint_name="x", old_primary="A", new_primary="B")
    assert not is_destructive(rec)


def test_diff_change_constraint_body_emits_record():
    changes = diff_specs(
        _build_constraint_spec(body_value=1),
        _build_constraint_spec(body_value=2),
    )
    rec = next((c for c in changes if isinstance(c, ChangeConstraintBody)), None)
    assert rec is not None
    assert rec.constraint_name == "year_positive"


def test_change_constraint_body_is_not_destructive():
    rec = ChangeConstraintBody(constraint_name="x")
    assert not is_destructive(rec)


def test_diff_change_constraint_severity_emits_record():
    changes = diff_specs(
        _build_constraint_spec(severity=Severity.ERROR),
        _build_constraint_spec(severity=Severity.WARNING),
    )
    rec = next((c for c in changes if isinstance(c, ChangeConstraintSeverity)), None)
    assert rec is not None
    assert rec.constraint_name == "year_positive"
    assert rec.old_severity == "error"
    assert rec.new_severity == "warning"


def test_change_constraint_severity_is_not_destructive():
    rec = ChangeConstraintSeverity(
        constraint_name="x", old_severity="error", new_severity="warning"
    )
    assert not is_destructive(rec)


# ---------------------------------------------------------------------------
# 6. Sanity: BoolExpr usage doesn't disturb the diff
# ---------------------------------------------------------------------------


def test_diff_bool_expr_constraint_body_emits_change_constraint_body():
    """Sanity check: a BoolExpr-bodied constraint that changes should still
    produce a ChangeConstraintBody record."""

    def build(*, value):
        st = TypeDefinition(name="string", base="str")
        int_t = TypeDefinition(name="integer", base="int")
        id_slot = Slot(name="id", range=st, identifier=True)
        year = Slot(name="year", range=int_t)
        movie = OntologyClass(name="Movie", slots=[id_slot, year])
        cmp_left = Compare(
            op=CompareOp.GT,
            left=SlotPath(from_class=movie, slots=[year]),
            right=Literal_(value=value),
        )
        body = BoolExpr(op=BoolOpKind.AND, operands=[cmp_left])
        con = Constraint(name="c", primary=movie, body=body)
        return Spec(
            id="t",
            version="1.0.0",
            types=[st, int_t],
            slots=[id_slot, year],
            classes=[movie],
            constraints=[con],
        )

    changes = diff_specs(build(value=1), build(value=2))
    assert any(isinstance(c, ChangeConstraintBody) for c in changes)
