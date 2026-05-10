"""Pure-Python compile + diff tests for defined classes (OWL DL equivalentClass).

Covers ReverseRelation compile correctness, multi-slot SlotPath
compilation through an FK chain, RelationAll wrapping ReverseRelation,
and the diff_specs transitions between concrete and defined-class form.

DB-bound end-to-end tests (Director view materialisation, GraphQL
shape, publish-gate rejection paths) live in
``tests/integration/spec/compile/test_defined_classes.py``.
"""

from __future__ import annotations

import pytest

from knot.spec import OntologyClass, Slot, Spec, TypeDefinition
from knot.spec.compile.postgres import CompileContext, compile_predicate, migration
from knot.spec.metaschema import (
    Compare,
    CompareOp,
    Literal_,
    RelationAll,
    RelationAny,
    ReverseRelation,
    SlotPath,
)


# ---------------------------------------------------------------------------
# ReverseRelation compile correctness
# ---------------------------------------------------------------------------


def test_reverse_relation_compiles():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    credit_id = Slot(name="credit_id", range=string_t)
    person_fk = Slot(name="person", range=person)
    role = Slot(name="role", range=string_t)
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)
    node = RelationAny(relation=rev)

    ctx = CompileContext(primary_class=person, alias="s")
    result = compile_predicate(node, ctx)
    sql_str = result.as_string(None)

    assert "EXISTS" in sql_str
    assert "credit" in sql_str.lower()
    assert "person" in sql_str.lower()


# ---------------------------------------------------------------------------
# Diff: concrete ↔ defined transitions are destructive
# ---------------------------------------------------------------------------


def test_concrete_to_defined_transition_is_destructive():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    # prev: Director is concrete
    director_prev = OntologyClass(name="Director", is_a=person, slots=[])
    prev_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_prev],
        sources=[],
    )

    # cand: Director is defined
    role_path = SlotPath(from_class=person, slots=[person_id])
    definition = Compare(op=CompareOp.IS_NOT_NULL, left=role_path)
    director_cand = OntologyClass(name="Director", is_a=person, slots=[], definition=definition)
    cand_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_cand],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    destructive = [c for c in changes if migration.is_destructive(c)]
    assert any(isinstance(c, migration.DropClass) for c in changes), (
        f"Expected DropClass in {changes}"
    )
    assert any(isinstance(c, migration.AddDefinedClass) for c in changes), (
        f"Expected AddDefinedClass in {changes}"
    )
    assert destructive, "Concrete→defined should be destructive"


def test_defined_to_concrete_transition_is_destructive():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    # prev: Director is defined (has definition)
    role_path = SlotPath(from_class=person, slots=[person_id])
    definition = Compare(op=CompareOp.IS_NOT_NULL, left=role_path)
    director_prev = OntologyClass(name="Director", is_a=person, slots=[], definition=definition)
    prev_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_prev],
        sources=[],
    )

    # cand: Director becomes concrete
    director_cand = OntologyClass(name="Director", is_a=person, slots=[])
    cand_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_cand],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    destructive = [c for c in changes if migration.is_destructive(c)]
    assert any(isinstance(c, migration.DropDefinedClass) for c in changes), (
        f"Expected DropDefinedClass in {changes}"
    )
    assert any(isinstance(c, migration.AddClass) for c in changes), (
        f"Expected AddClass in {changes}"
    )
    assert destructive, "Defined→concrete should be destructive"


def test_defined_class_redefinition_reemits_view():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    role_path = SlotPath(from_class=person, slots=[person_id])
    def1 = Compare(op=CompareOp.IS_NOT_NULL, left=role_path)
    def2 = Compare(op=CompareOp.IS_NULL, left=role_path)

    director_prev = OntologyClass(name="Director", is_a=person, slots=[], definition=def1)
    director_cand = OntologyClass(name="Director", is_a=person, slots=[], definition=def2)

    prev_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_prev],
        sources=[],
    )
    cand_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_cand],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    assert any(isinstance(c, migration.AddDefinedClass) for c in changes), (
        f"Expected AddDefinedClass (re-create view) in {changes}"
    )


# ---------------------------------------------------------------------------
# Compile through FK chain
# ---------------------------------------------------------------------------


def test_multi_slot_path_compile():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person_name = Slot(name="name", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id, person_name])

    credit_id = Slot(name="credit_id", range=string_t)
    person_fk = Slot(name="person", range=person)
    role = Slot(name="role", range=string_t)
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    # SlotPath: Credit.person.name (person_fk → person_name)
    path = SlotPath(from_class=credit, slots=[person_fk, person_name])
    node = Compare(op=CompareOp.EQ, left=path, right=Literal_(value="Alice"))

    ctx = CompileContext(primary_class=credit, alias="s")
    compile_predicate(node, ctx)

    # The final expression references the joined alias, not the table name directly.
    # The JOIN fragments accumulated in ctx.joins should reference the person table.
    assert len(ctx.joins) >= 1, "Expected JOIN fragments from multi-slot path"
    join_str = ctx.joins[0].as_string(None)
    assert "person" in join_str.lower(), f"Expected 'person' in JOIN: {join_str}"


def test_reverse_relation_in_relation_all_compiles():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    credit_id = Slot(name="credit_id", range=string_t)
    person_fk = Slot(name="person", range=person)
    role = Slot(name="role", range=string_t)
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)
    role_path = SlotPath(from_class=credit, slots=[role])
    body = Compare(op=CompareOp.EQ, left=role_path, right=Literal_(value="director"))
    node = RelationAll(relation=rev, body=body)

    ctx = CompileContext(primary_class=person, alias="s")
    result = compile_predicate(node, ctx)
    sql_str = result.as_string(None)
    assert "NOT EXISTS" in sql_str
    assert "credit" in sql_str.lower()


def test_reverse_relation_direct_raises_compiler_error():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    credit_id = Slot(name="credit_id", range=string_t)
    person_fk = Slot(name="person", range=person)
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk])

    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)

    ctx = CompileContext(primary_class=person, alias="s")
    with pytest.raises(NotImplementedError):
        compile_predicate(rev, ctx)


# ---------------------------------------------------------------------------
# diff_specs: defined class adds and re-emits
# ---------------------------------------------------------------------------


def test_diff_specs_new_defined_class():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    role_path = SlotPath(from_class=person, slots=[person_id])
    definition = Compare(op=CompareOp.IS_NOT_NULL, left=role_path)
    director = OntologyClass(name="Director", is_a=person, slots=[], definition=definition)

    prev_spec = Spec(
        id="t", version="1", types=[string_t], slots=[person_id], classes=[person], sources=[]
    )
    cand_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    # Should produce AddDefinedClass, NOT AddClass for director
    add_defined = [c for c in changes if isinstance(c, migration.AddDefinedClass)]
    add_class = [
        c for c in changes if isinstance(c, migration.AddClass) and c.cls.name == "Director"
    ]
    assert add_defined, f"Expected AddDefinedClass; got {changes}"
    assert not add_class, f"Should NOT produce AddClass for defined class; got {changes}"


def test_diff_specs_both_defined_re_emits_view():
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    role_path = SlotPath(from_class=person, slots=[person_id])
    def1 = Compare(op=CompareOp.IS_NOT_NULL, left=role_path)
    def2 = Compare(op=CompareOp.IS_NULL, left=role_path)

    director_prev = OntologyClass(name="Director", is_a=person, slots=[], definition=def1)
    director_cand = OntologyClass(name="Director", is_a=person, slots=[], definition=def2)

    prev_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_prev],
        sources=[],
    )
    cand_spec = Spec(
        id="t",
        version="1",
        types=[string_t],
        slots=[person_id],
        classes=[person, director_cand],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    assert any(isinstance(c, migration.AddDefinedClass) for c in changes), (
        f"Expected AddDefinedClass in {changes}"
    )
