"""Lake materialization SQL endpoint tests.

knot generates SELECT bodies for current and history views per
materializable class. This file verifies:

  1. Endpoint returns one entry per concrete class.
  2. Generated SQL is syntactically valid (we EXPLAIN it against postgres).
  3. ``current`` body filters to valid_to IS NULL; ``history`` does not.
  4. Defined classes (views) and abstract classes are skipped.
  5. Generated current-body returns the right number of rows after ingest.
  6. ?class=X filter works; unknown class returns 404.
  7. /lake/materialize requires a published spec (409 otherwise).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from knot import db
from knot.api.main import app
from knot.api.auth.security import Principal, require_user
from knot.db import spec_store
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _spec_with_concrete_and_abstract() -> Spec:
    """One concrete Movie source class + one abstract Auditable class."""
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")

    audited_at = Slot(name="audited_at", range=st)
    auditable = OntologyClass(
        name="Auditable",
        slots=[audited_at],
        abstract=True,
    )

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    year = Slot(name="year", range=it)
    movie = OntologyClass(
        name="Movie",
        slots=[imdb_id, title, year],
        mixins=[auditable],
    )
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id="lake_test", version="1.0.0",
        types=[st, it],
        slots=[imdb_id, title, year, audited_at],
        classes=[movie, auditable],
        sources=[src],
    )


@pytest_asyncio.fixture
async def clean(pg_conn):
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE dq_observations CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()
    yield pg_conn


@pytest.fixture
def client():
    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


@pytest_asyncio.fixture
async def published(clean):
    rev = await create_draft(clean)
    await update_draft(clean, rev, _spec_with_concrete_and_abstract())
    await publish_draft(clean, rev)
    yield clean, rev


# ---------------------------------------------------------------------------
# 1. Endpoint shape: one entry per concrete class
# ---------------------------------------------------------------------------

def test_materialize_lists_concrete_classes(published, client):
    conn, rev = published
    r = client.get("/lake/materialize")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spec_revision"] == rev
    names = {c["name"] for c in body["classes"]}
    assert names == {"Movie"}  # Auditable is abstract — skipped.
    movie = next(c for c in body["classes"] if c["name"] == "Movie")
    assert "current" in movie
    assert "history" in movie


# ---------------------------------------------------------------------------
# 2. Generated SQL is syntactically valid (EXPLAIN it)
# ---------------------------------------------------------------------------

async def test_generated_sql_is_valid(published, client):
    conn, rev = published
    r = client.get("/lake/materialize")
    movie = next(c for c in r.json()["classes"] if c["name"] == "Movie")

    # EXPLAIN runs the planner without executing. Syntax errors → exception.
    await conn.execute(f"EXPLAIN {movie['current']}")
    await conn.execute(f"EXPLAIN {movie['history']}")


# ---------------------------------------------------------------------------
# 3. current filters to valid_to IS NULL; history does not
# ---------------------------------------------------------------------------

def test_current_filters_history_does_not(published, client):
    conn, rev = published
    r = client.get("/lake/materialize")
    movie = next(c for c in r.json()["classes"] if c["name"] == "Movie")
    assert "valid_to IS NULL" in movie["current"]
    assert "valid_to IS NULL" not in movie["history"]
    # History exposes columns that current doesn't need.
    assert "valid_to" in movie["history"]
    assert "change_type" in movie["history"]


# ---------------------------------------------------------------------------
# 4. Generated current-body returns the right rows after ingest
# ---------------------------------------------------------------------------

async def test_current_body_runs_against_real_data(published, client):
    conn, rev = published
    r = client.post(
        "/graph/ingest/imdb",
        json={
            "rows": [
                {"imdb_id": "tt1", "title": "Shawshank", "year": 1994},
                {"imdb_id": "tt2", "title": "Godfather", "year": 1972},
            ],
        },
    )
    assert r.status_code == 200, r.text

    r = client.get("/lake/materialize?class=Movie")
    assert r.status_code == 200, r.text
    movie = r.json()["classes"][0]

    cur = await conn.execute(movie["current"])
    rows = await cur.fetchall()
    canonical_ids = {row[0] for row in rows}
    assert canonical_ids == {"tt1", "tt2"}


# ---------------------------------------------------------------------------
# 5. Mixin slot appears in the generated SELECT (audited_at)
# ---------------------------------------------------------------------------

def test_mixin_slot_in_materialized_view(published, client):
    r = client.get("/lake/materialize?class=Movie")
    movie = r.json()["classes"][0]
    assert "audited_at" in movie["current"]
    assert "audited_at" in movie["history"]


# ---------------------------------------------------------------------------
# 6. Unknown class → 404
# ---------------------------------------------------------------------------

def test_materialize_unknown_class_404(published, client):
    r = client.get("/lake/materialize?class=DoesNotExist")
    assert r.status_code == 404
    assert "not on the published spec" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 7. No published spec → 409
# ---------------------------------------------------------------------------

def test_materialize_without_published_spec_409(clean, client):
    r = client.get("/lake/materialize")
    assert r.status_code == 409
    assert "no spec is published" in r.json()["detail"].lower()
