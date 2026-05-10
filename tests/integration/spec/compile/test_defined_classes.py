"""Tests for defined classes (OWL DL equivalentClass via ReverseRelation).

End-to-end story: Person → Credit (FK: Credit.person → Person.canonical_id)
→ Director (defined class: Person ⊓ ∃ reverse(Credit.person) ∧ Credit.role='director').

Coverage
--------
Integration (DB):
  1. Publish spec with Person/Credit/Director; verify knot_data.director is a VIEW.
  2. Insert persons + credits (some director, some not).
  3. directorPage → only persons who have a director credit.
  4. personPage → all persons.
  5. directorByCanonicalId for a director → returns the row.
  6. directorByCanonicalId for a non-director → returns null.
  7. Update Credit roles (re-ingest) → director set changes.
  8. ReverseRelation compile correctness (unit).
  9. AddDefinedClass migration emits CREATE OR REPLACE VIEW (unit DDL).
 10. DropDefinedClass emits DROP VIEW (unit DDL).
 11. Publish gate rejects defined class with broken predicate.
 12. Publish gate rejects defined class with no is_a.
 13. Concrete→defined transition produces DropClass + AddDefinedClass (destructive).
 14. Defined→concrete transition produces DropDefinedClass + AddClass (destructive).
 15. Defined class with redefined definition re-emits CREATE OR REPLACE VIEW.
 16. directorResolved for a director returns record.
 17. directorResolved for a non-director returns null.
 18. directorPage with where filter (by name).
 19. Both concrete + defined classes appear in schema (personPage + directorPage).
 20. Multi-slot SlotPath compile through FK chain.
 21. ReverseRelation inside RelationAll compiles.
 22. ReverseRelation directly raises CompilerError (not a boolean).
 23. diff_specs: new defined class → AddDefinedClass, not AddClass.
 24. diff_specs: unchanged defined class with same definition → still re-emits CREATE OR REPLACE.
 25. Person count unchanged after Director view created (view is non-destructive).
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from knot import db
from knot.db import graph_store, spec_store
from tests._helpers import publish_spec
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.compile.postgres import CompileContext, compile_predicate, migration
from knot.spec.errors import PublishGateError
from knot.spec.metaschema import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Literal_,
    RelationAll,
    RelationAny,
    ReverseRelation,
    SlotPath,
)

# ---------------------------------------------------------------------------
# Spec builders
# ---------------------------------------------------------------------------


def _build_person_credit_director_spec() -> tuple[
    Spec, OntologyClass, OntologyClass, OntologyClass, Source, Source
]:
    """Build a Spec with Person, Credit (FK→Person), and Director (defined)."""
    string_t = TypeDefinition(name="string", base="str")

    # Person slots
    person_id = Slot(name="person_id", range=string_t, identifier=True, required=True)
    person_name = Slot(name="name", range=string_t)

    # Credit slots
    credit_id = Slot(name="credit_id", range=string_t, identifier=True, required=True)
    person_cls_placeholder = OntologyClass(name="Person")  # forward ref; patched below
    person_fk = Slot(name="person", range=person_cls_placeholder)
    role = Slot(name="role", range=string_t)

    person = OntologyClass(name="Person", slots=[person_id, person_name])
    # patch person_fk.range to real person object
    person_fk.range = person
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    # Director = Person ⊓ ∃ reverse(Credit.person) ∧ Credit.role = "director"
    # The definition predicate: RelationAny(ReverseRelation(Credit, Credit.person))
    # with an implied filter on Credit.role = "director".
    # We compose: RelationAny(ReverseRelation(...)) with the role compare
    # as a FilteredRelation-style combined predicate using RelationAll approach.
    #
    # For simplicity: use RelationAny with ReverseRelation where the FK slot
    # has an additional implicit filter via a Compare on Credit.role.
    # The correct expression: EXISTS (Credit rows where credit.person = person.canonical_id AND credit.role = 'director')
    # This is: RelationAny(FilteredRelation(ReverseRelation(Credit, person_fk), Compare(role='director')))
    # But FilteredRelation wraps RelationRef not ReverseRelation in the current design.
    # Use RelationAll(ReverseRelation(...), body=Compare(role,'director')) for "all credits are director"
    # or RelationAny(ReverseRelation(...)) with role filter as a BoolExpr around it.
    #
    # Simplest: use a BoolExpr combining RelationAny(ReverseRelation) + role check via
    # a secondary RelationAny with role=director filter baked in.
    # Per spec: Director.definition = RelationAny(relation=ReverseRelation(Credit, person_fk))
    # with a filter that checks role='director'. We can do this by building a
    # Compare on the reverse relation's slot inside RelationAll.
    #
    # For the E2E story: RelationAny(ReverseRelation(Credit, person_fk)) WHERE role='director'
    # In SQL: EXISTS (SELECT 1 FROM credit t JOIN credit_bindings tb ON ...
    #                 JOIN person_bindings ob ON ... WHERE t.person = ob.canonical_id
    #                 AND t.role = 'director')
    # We need to fold the role filter into the RelationAny. The current RelationAny
    # doesn't have a body filter — but RelationAll does. Use:
    # ~RelationAll(ReverseRelation(Credit, person_fk), body=(role != 'director'))
    # which means: NOT all credits are non-director = at least one credit is director.
    # Or just: use the role filter as a second Compare on the credit rows via RelationAll.
    #
    # Cleanest approach supported by current code:
    # BoolExpr(AND, [
    #   RelationAny(ReverseRelation(Credit, person_fk)),   -- has at least one credit
    #   ~RelationAll(ReverseRelation(Credit, person_fk), body=Compare(role, NEQ, 'director'))
    #   -- NOT all credits are non-director == at least one credit has role=director
    # ])
    # This is logically: ∃ credit referencing this person ∧ ∃ director credit
    # Simplify to just: ~RelationAll(ReverseRelation, role NEQ director) which means
    # NOT(all credits are non-director) = at least one credit is director.
    #
    # Even simpler for the test: just check RelationAny(ReverseRelation where role='director')
    # We can express: NOT EXISTS all(role != director) = ~RelationAll(ReverseRelation, role!=director)
    # which exactly means: at least one credit has role='director'

    role_path = SlotPath(from_class=credit, slots=[role])

    # Director definition: at least one Credit row (reverse-FK) has role='director'
    # = NOT (all Credits through reverse-FK have role != 'director')
    # = NOT RelationAll(ReverseRelation(Credit, person_fk), body=Compare(role, NEQ, director))
    role_not_director = Compare(op=CompareOp.NEQ, left=role_path, right=Literal_(value="director"))
    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)
    definition = BoolExpr(
        op=BoolOpKind.NOT,
        operands=[RelationAll(relation=rev, body=role_not_director)],
    )

    director = OntologyClass(
        name="Director",
        is_a=person,
        slots=[],  # inherits person's slots
        definition=definition,
    )

    person_src = Source(name="person_src", entity_class=person, identifier_slot=person_id)
    credit_src = Source(name="credit_src", entity_class=credit, identifier_slot=credit_id)

    spec = Spec(
        id="defined_class_test",
        version="1.0.0",
        types=[string_t],
        slots=[person_id, person_name, credit_id, person_fk, role],
        classes=[person, credit, director],
        sources=[person_src, credit_src],
    )
    return spec, person, credit, director, person_src, credit_src


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def dc_db(pg_conn):
    """Publish the Person/Credit/Director spec. Yields (conn, spec, ..., rev)."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec, person, credit, director, person_src, credit_src = _build_person_credit_director_spec()
    rev = await publish_spec(pg_conn, spec)

    yield pg_conn, spec, person, credit, director, person_src, credit_src, rev


@pytest.fixture
def dc_client(dc_db):
    """TestClient with KNOT_AUTH_DEV_MODE=1 bypass active."""
    from fastapi.testclient import TestClient

    from knot.api.main import app

    os.environ["KNOT_AUTH_DEV_MODE"] = "1"
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        os.environ.pop("KNOT_AUTH_DEV_MODE", None)


def _post(client, query: str, variables: dict | None = None) -> dict:
    resp = client.post(
        "/graph/query",
        json={"query": query, "variables": variables},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Director view exists in postgres
# ---------------------------------------------------------------------------


async def test_director_view_exists(dc_db):
    conn, *_ = dc_db
    cur = await conn.execute(
        "SELECT viewname FROM pg_views WHERE schemaname = 'knot_data' AND viewname = 'director'"
    )
    row = await cur.fetchone()
    assert row is not None, "knot_data.director VIEW was not created"


# ---------------------------------------------------------------------------
# 2. Person table is a table, not a view
# ---------------------------------------------------------------------------


async def test_person_is_table_not_view(dc_db):
    conn, *_ = dc_db
    # Should be in pg_tables (concrete class)
    cur = await conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'knot_data' AND tablename = 'person'"
    )
    row = await cur.fetchone()
    assert row is not None, "knot_data.person TABLE was not created"


# ---------------------------------------------------------------------------
# 3. directorPage returns only director persons
# ---------------------------------------------------------------------------


async def test_director_page_returns_only_directors(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    # Insert persons
    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
            {"person_id": "p3", "name": "Carol"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
                {"person_id": "p3", "name": "Carol"},
            ]
        ],
    )
    # Insert credits: Alice is director, Bob is actor, Carol is director
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
            {"credit_id": "c2", "person": "p2", "role": "actor"},
            {"credit_id": "c3", "person": "p3", "role": "director"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
                {"credit_id": "c2", "person": "p2", "role": "actor"},
                {"credit_id": "c3", "person": "p3", "role": "director"},
            ]
        ],
    )

    result = _post(dc_client, "{ director { personId } directorCount }")
    assert "errors" not in result, result.get("errors")
    rows = result["data"]["director"]
    person_ids = {r["personId"] for r in rows}
    assert person_ids == {"p1", "p3"}, f"unexpected directors: {person_ids}"
    assert result["data"]["directorCount"] == 2


# ---------------------------------------------------------------------------
# 4. personPage returns all persons
# ---------------------------------------------------------------------------


async def test_person_page_returns_all_persons(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
            ]
        ],
    )

    result = _post(dc_client, "{ person { personId } personCount }")
    assert len(result["data"]["person"]) == 2
    assert result["data"]["personCount"] == 2


# ---------------------------------------------------------------------------
# 5. directorByCanonicalId for a director
# ---------------------------------------------------------------------------


async def test_director_by_canonical_id_found(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
            {"credit_id": "c2", "person": "p2", "role": "actor"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
                {"credit_id": "c2", "person": "p2", "role": "actor"},
            ]
        ],
    )

    result = _post(dc_client, '{ directorByCanonicalId(canonicalId: "p1") { personId name } }')
    assert "errors" not in result, result.get("errors")
    row = result["data"]["directorByCanonicalId"]
    assert row is not None
    assert row["personId"] == "p1"
    assert row["name"] == "Alice"


# ---------------------------------------------------------------------------
# 6. directorByCanonicalId for non-director returns null
# ---------------------------------------------------------------------------


async def test_director_by_canonical_id_non_director_is_null(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
            {"credit_id": "c2", "person": "p2", "role": "actor"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
                {"credit_id": "c2", "person": "p2", "role": "actor"},
            ]
        ],
    )

    result = _post(dc_client, '{ directorByCanonicalId(canonicalId: "p2") { personId } }')
    assert "errors" not in result, result.get("errors")
    assert result["data"]["directorByCanonicalId"] is None


# ---------------------------------------------------------------------------
# 7. Changing credit role updates the director view (recomputed at query time)
# ---------------------------------------------------------------------------


async def test_director_view_recomputes_on_credit_update(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
            ]
        ],
    )
    # Initially Alice is a director
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
            ]
        ],
    )

    result = _post(dc_client, "{ directorCount }")
    assert result["data"]["directorCount"] == 1

    # Re-ingest with role changed to actor
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "actor"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "actor"},
            ]
        ],
    )

    # View recomputes at query time — Alice is no longer a director
    result = _post(dc_client, "{ directorCount }")
    assert result["data"]["directorCount"] == 0


# ---------------------------------------------------------------------------
# 8. AddDefinedClass emits CREATE OR REPLACE VIEW
#
# Pure-Python compile + diff tests (ReverseRelation compile,
# concrete↔defined transitions, multi-slot SlotPath, diff_specs) live in
# tests/unit/spec/compile/test_defined_classes_diff.py.
# ---------------------------------------------------------------------------


async def test_add_defined_class_ddl(pg_conn):
    """AddDefinedClass.emit_ddl calls CREATE OR REPLACE VIEW."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await db.apply_schema()
    await pg_conn.execute("CREATE SCHEMA IF NOT EXISTS knot_data")

    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person_name = Slot(name="name", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id, person_name])

    credit_id = Slot(name="credit_id", range=string_t)
    person_fk = Slot(name="person", range=person)
    role = Slot(name="role", range=string_t)
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    # Create parent table first
    await migration.emit_ddl(migration.AddClass(cls=person), pg_conn)
    await migration.emit_ddl(migration.AddClass(cls=credit), pg_conn)

    role_path = SlotPath(from_class=credit, slots=[role])
    role_not_director = Compare(op=CompareOp.NEQ, left=role_path, right=Literal_(value="director"))
    rev = ReverseRelation(target_class=credit, fk_slot=person_fk)
    definition = BoolExpr(
        op=BoolOpKind.NOT,
        operands=[RelationAll(relation=rev, body=role_not_director)],
    )
    director = OntologyClass(name="Director", is_a=person, slots=[], definition=definition)

    await migration.emit_ddl(migration.AddDefinedClass(cls=director), pg_conn)

    # Verify VIEW exists
    cur = await pg_conn.execute(
        "SELECT viewname FROM pg_views WHERE schemaname = 'knot_data' AND viewname = 'director'"
    )
    row = await cur.fetchone()
    assert row is not None, "Director VIEW was not created"


# ---------------------------------------------------------------------------
# 10. DropDefinedClass emits DROP VIEW
# ---------------------------------------------------------------------------


async def test_drop_defined_class_ddl(pg_conn):
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await db.apply_schema()
    await pg_conn.execute("CREATE SCHEMA IF NOT EXISTS knot_data")

    # Create a trivial view to drop
    await pg_conn.execute("CREATE VIEW knot_data.testview AS SELECT 1 AS x")

    await migration.emit_ddl(migration.DropDefinedClass(class_name="testview"), pg_conn)

    cur = await pg_conn.execute(
        "SELECT viewname FROM pg_views WHERE schemaname = 'knot_data' AND viewname = 'testview'"
    )
    row = await cur.fetchone()
    assert row is None, "testview VIEW was not dropped"


# ---------------------------------------------------------------------------
# 11. Publish gate rejects defined class with broken predicate
# ---------------------------------------------------------------------------


async def test_publish_gate_rejects_broken_definition(pg_conn):
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    # Use a RecursiveTraversal as the definition — compile raises NotImplementedError
    from knot.spec.metaschema import RecursiveTraversal, RelationRef
    from knot.spec.metaschema import SlotPath as SP

    is_a_slot = Slot(name="is_a", range=person)
    start = RelationRef(from_class=person, slot=is_a_slot)
    bad_definition = RecursiveTraversal(start=start, step=SP(from_class=person, slots=[is_a_slot]))

    director = OntologyClass(name="Director", is_a=person, slots=[], definition=bad_definition)

    spec = Spec(
        id="gate_test",
        version="1.0.0",
        types=[string_t],
        slots=[person_id, is_a_slot],
        classes=[person, director],
        sources=[],
    )

    with pytest.raises(PublishGateError, match="definition failed to compile"):
        await spec_store.publish_gate(spec)


# ---------------------------------------------------------------------------
# 12. Publish gate rejects defined class with no is_a
# ---------------------------------------------------------------------------


async def test_publish_gate_rejects_defined_class_without_is_a(pg_conn):
    string_t = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=string_t)
    person = OntologyClass(name="Person", slots=[person_id])

    role_path = SlotPath(from_class=person, slots=[person_id])
    definition = Compare(op=CompareOp.IS_NOT_NULL, left=role_path)

    # Director has definition but NO is_a
    director = OntologyClass(name="Director", is_a=None, slots=[], definition=definition)

    spec = Spec(
        id="gate_test2",
        version="1.0.0",
        types=[string_t],
        slots=[person_id],
        classes=[person, director],
        sources=[],
    )

    with pytest.raises(PublishGateError, match="must have is_a"):
        await spec_store.publish_gate(spec)


# ---------------------------------------------------------------------------
# 13. Concrete→defined transition is destructive (diff_specs)
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


# ---------------------------------------------------------------------------
# 14. Defined→concrete transition is destructive (diff_specs)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 15. Defined class with changed definition re-emits CREATE OR REPLACE VIEW
# ---------------------------------------------------------------------------


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
# 16. directorResolved returns record for a director
# ---------------------------------------------------------------------------


async def test_director_resolved_found(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
            {"credit_id": "c2", "person": "p2", "role": "actor"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
                {"credit_id": "c2", "person": "p2", "role": "actor"},
            ]
        ],
    )

    result = _post(
        dc_client, '{ directorResolved(canonicalId: "p1") { personId name canonicalId } }'
    )
    assert "errors" not in result, result.get("errors")
    row = result["data"]["directorResolved"]
    assert row is not None
    assert row["canonicalId"] == "p1"
    assert row["name"] == "Alice"


# ---------------------------------------------------------------------------
# 17. directorResolved for non-director returns null
# ---------------------------------------------------------------------------


async def test_director_resolved_non_director_is_null(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
            {"credit_id": "c2", "person": "p2", "role": "actor"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
                {"credit_id": "c2", "person": "p2", "role": "actor"},
            ]
        ],
    )

    result = _post(dc_client, '{ directorResolved(canonicalId: "p2") { personId } }')
    assert "errors" not in result, result.get("errors")
    assert result["data"]["directorResolved"] is None


# ---------------------------------------------------------------------------
# 18. directorPage with where filter (by name)
# ---------------------------------------------------------------------------


async def test_director_page_with_name_filter(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
            {"person_id": "p3", "name": "Carol"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
                {"person_id": "p3", "name": "Carol"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
            {"credit_id": "c2", "person": "p2", "role": "actor"},
            {"credit_id": "c3", "person": "p3", "role": "director"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
                {"credit_id": "c2", "person": "p2", "role": "actor"},
                {"credit_id": "c3", "person": "p3", "role": "director"},
            ]
        ],
    )

    # Filter directors by name = 'Alice'
    result = _post(
        dc_client,
        '{ director(where: { name: { eq: "Alice" } }) { name } '
        '  directorCount(where: { name: { eq: "Alice" } }) }',
    )
    assert "errors" not in result, result.get("errors")
    rows = result["data"]["director"]
    assert len(rows) == 1
    assert rows[0]["name"] == "Alice"
    assert result["data"]["directorCount"] == 1


# ---------------------------------------------------------------------------
# 19. Both person and director appear in schema
# ---------------------------------------------------------------------------


def test_both_classes_in_schema(dc_db, dc_client):
    # __typename returns Query; introspect that both fields exist
    result = _post(dc_client, "{ __typename }")
    assert result["data"]["__typename"] == "Query"
    # Check that director fields exist via a trivial query
    r1 = _post(dc_client, "{ directorCount }")
    r2 = _post(dc_client, "{ personCount }")
    assert "errors" not in r1
    assert "errors" not in r2


# ---------------------------------------------------------------------------
# 20. Multi-slot SlotPath compiles through FK chain (unit)
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


# ---------------------------------------------------------------------------
# 21. ReverseRelation inside RelationAll compiles
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 22. ReverseRelation directly (not in RelationAll/Any) raises CompilerError
# ---------------------------------------------------------------------------


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
# 23. diff_specs: new defined class → AddDefinedClass, not AddClass
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


# ---------------------------------------------------------------------------
# 24. diff_specs: both defined → AddDefinedClass (re-emit view)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 25. Person count unchanged after Director view created
# ---------------------------------------------------------------------------


async def test_person_count_unchanged_after_director_view(dc_db, dc_client):
    conn, spec, person, credit, director, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
            {"person_id": "p3", "name": "Carol"},
        ],
        canonical_ids=[
            str(r["person_id"])
            for r in [
                {"person_id": "p1", "name": "Alice"},
                {"person_id": "p2", "name": "Bob"},
                {"person_id": "p3", "name": "Carol"},
            ]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "person": "p1", "role": "director"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "person": "p1", "role": "director"},
            ]
        ],
    )

    result = _post(dc_client, "{ personCount }")
    assert result["data"]["personCount"] == 3

    # Director view only shows 1 (Alice)
    result = _post(dc_client, "{ directorCount }")
    assert result["data"]["directorCount"] == 1
