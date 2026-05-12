"""Tests for FK-relation classes and ReverseRelation compile.

End-to-end story: Person → Credit (FK: Credit.person → Person canonical_id)

Coverage
--------
Integration (DB):
  1. Publish spec with Person/Credit; verify tables created.
  2. Insert persons + credits, query via GraphQL.
  3. personPage → all persons.
  4. ReverseRelation compile correctness (unit).
  5. Multi-slot SlotPath compile through FK chain (unit).
  6. ReverseRelation inside RelationAll compiles (unit).
  7. ReverseRelation directly raises NotImplementedError (unit).
  8. diff_specs: AddClass for normal class.
  9. diff_specs: DropClass for removed class.
 10. Both concrete classes appear in schema (personPage + creditPage).
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from knot import db
from knot.db import graph_store, spec_store
from tests._helpers import publish_spec
from knot.spec import OntologyClass, Slot, Source, Spec
from knot.spec.metaschema import ClassRef, Primitive
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
# Spec builders
# ---------------------------------------------------------------------------


def _build_person_credit_spec() -> tuple[
    Spec, OntologyClass, OntologyClass, Source, Source
]:
    """Build a Spec with Person and Credit (FK→Person)."""
    # Person slots
    person_id = Slot(name="person_id", type=Primitive(name="string"), identifier=True, required=True)
    person_name = Slot(name="name", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id, person_name])

    # Credit slots — person FK stored as canonical_id TEXT
    credit_id = Slot(name="credit_id", type=Primitive(name="string"), identifier=True, required=True)
    person_fk = Slot(name="person", type=ClassRef(target_class=person))
    role = Slot(name="role", type=Primitive(name="string"))
    credit = OntologyClass(name="Credit", slots=[credit_id, person_fk, role])

    person_src = Source(name="person_src", entity_class=person, identifier_slot=person_id)
    credit_src = Source(name="credit_src", entity_class=credit, identifier_slot=credit_id)

    spec = Spec(
        id="defined_class_test",
        version="1.0.0",
        slots=[person_id, person_name, credit_id, person_fk, role],
        classes=[person, credit],
        sources=[person_src, credit_src],
    )
    return spec, person, credit, person_src, credit_src


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def dc_db(pg_conn):
    """Publish the Person/Credit spec. Yields (conn, spec, ..., rev)."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec, person, credit, person_src, credit_src = _build_person_credit_spec()
    rev = await publish_spec(pg_conn, spec)

    yield pg_conn, spec, person, credit, person_src, credit_src, rev


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
# 1. Tables exist in postgres
# ---------------------------------------------------------------------------


async def test_person_table_created(dc_db):
    conn, *_ = dc_db
    cur = await conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'knot_data' AND tablename = 'person'"
    )
    row = await cur.fetchone()
    assert row is not None, "knot_data.person TABLE was not created"


async def test_credit_table_created(dc_db):
    conn, *_ = dc_db
    cur = await conn.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'knot_data' AND tablename = 'credit'"
    )
    row = await cur.fetchone()
    assert row is not None, "knot_data.credit TABLE was not created"


# ---------------------------------------------------------------------------
# 2. Insert and query rows via GraphQL
# ---------------------------------------------------------------------------


async def test_person_page_returns_inserted_rows(dc_db, dc_client):
    conn, spec, person, credit, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=["p1", "p2"],
    )

    result = _post(dc_client, "{ person { personId name } personCount }")
    assert "errors" not in result, result.get("errors")
    assert result["data"]["personCount"] == 2
    names = {r["name"] for r in result["data"]["person"]}
    assert names == {"Alice", "Bob"}


# ---------------------------------------------------------------------------
# 3. personPage returns all persons
# ---------------------------------------------------------------------------


async def test_person_page_returns_all_persons(dc_db, dc_client):
    conn, spec, person, credit, person_src, credit_src, rev = dc_db

    await graph_store.insert_rows(
        conn,
        source=person_src,
        spec_revision=rev,
        rows=[
            {"person_id": "p1", "name": "Alice"},
            {"person_id": "p2", "name": "Bob"},
        ],
        canonical_ids=["p1", "p2"],
    )
    result = _post(dc_client, "{ person { personId } personCount }")
    assert len(result["data"]["person"]) == 2
    assert result["data"]["personCount"] == 2


# ---------------------------------------------------------------------------
# 4. ReverseRelation compile correctness (unit)
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


# ---------------------------------------------------------------------------
# 5. Multi-slot SlotPath compile through FK chain (unit)
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

    assert len(ctx.joins) >= 1, "Expected JOIN fragments from multi-slot path"
    join_str = ctx.joins[0].as_string(None)
    assert "person" in join_str.lower(), f"Expected 'person' in JOIN: {join_str}"


# ---------------------------------------------------------------------------
# 6. ReverseRelation inside RelationAll compiles (unit)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 7. ReverseRelation directly raises NotImplementedError (unit)
# ---------------------------------------------------------------------------


def test_reverse_relation_direct_raises():
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
# 8. diff_specs: AddClass for new class (unit)
# ---------------------------------------------------------------------------


def test_diff_specs_add_class():
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])

    prev_spec = Spec(id="t", version="1", slots=[person_id], classes=[person], sources=[])
    director = OntologyClass(name="Director", is_a=person, slots=[])
    cand_spec = Spec(
        id="t",
        version="1",
        slots=[person_id],
        classes=[person, director],
        sources=[],
    )

    changes = migration.diff_specs(prev_spec, cand_spec)
    add_class = [c for c in changes if isinstance(c, migration.AddClass) and c.cls.name == "Director"]
    assert add_class, f"Expected AddClass for Director; got {changes}"


# ---------------------------------------------------------------------------
# 9. diff_specs: DropClass for removed class (unit)
# ---------------------------------------------------------------------------


def test_diff_specs_drop_class():
    person_id = Slot(name="person_id", type=Primitive(name="string"))
    person = OntologyClass(name="Person", slots=[person_id])
    director = OntologyClass(name="Director", is_a=person, slots=[])

    prev_spec = Spec(
        id="t",
        version="1",
        slots=[person_id],
        classes=[person, director],
        sources=[],
    )
    cand_spec = Spec(id="t", version="1", slots=[person_id], classes=[person], sources=[])

    changes = migration.diff_specs(prev_spec, cand_spec)
    drop_class = [c for c in changes if isinstance(c, migration.DropClass) and c.class_name == "Director"]
    assert drop_class, f"Expected DropClass for Director; got {changes}"


# ---------------------------------------------------------------------------
# 10. Both concrete classes appear in schema
# ---------------------------------------------------------------------------


def test_both_classes_in_schema(dc_db, dc_client):
    result = _post(dc_client, "{ __typename }")
    assert result["data"]["__typename"] == "Query"
    r1 = _post(dc_client, "{ personCount }")
    r2 = _post(dc_client, "{ creditCount }")
    assert "errors" not in r1
    assert "errors" not in r2
