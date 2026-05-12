"""Integration tests for GET/POST /spec/graphql.

The spec metaschema is static (typed Pydantic entities — commitment 2),
so the spec GraphQL schema is also static, defined once in
``knot.api.spec_graphql``.  These tests exercise the wire-level surface:

  - GET serves Strawberry's bundled GraphiQL HTML.
  - POST with no published spec returns ``{"data": {"publishedSpec": null}}``.
  - POST returns the full spec shape (classes with inline slots/sources/constraints).
  - Selective field projection works (only ``classes { name }``).
"""

from __future__ import annotations

import pytest_asyncio
from fastapi.testclient import TestClient

from knot import db
from knot.spec import OntologyClass, Slot, Source, Spec
from knot.spec.metaschema import (
    ClassRef,
    Constraint,
    Primitive,
    Severity,
    SlotConstraints,
    SourceBinding,
)
from tests._helpers import publish_spec


def _client() -> TestClient:
    from knot.api.main import app

    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Spec builders
# ---------------------------------------------------------------------------


def _build_spec() -> Spec:
    """Movie + Person spec with two sources and a trivial constraint —
    exercises every entity kind the GraphQL schema surfaces.
    """
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"), required=True)
    year = Slot(
        name="year",
        type=Primitive(name="integer"),
        constraints=SlotConstraints(min_value=1888.0, max_value=2100.0),
    )
    person_id = Slot(
        name="person_id", type=Primitive(name="string"), identifier=True, required=True
    )
    name = Slot(name="name", type=Primitive(name="string"), required=True)

    person = OntologyClass(
        name="Person",
        slots=[person_id, name],
        description="A human associated with a Movie.",
    )
    directed_by = Slot(name="directed_by", type=ClassRef(target_class=person))
    movie = OntologyClass(
        name="Movie",
        slots=[imdb_id, title, year, directed_by],
        description="A theatrical motion picture.",
    )

    imdb_src = Source(name="imdb", description="IMDb data feed.")
    wiki_src = Source(name="wiki")
    imdb_binding = SourceBinding(source=imdb_src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    wiki_binding = SourceBinding(source=wiki_src, class_=person, identifier_slot=person_id)  # type: ignore[call-arg]

    # Constraint: year must be > 1900 (toy invariant exercising surface).
    year_check = Constraint(
        name="movie_year_after_1900",
        primary=movie,
        body="year > 1900",
        severity=Severity.WARNING,
        message="Movie year should be after 1900.",
    )

    return Spec(
        id="spec_graphql_test",
        version="1.2.3",
        classes=[person, movie],
        sources=[imdb_src, wiki_src],
        source_bindings=[imdb_binding, wiki_binding],
        constraints=[year_check],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def empty_state(pg_conn):
    """Drop everything so no spec is published — for the null-response test."""
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await db.apply_schema()
    yield
    # Re-apply for the next test in the suite.
    await db.apply_schema()


@pytest_asyncio.fixture
async def published(pg_conn):
    """Publish the demo spec; yield (rev, spec)."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec = _build_spec()
    rev = await publish_spec(pg_conn, spec)
    yield rev, spec


# ---------------------------------------------------------------------------
# 1. GraphiQL UI
# ---------------------------------------------------------------------------


def test_graphiql_ui_served():
    """GET /spec/graphql returns 200 with HTML containing 'GraphiQL'."""
    resp = _client().get("/spec/graphql")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "GraphiQL" in resp.text


# ---------------------------------------------------------------------------
# 2. No published spec → publishedSpec: null
# ---------------------------------------------------------------------------


def test_published_spec_returns_null_when_no_publish(empty_state):
    resp = _client().post(
        "/spec/graphql",
        json={"query": "{ publishedSpec { id version } }"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body.get("errors")
    assert body["data"] == {"publishedSpec": None}


# ---------------------------------------------------------------------------
# 3. Full shape — every entity kind round-trips through the schema
# ---------------------------------------------------------------------------


def test_published_spec_returns_full_shape(published):
    rev, spec = published
    query = """
    {
      publishedSpec {
        id
        version
        revision
        contentHash
        classes {
          name abstract description isAName mixinNames
          slots {
            name identifier required resolutionPolicy
            typeKind typeName minimumValue maximumValue
            permissibleValues
          }
          effectiveSlots { name }
        }
        sources { name description }
        sourceBindings { sourceName className identifierSlotName description }
        constraints { name primaryClassName severity message }
      }
    }
    """
    resp = _client().post("/spec/graphql", json={"query": query})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body.get("errors")
    ps = body["data"]["publishedSpec"]
    assert ps is not None
    assert ps["id"] == "spec_graphql_test"
    assert ps["version"] == "1.2.3"
    assert ps["revision"] == rev
    assert ps["contentHash"]  # non-empty

    # Classes — slots are inline
    classes_by_name = {c["name"]: c for c in ps["classes"]}
    assert set(classes_by_name) == {"Person", "Movie"}

    movie = classes_by_name["Movie"]
    assert movie["isAName"] is None
    assert movie["mixinNames"] == []
    assert movie["abstract"] is False
    movie_slots_by_name = {s["name"]: s for s in movie["slots"]}
    assert set(movie_slots_by_name) == {"imdb_id", "title", "year", "directed_by"}

    year_slot = movie_slots_by_name["year"]
    assert year_slot["typeKind"] == "primitive"
    assert year_slot["typeName"] == "integer"
    assert year_slot["minimumValue"] == 1888.0
    assert year_slot["maximumValue"] == 2100.0

    directed_by = movie_slots_by_name["directed_by"]
    assert directed_by["typeKind"] == "class"
    assert directed_by["typeName"] == "Person"

    imdb_id_slot = movie_slots_by_name["imdb_id"]
    assert imdb_id_slot["identifier"] is True
    assert imdb_id_slot["required"] is True

    person = classes_by_name["Person"]
    person_slots_by_name = {s["name"]: s for s in person["slots"]}
    assert set(person_slots_by_name) == {"person_id", "name"}

    # Sources (slim — just name + description)
    sources_by_name = {s["name"]: s for s in ps["sources"]}
    assert set(sources_by_name) == {"imdb", "wiki"}
    assert sources_by_name["imdb"]["description"] == "IMDb data feed."

    # SourceBindings carry class/slot/trust metadata
    bindings_by_source = {b["sourceName"]: b for b in ps["sourceBindings"]}
    assert set(bindings_by_source) == {"imdb", "wiki"}
    imdb_b = bindings_by_source["imdb"]
    assert imdb_b["className"] == "Movie"
    assert imdb_b["identifierSlotName"] == "imdb_id"
    assert imdb_b["description"] is None

    # Constraints
    assert len(ps["constraints"]) == 1
    cn = ps["constraints"][0]
    assert cn["name"] == "movie_year_after_1900"
    assert cn["primaryClassName"] == "Movie"
    assert cn["severity"] == "warning"
    assert cn["message"] == "Movie year should be after 1900."


# ---------------------------------------------------------------------------
# 4. Selective field query — GraphQL returns only requested fields
# ---------------------------------------------------------------------------


def test_query_just_class_names(published):
    """Only the requested fields come back — proves field-level projection."""
    resp = _client().post(
        "/spec/graphql",
        json={"query": "{ publishedSpec { classes { name } } }"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body.get("errors")
    ps = body["data"]["publishedSpec"]
    assert ps == {"classes": [{"name": "Person"}, {"name": "Movie"}]}
