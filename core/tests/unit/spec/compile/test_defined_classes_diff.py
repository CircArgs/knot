"""Pure-Python compile tests for ReverseRelation and FK-chain SlotPath.

Covers ReverseRelation compile correctness and multi-slot SlotPath
compilation through a ClassRef FK chain.

DB-bound end-to-end tests live in
``tests/integration/spec/compile/test_defined_classes.py``.
"""

from __future__ import annotations

import pytest

from knot.spec import OntologyClass, Primitive, Slot, Spec
from knot.spec.compile.postgres import CompileContext, compile_predicate, migration
from knot.spec.metaschema import (
    ClassRef,
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
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])

    credit_id = Slot(name="credit_id", type=Primitive(name="string"))
    person_fk = Slot(name="person", type=ClassRef(target_class=person))
    role = Slot(name="role", type=Primitive(name="string"))
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)
    node = RelationAny(relation=rev)

    ctx = CompileContext(primary_class=person, alias="s")
    result = compile_predicate(node, ctx)
    sql_str = result.as_string(None)

    assert "EXISTS" in sql_str
    assert "credit" in sql_str.lower()
    assert "person" in sql_str.lower()


def test_reverse_relation_in_relation_all_compiles():
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])

    credit_id = Slot(name="credit_id", type=Primitive(name="string"))
    person_fk = Slot(name="person", type=ClassRef(target_class=person))
    role = Slot(name="role", type=Primitive(name="string"))
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
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])

    credit_id = Slot(name="credit_id", type=Primitive(name="string"))
    person_fk = Slot(name="person", type=ClassRef(target_class=person))
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk])

    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)

    ctx = CompileContext(primary_class=person, alias="s")
    with pytest.raises(NotImplementedError):
        compile_predicate(rev, ctx)


# ---------------------------------------------------------------------------
# Compile through FK chain
# ---------------------------------------------------------------------------


def test_multi_slot_path_compile():
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person_name = Slot(name="name", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id, person_name])

    credit_id = Slot(name="credit_id", type=Primitive(name="string"))
    person_fk = Slot(name="person", type=ClassRef(target_class=person))
    role = Slot(name="role", type=Primitive(name="string"))
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


# ---------------------------------------------------------------------------
# diff_specs: class add/drop basics
# ---------------------------------------------------------------------------


def test_diff_specs_add_class():
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])

    prev_spec = Spec(id="t", version="1", classes=[person], sources=[])
    director = OntologyClass(name="Director", is_a=person, slots=[])
    cand_spec = Spec(
        id="t",
        version="1",
        classes=[person, director],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    add_class = [c for c in changes if isinstance(c, migration.AddClass) and c.cls.name == "Director"]
    assert add_class, f"Expected AddClass for Director; got {changes}"


def test_diff_specs_drop_class():
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])
    director = OntologyClass(name="Director", is_a=person, slots=[])

    prev_spec = Spec(
        id="t",
        version="1",
        classes=[person, director],
        sources=[],
    )
    cand_spec = Spec(id="t", version="1", classes=[person], sources=[])

    changes = migration.diff_specs(prev_spec, cand_spec)
    drop_class = [c for c in changes if isinstance(c, migration.DropClass) and c.class_name == "Director"]
    assert drop_class, f"Expected DropClass for Director; got {changes}"
