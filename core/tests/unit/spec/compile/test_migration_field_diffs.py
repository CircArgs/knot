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
    Constraint,
    Literal_,
    OntologyClass,
    Primitive,
    ResolutionPolicy,
    Severity,
    Slot,
    SlotConstraints,
    Source,
    SourceBinding,
    Spec,
)
from knot.spec.compile.postgres.migration import (
    AddConstraint,
    AddSource,
    AddSourceBinding,
    ChangeClassAbstract,
    ChangeClassIsA,
    ChangeClassMixins,
    ChangeConstraintBody,
    ChangeConstraintPrimary,
    ChangeConstraintSeverity,
    ChangeSlotDerivation,
    ChangeSlotIdentifier,
    ChangeSlotMaximum,
    ChangeSlotMinimum,
    ChangeSlotPattern,
    ChangeSlotPermissibleValues,
    ChangeSlotResolutionPolicy,
    ChangeSlotTypeExpression,
    ChangeSourceBindingIdentifierSlot,
    ChangeSourceBindingTrust,
    DropConstraint,
    DropSource,
    DropSourceBinding,
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
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True, required=True)
        movie = OntologyClass(name="Movie", slots=[id_slot])
        src = Source(name="imdb")
        binding = SourceBinding(
            source=src,
            class_=movie,
            identifier_slot=id_slot,
        )
        spec = Spec(
            id="t",
            version="1.0.0",
            classes=[movie],
            sources=[src],
            source_bindings=[binding],
        )
        return {
            "spec": spec,
            "id_slot": id_slot,
            "movie": movie,
            "source": src,
            "binding": binding,
        }

    prev = _build()
    cand = _build()
    mutator(cand)
    return prev["spec"], cand["spec"]


# ---------------------------------------------------------------------------
# 1. Slot field diffs
# ---------------------------------------------------------------------------


def test_diff_change_slot_pattern_emits_record():
    def mutate(s):
        s["id_slot"].constraints = SlotConstraints(pattern=r"^tt[0-9]+$")

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
        s["id_slot"].constraints = SlotConstraints(permissible_values=["a", "b"])

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
        s["id_slot"].constraints = SlotConstraints(min_value=0.0)

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
        s["id_slot"].constraints = SlotConstraints(max_value=100.0)

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


def test_diff_change_slot_type_expression_emits_record():
    """Changing slot type from string→integer emits ChangeSlotTypeExpression."""
    id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True, required=True)
    year_str = Slot(name="year", type=Primitive(name="string"))
    year_int = Slot(name="year", type=Primitive(name="integer"))
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, year_int])
    prev = Spec(id="t", version="1.0.0", classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", classes=[cand_movie])
    changes = diff_specs(prev, cand)
    rec = next((c for c in changes if isinstance(c, ChangeSlotTypeExpression)), None)
    assert rec is not None
    assert rec.slot.name == "year"
    assert rec.prev_pg_type == "TEXT"
    assert rec.new_pg_type == "INTEGER"


def test_change_slot_type_expression_is_destructive():
    id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
    cls = OntologyClass(name="Movie", slots=[id_slot])
    slot = Slot(name="year", type=Primitive(name="integer"))
    rec = ChangeSlotTypeExpression(cls=cls, slot=slot, prev_pg_type="TEXT", new_pg_type="INTEGER")
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
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        title = Slot(name="title", type=Primitive(name="string"))
        derived = Slot(
            name="upper_title",
            type=Primitive(name="string"),
            derivation=ScalarDerivation(expression=Literal_(value="X")),
        )
        movie = OntologyClass(name="Movie", slots=[id_slot, title, derived])
        return Spec(
            id="t",
            version="1.0.0",
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
# 2. OntologyClass field diffs
# ---------------------------------------------------------------------------


def test_diff_change_class_abstract_emits_record():
    def build(*, abstract):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Title", slots=[id_slot], abstract=abstract)
        return Spec(id="t", version="1.0.0", classes=[cls])

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
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        a = OntologyClass(name="A", slots=[id_slot])
        b = OntologyClass(name="B", slots=[id_slot])
        child = OntologyClass(
            name="Child", slots=[id_slot], is_a={"A": a, "B": b, None: None}[parent_name]
        )
        classes = [a, b, child]
        return Spec(id="t", version="1.0.0", classes=classes)

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
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        a = OntologyClass(name="A", slots=[])
        b = OntologyClass(name="B", slots=[])
        mx_lookup = {"A": a, "B": b}
        cls = OntologyClass(
            name="Child", slots=[id_slot], mixins=[mx_lookup[m] for m in mixin_names]
        )
        return Spec(id="t", version="1.0.0", classes=[a, b, cls])

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


# ---------------------------------------------------------------------------
# 3. Source field diffs
# ---------------------------------------------------------------------------


def test_diff_add_source_emits_record():
    def build(*, with_source):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        sources = [Source(name="imdb")] if with_source else []
        return Spec(
            id="t",
            version="1.0.0",
            classes=[cls],
            sources=sources,
        )

    changes = diff_specs(build(with_source=False), build(with_source=True))
    rec = next((c for c in changes if isinstance(c, AddSource)), None)
    assert rec is not None
    assert rec.source_name == "imdb"


def test_diff_drop_source_emits_record():
    def build(*, with_source):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        sources = [Source(name="imdb")] if with_source else []
        return Spec(
            id="t",
            version="1.0.0",
            classes=[cls],
            sources=sources,
        )

    changes = diff_specs(build(with_source=True), build(with_source=False))
    rec = next((c for c in changes if isinstance(c, DropSource)), None)
    assert rec is not None
    assert rec.source_name == "imdb"


def test_drop_source_is_destructive():
    assert is_destructive(DropSource(source_name="imdb"))


# ---------------------------------------------------------------------------
# 4. SourceBinding field diffs
# ---------------------------------------------------------------------------


def test_diff_add_source_binding_emits_record():
    def build(*, with_binding):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        src = Source(name="imdb")
        bindings = (
            [SourceBinding(source=src, class_=cls, identifier_slot=id_slot)] if with_binding else []
        )
        return Spec(
            id="t",
            version="1.0.0",
            classes=[cls],
            sources=[src],
            source_bindings=bindings,
        )

    changes = diff_specs(build(with_binding=False), build(with_binding=True))
    rec = next((c for c in changes if isinstance(c, AddSourceBinding)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.class_name == "Movie"
    assert rec.identifier_slot == "id"


def test_diff_drop_source_binding_emits_record():
    def build(*, with_binding):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        src = Source(name="imdb")
        bindings = (
            [SourceBinding(source=src, class_=cls, identifier_slot=id_slot)] if with_binding else []
        )
        return Spec(
            id="t",
            version="1.0.0",
            classes=[cls],
            sources=[src],
            source_bindings=bindings,
        )

    changes = diff_specs(build(with_binding=True), build(with_binding=False))
    rec = next((c for c in changes if isinstance(c, DropSourceBinding)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.class_name == "Movie"


def test_drop_source_binding_is_destructive():
    assert is_destructive(DropSourceBinding(source_name="imdb", class_name="Movie"))


def test_diff_change_source_binding_identifier_slot_emits_record():
    def build(*, identifier_name):
        slot_a = Slot(name="id_a", type=Primitive(name="string"), identifier=True)
        slot_b = Slot(name="id_b", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Movie", slots=[slot_a, slot_b])
        src = Source(name="imdb")
        identifier = {"id_a": slot_a, "id_b": slot_b}[identifier_name]
        binding = SourceBinding(source=src, class_=cls, identifier_slot=identifier)
        return Spec(
            id="t",
            version="1.0.0",
            classes=[cls],
            sources=[src],
            source_bindings=[binding],
        )

    changes = diff_specs(build(identifier_name="id_a"), build(identifier_name="id_b"))
    rec = next((c for c in changes if isinstance(c, ChangeSourceBindingIdentifierSlot)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.class_name == "Movie"
    assert rec.old_slot == "id_a"
    assert rec.new_slot == "id_b"


def test_change_source_binding_identifier_slot_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    rec = ChangeSourceBindingIdentifierSlot(
        cls=cls, source_name="imdb", class_name="Movie", old_slot="id_a", new_slot="id_b"
    )
    assert is_destructive(rec)


def test_diff_change_source_binding_trust_emits_record():
    """diff_specs emits ChangeSourceBindingTrust when trust_prior changes.

    trust_prior is RUNTIME (excluded from the content hash) but diff_specs
    compares live objects directly and emits a Bucket C audit record.
    No DDL is emitted; the record is non-destructive.
    """

    def build(*, trust_prior):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        cls = OntologyClass(name="Movie", slots=[id_slot])
        src = Source(name="imdb")
        binding = SourceBinding(
            source=src, class_=cls, identifier_slot=id_slot, trust_prior=trust_prior
        )
        return Spec(
            id="t",
            version="1.0.0",
            classes=[cls],
            sources=[src],
            source_bindings=[binding],
        )

    changes = diff_specs(build(trust_prior=(1.0, 1.0)), build(trust_prior=(5.0, 2.0)))
    rec = next((c for c in changes if isinstance(c, ChangeSourceBindingTrust)), None)
    assert rec is not None
    assert rec.source_name == "imdb"
    assert rec.class_name == "Movie"
    assert rec.old_prior == (1.0, 1.0)
    assert rec.new_prior == (5.0, 2.0)


def test_change_source_binding_trust_is_not_destructive():
    rec = ChangeSourceBindingTrust(
        source_name="imdb",
        class_name="Movie",
        old_prior=(1.0, 1.0),
        new_prior=(5.0, 2.0),
    )
    assert not is_destructive(rec)


# ---------------------------------------------------------------------------
# 5. Constraint field diffs
# ---------------------------------------------------------------------------


def _build_constraint_spec(*, body_value=1, severity=Severity.ERROR, primary_name="Movie"):
    id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[id_slot, year])
    series = OntologyClass(name="Series", slots=[id_slot, year])
    primary_cls = {"Movie": movie, "Series": series}[primary_name]
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)
    con = Constraint(
        name="year_positive",
        primary=primary_cls,
        body=f"year > {body_value}",
        severity=severity,
    )
    return Spec(
        id="t",
        version="1.0.0",
        classes=[movie, series],
        sources=[src],
        source_bindings=[binding],
        constraints=[con],
    )


def _build_constraintless_spec():
    id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[id_slot, year])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)
    return Spec(
        id="t",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
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


def test_diff_change_constraint_body_different_sql_emits_record():
    """AST-inequivalent SQL bodies produce a ChangeConstraintBody record."""

    def build(*, value):
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True)
        year = Slot(name="year", type=Primitive(name="integer"))
        movie = OntologyClass(name="Movie", slots=[id_slot, year])
        con = Constraint(name="c", primary=movie, body=f"year > {value}")
        return Spec(
            id="t",
            version="1.0.0",
            classes=[movie],
            constraints=[con],
        )

    changes = diff_specs(build(value=1), build(value=2))
    assert any(isinstance(c, ChangeConstraintBody) for c in changes)
