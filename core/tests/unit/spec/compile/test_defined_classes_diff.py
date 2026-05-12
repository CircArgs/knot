"""Pure-Python compile tests for ReverseRelation and FK-chain PropertyPath.

Covers ReverseRelation compile correctness and multi-slot PropertyPath
compilation through a ClassRef FK chain.

DB-bound end-to-end tests live in
``tests/integration/spec/compile/test_defined_classes.py``.
"""

from __future__ import annotations

import pytest

from knot.spec import OntologyClass, Primitive, Property, Spec
from knot.spec.compile.postgres import CompileContext, compile_predicate, migration
from knot.spec.metaschema import (
    ClassRef,
    Compare,
    CompareOp,
    Literal_,
    RelationAll,
    RelationAny,
    ReverseRelation,
    PropertyPath,
)

# ---------------------------------------------------------------------------
# ReverseRelation compile correctness
# ---------------------------------------------------------------------------


def test_reverse_relation_compiles():
    person_id = Property(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", properties=[person_id])

    credit_id = Property(name="credit_id", type=Primitive(name="string"))
    person_fk = Property(name="person", type=ClassRef(target_class=person))
    role = Property(name="role", type=Primitive(name="string"))
    credit = OntologyClass(name="Credit", properties=[credit_id, person_fk, role])

    rev = ReverseRelation(target_class=credit, fk_property=person_fk)
    node = RelationAny(relation=rev)

    ctx = CompileContext(primary_class=person, alias="s")
    result = compile_predicate(node, ctx)
    sql_str = result.as_string(None)

    assert "EXISTS" in sql_str
    assert "credit" in sql_str.lower()
    assert "person" in sql_str.lower()


def test_reverse_relation_in_relation_all_compiles():
    person_id = Property(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", properties=[person_id])

    credit_id = Property(name="credit_id", type=Primitive(name="string"))
    person_fk = Property(name="person", type=ClassRef(target_class=person))
    role = Property(name="role", type=Primitive(name="string"))
    credit = OntologyClass(name="Credit", properties=[credit_id, person_fk, role])

    rev = ReverseRelation(target_class=credit, fk_property=person_fk)
    role_path = PropertyPath(from_class=credit, properties=[role])
    body = Compare(op=CompareOp.EQ, left=role_path, right=Literal_(value="director"))
    node = RelationAll(relation=rev, body=body)

    ctx = CompileContext(primary_class=person, alias="s")
    result = compile_predicate(node, ctx)
    sql_str = result.as_string(None)
    assert "NOT EXISTS" in sql_str
    assert "credit" in sql_str.lower()


def test_reverse_relation_direct_raises_compiler_error():
    person_id = Property(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", properties=[person_id])

    credit_id = Property(name="credit_id", type=Primitive(name="string"))
    person_fk = Property(name="person", type=ClassRef(target_class=person))
    credit = OntologyClass(name="Credit", properties=[credit_id, person_fk])

    rev = ReverseRelation(target_class=credit, fk_property=person_fk)

    ctx = CompileContext(primary_class=person, alias="s")
    with pytest.raises(NotImplementedError):
        compile_predicate(rev, ctx)


# ---------------------------------------------------------------------------
# Compile through FK chain
# ---------------------------------------------------------------------------


def test_multi_slot_path_compile():
    person_id = Property(name="person_id", type=Primitive(name="string"))
    person_name = Property(name="name", type=Primitive(name="string"))
    person = OntologyClass(name="Person", properties=[person_id, person_name])

    credit_id = Property(name="credit_id", type=Primitive(name="string"))
    person_fk = Property(name="person", type=ClassRef(target_class=person))
    role = Property(name="role", type=Primitive(name="string"))
    credit = OntologyClass(name="Credit", properties=[credit_id, person_fk, role])

    # PropertyPath: Credit.person.name (person_fk → person_name)
    path = PropertyPath(from_class=credit, properties=[person_fk, person_name])
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
    person_id = Property(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", properties=[person_id])

    prev_spec = Spec(id="t", version="1", classes=[person], sources=[])
    director = OntologyClass(name="Director", is_a=person, properties=[])
    cand_spec = Spec(
        id="t",
        version="1",
        classes=[person, director],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    add_class = [
        c for c in changes if isinstance(c, migration.AddClass) and c.cls.name == "Director"
    ]
    assert add_class, f"Expected AddClass for Director; got {changes}"


def test_diff_specs_drop_class():
    person_id = Property(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", properties=[person_id])
    director = OntologyClass(name="Director", is_a=person, properties=[])

    prev_spec = Spec(
        id="t",
        version="1",
        classes=[person, director],
        sources=[],
    )
    cand_spec = Spec(id="t", version="1", classes=[person], sources=[])

    changes = migration.diff_specs(prev_spec, cand_spec)
    drop_class = [
        c for c in changes if isinstance(c, migration.DropClass) and c.class_name == "Director"
    ]
    assert drop_class, f"Expected DropClass for Director; got {changes}"
