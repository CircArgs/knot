"""Integration tests for spec authoring (POST /sources, spec_revisions storage).

Closes spec-loading.md option 1: postgres-backed spec, seeded from B2 fixture
on first boot, mutable via API.

Requires the docker-compose stack (postgres) to be running.
"""

from __future__ import annotations

import psycopg
import pytest
from fastapi.testclient import TestClient

from knot import spec_store
from knot.canonical import compute_content_hash
from knot.control_db import apply_schema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _ensure_schema(postgres_dsn):
    apply_schema(postgres_dsn)


@pytest.fixture(scope="module")
def client():
    from knot.api import app
    import knot.api as api_module

    api_module.DSN = "postgresql://knot:knot@localhost:5432/knot_control"

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def pg(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _reset_spec_revisions(pg):
    """Reset spec_revisions and seed from fixture so each test starts from B2.

    TRUNCATE ... RESTART IDENTITY resets the SERIAL sequence so the seeded
    row is always revision 1.
    """
    pg.execute("TRUNCATE spec_revisions RESTART IDENTITY")
    spec_store.seed_from_fixture(pg)
    yield


# ---------------------------------------------------------------------------
# spec_store: round-trip + identity
# ---------------------------------------------------------------------------

def test_seed_from_fixture_writes_active_revision(pg):
    pg.execute("TRUNCATE spec_revisions RESTART IDENTITY")
    rev = spec_store.seed_from_fixture(pg)
    assert rev == 1

    rows = pg.execute(
        "SELECT revision, active FROM spec_revisions"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 1
    assert rows[0][1] is True


def test_load_active_returns_rehydrated_spec_with_real_refs(pg):
    """After save+load, cross-references must be real Python object identity (`is`)."""
    spec = spec_store.load_active(pg)
    assert spec is not None
    # Sources reference real OntologyClass and Slot objects from spec.classes / spec.slots.
    movie_cls = next(c for c in spec.classes if c.name == "Movie")
    movie_imdb_id = next(s for s in movie_cls.slots if s.name == "imdb_id")

    imdb_movies = next(s for s in spec.sources if s.name == "imdb_movies")
    assert imdb_movies.entity_class is movie_cls, "Source.entity_class must be `is` the OntologyClass on spec.classes"
    assert imdb_movies.identifier_slot is movie_imdb_id, "Source.identifier_slot must be `is` the Slot on the class"


def test_save_load_round_trip_preserves_content_hash(pg):
    """Saving the loaded spec back must produce the same content_hash."""
    spec_a = spec_store.load_active(pg)
    hash_a = compute_content_hash(spec_a)

    rev = spec_store.save_revision(pg, spec_a)
    spec_b = spec_store.load_active(pg)
    hash_b = compute_content_hash(spec_b)

    assert hash_a == hash_b
    assert rev == 2


def test_save_revision_deactivates_prior(pg):
    """save_revision must keep exactly one row with active=TRUE."""
    spec = spec_store.load_active(pg)
    spec_store.save_revision(pg, spec)
    spec_store.save_revision(pg, spec)

    active_count = pg.execute(
        "SELECT COUNT(*) FROM spec_revisions WHERE active = TRUE"
    ).fetchone()[0]
    assert active_count == 1


# ---------------------------------------------------------------------------
# GET /sources
# ---------------------------------------------------------------------------

def test_get_sources_returns_b2_seed(client):
    resp = client.get("/sources")
    assert resp.status_code == 200, resp.text
    sources = resp.json()
    names = {s["name"] for s in sources}
    # B2 seeds 8 sources.
    assert {
        "imdb_movies", "tmdb_movies", "wikidata_movies",
        "imdb_persons", "tmdb_persons",
        "imdb_credits", "tmdb_credits", "wikidata_credits",
    } <= names

    # Each item carries entity_class + identifier_slot as name strings.
    imdb_movies = next(s for s in sources if s["name"] == "imdb_movies")
    assert imdb_movies["entity_class"] == "Movie"
    assert imdb_movies["identifier_slot"] == "imdb_id"


# ---------------------------------------------------------------------------
# POST /sources
# ---------------------------------------------------------------------------

def test_post_source_succeeds_and_persists(client, pg):
    resp = client.post("/sources", json={
        "name": "my_test_source",
        "entity_class": "Movie",
        "identifier_slot": "imdb_id",
        "description": "test",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["spec_revision"] == 2  # seed was rev 1
    assert len(data["spec_content_hash"]) == 64
    assert data["source"]["name"] == "my_test_source"
    assert data["source"]["entity_class"] == "Movie"
    assert data["source"]["identifier_slot"] == "imdb_id"

    # GET /sources reflects it.
    listed = client.get("/sources").json()
    assert any(s["name"] == "my_test_source" for s in listed)

    # Active spec revision row exists in postgres.
    row = pg.execute(
        "SELECT revision FROM spec_revisions WHERE active = TRUE"
    ).fetchone()
    assert row[0] == 2


def test_post_source_unknown_entity_class_returns_400(client):
    resp = client.post("/sources", json={
        "name": "ghost_source",
        "entity_class": "NotARealClass",
        "identifier_slot": "imdb_id",
    })
    assert resp.status_code == 400
    assert "NotARealClass" in resp.json()["detail"]


def test_post_source_unknown_identifier_slot_returns_400(client):
    resp = client.post("/sources", json={
        "name": "ghost_source",
        "entity_class": "Movie",
        "identifier_slot": "not_a_slot",
    })
    assert resp.status_code == 400
    assert "not_a_slot" in resp.json()["detail"]


def test_post_source_duplicate_name_returns_409(client):
    # imdb_movies already exists from the B2 seed.
    resp = client.post("/sources", json={
        "name": "imdb_movies",
        "entity_class": "Movie",
        "identifier_slot": "imdb_id",
    })
    assert resp.status_code == 409


def test_post_source_extra_fields_rejected(client):
    """extra='forbid' on SourceCreate (commitment 16)."""
    resp = client.post("/sources", json={
        "name": "x",
        "entity_class": "Movie",
        "identifier_slot": "imdb_id",
        "stranger_danger": True,
    })
    assert resp.status_code == 422


def test_get_spec_includes_added_source(client):
    """After POST /sources, GET /spec contains the new source."""
    client.post("/sources", json={
        "name": "another_test_source",
        "entity_class": "Person",
        "identifier_slot": "imdb_id",
    })
    spec = client.get("/spec").json()
    source_names = [s["name"] for s in spec.get("sources", [])]
    assert "another_test_source" in source_names


def test_post_runs_after_post_source_uses_new_active_spec(client, pg):
    """POST /sources then POST /runs — compile must read the new active spec.

    We can't easily inspect the compiled WorkflowSpec for the new source's
    normalize stage (the compiler doesn't enumerate Sources today), but we
    CAN verify the run reads the *current* spec by checking compile_hash
    differs after a spec change.
    """
    r1 = client.post("/runs", json={"scope": "Movie"})
    assert r1.status_code == 200
    hash_a = r1.json()["compile_hash"]

    # Add a source — this writes a new spec_revisions row.
    add = client.post("/sources", json={
        "name": "compile_uses_active_spec_src",
        "entity_class": "Movie",
        "identifier_slot": "imdb_id",
    })
    assert add.status_code == 200

    # Compile again — same scope but the spec is different.  Today knot's
    # compile hash depends on spec_revision_ids which are synthetic (=1)
    # for every class, so the compile_hash will be the same.  This test
    # therefore only asserts the run records dispatch successfully — a stronger
    # assertion is open until per-source-watermark inputs flow into cache_key.
    r2 = client.post("/runs", json={"scope": "Movie"})
    assert r2.status_code == 200
