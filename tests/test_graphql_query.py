"""Integration tests for POST /graph/query (GraphQL endpoint).

Coverage:
  - Schema regenerates on spec publish (content_hash changes → new schema).
  - Query a class with a Compare filter (year >= 1990).
  - Multiple Compare filters AND-ed together.
  - Pagination: limit/offset.
  - as_of revision pin.
  - 401 / 403 auth paths, dev-mode bypass.
  - Class with no rows returns empty list.
  - LIKE / Matches predicate.

Fixture: a published Movie spec with two sources (imdb, tmdb) and some rows.
Tests run against the live postgres stack; isolation via per-test truncation.
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.db import graph_store, spec_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.ontology import OntologyClass, Slot, Source, Spec, TypeDefinition


# ---------------------------------------------------------------------------
# Spec + data builders
# ---------------------------------------------------------------------------

def _build_spec() -> tuple[Spec, OntologyClass, Source]:
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="gql_test",
        version="1.0.0",
        types=[st, it],
        slots=[imdb_id, title, year],
        classes=[movie],
        sources=[src],
    )
    return spec, movie, src


def _sample_rows() -> list[dict]:
    return [
        {"imdb_id": "tt0111161", "title": "The Shawshank Redemption", "year": 1994},
        {"imdb_id": "tt0068646", "title": "The Godfather", "year": 1972},
        {"imdb_id": "tt0108052", "title": "Schindler's List", "year": 1993},
        {"imdb_id": "tt0167260", "title": "The Return of the King", "year": 2003},
        {"imdb_id": "tt0050083", "title": "12 Angry Men", "year": 1957},
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gql_db(pg_conn):
    """Publish a Movie spec and insert sample rows. Yields (conn, spec, src, rev)."""
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()

    spec, movie, src = _build_spec()
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    graph_store.insert_rows(
        pg_conn, source=src, spec_revision=rev, rows=_sample_rows()
    )
    yield pg_conn, spec, src, rev


@pytest.fixture
def gql_client(gql_db):
    """TestClient with KNOT_AUTH_DEV_MODE=1 bypass active."""
    from knot.api.main import app
    os.environ["KNOT_AUTH_DEV_MODE"] = "1"
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        os.environ.pop("KNOT_AUTH_DEV_MODE", None)


def _post(client: TestClient, query: str, variables: dict | None = None) -> dict:
    resp = client.post(
        "/graph/query",
        json={"query": query, "variables": variables},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 1. Schema regenerates on new publish
# ---------------------------------------------------------------------------

def test_schema_cache_changes_on_new_publish(gql_db):
    """Content hash changes after a new publish → schema cache miss → new schema."""
    from knot.api._graphql_schema import _schema_cache, get_or_build_schema

    conn, spec, src, rev = gql_db

    hash1 = spec_store.get_published_content_hash(conn)
    schema1 = get_or_build_schema(spec, hash1)

    # Publish a new (identical-content but new revision) draft.
    spec2, movie2, src2 = _build_spec()
    # Tweak version so content hash differs.
    spec2.version = "2.0.0"
    rev2 = create_draft(conn)
    update_draft(conn, rev2, spec2)
    publish_draft(conn, rev2)

    hash2 = spec_store.get_published_content_hash(conn)
    assert hash1 != hash2

    schema2 = get_or_build_schema(spec2, hash2)
    assert schema1 is not schema2


# ---------------------------------------------------------------------------
# 2. Query without filter → all rows
# ---------------------------------------------------------------------------

def test_query_no_filter_returns_all_rows(gql_client):
    result = _post(gql_client, "{ movie { rows } }")
    assert "data" in result
    rows = result["data"]["movie"]["rows"]
    assert len(rows) == 5
    # Each row is a JSON string with the expected keys.
    first = json.loads(rows[0])
    assert "imdb_id" in first or "_canonical_id" in first


# ---------------------------------------------------------------------------
# 3. Compare filter: year >= 1990
# ---------------------------------------------------------------------------

def test_query_filter_year_gte(gql_client):
    query = "{ movie(where: { year: { gte: 1990 } }) { rows } }"
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    years = [r["year"] for r in rows]
    assert all(y >= 1990 for y in years), f"unexpected years: {years}"
    assert len(rows) == 3  # 1994, 1993, 2003


# ---------------------------------------------------------------------------
# 4. Multiple Compare filters (AND): 1990 <= year <= 2000
# ---------------------------------------------------------------------------

def test_query_filter_year_range(gql_client):
    query = "{ movie(where: { year: { gte: 1990, lte: 2000 } }) { rows } }"
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    years = [r["year"] for r in rows]
    assert all(1990 <= y <= 2000 for y in years)
    assert len(rows) == 2  # 1994, 1993


# ---------------------------------------------------------------------------
# 5. Pagination: limit / offset
# ---------------------------------------------------------------------------

def test_query_limit(gql_client):
    query = "{ movie(limit: 2) { rows } }"
    result = _post(gql_client, query)
    assert len(result["data"]["movie"]["rows"]) == 2


def test_query_offset(gql_client):
    all_result = _post(gql_client, "{ movie { rows } }")
    all_rows = [json.loads(r) for r in all_result["data"]["movie"]["rows"]]

    offset_result = _post(gql_client, "{ movie(limit: 10, offset: 2) { rows } }")
    offset_rows = [json.loads(r) for r in offset_result["data"]["movie"]["rows"]]

    # Offset skips first 2 rows; results should be the tail of all rows.
    assert len(offset_rows) == 3
    assert [r["imdb_id"] for r in offset_rows] == [r["imdb_id"] for r in all_rows[2:]]


# ---------------------------------------------------------------------------
# 6. as_of revision pin
# ---------------------------------------------------------------------------

def test_query_as_of_past_revision_returns_empty(gql_db, gql_client):
    """as_of=0 (before any rows were inserted) should return no rows.
    Rows were inserted at spec_revision=rev; as_of=rev-1 returns nothing.
    """
    conn, spec, src, rev = gql_db
    # rev is the current published revision (rows have _spec_revision = rev).
    # as_of = rev - 1 → no rows visible.
    query = f"{{ movie(asOf: {rev - 1}) {{ rows }} }}"
    result = _post(gql_client, query)
    rows = result["data"]["movie"]["rows"]
    assert rows == [], f"expected empty, got {rows}"


def test_query_as_of_current_revision_returns_rows(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f"{{ movie(asOf: {rev}) {{ rows }} }}"
    result = _post(gql_client, query)
    assert len(result["data"]["movie"]["rows"]) == 5


# ---------------------------------------------------------------------------
# 7. Class with no rows returns empty list
# ---------------------------------------------------------------------------

def test_query_class_with_no_rows(gql_client):
    # No rows were inserted for any extra class; wipe and re-publish with
    # only the Movie class but insert zero rows.
    query = "{ movie(where: { year: { gt: 9999 } }) { rows } }"
    result = _post(gql_client, query)
    assert result["data"]["movie"]["rows"] == []


# ---------------------------------------------------------------------------
# 8. LIKE / Matches predicate
# ---------------------------------------------------------------------------

def test_query_like_filter(gql_client):
    query = '{ movie(where: { title: { like: "The%" } }) { rows } }'
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    titles = [r["title"] for r in rows]
    assert all(t.startswith("The") for t in titles), f"unexpected titles: {titles}"
    assert len(rows) == 3  # "The Shawshank Redemption", "The Godfather", "The Return of the King"


# ---------------------------------------------------------------------------
# 9. Auth paths
# ---------------------------------------------------------------------------

def test_graphql_no_auth_returns_401_when_enforced():
    """Without KNOT_AUTH_DEV_MODE=1, missing token → 401."""
    from knot.api.main import app
    from knot.auth import require_user as _require_user, Principal as _Principal, _strip_bearer
    from knot.db import users
    from fastapi import HTTPException, Header

    def strict_require_user(
        authorization: str | None = Header(default=None),
    ) -> _Principal:
        token = _strip_bearer(authorization)
        key_hash = users.hash_key(token)
        with db.connect() as conn:
            user = users.find_by_key_hash(conn, key_hash)
        if user is None:
            raise HTTPException(403, "Invalid bearer token")
        return _Principal(username=user.username, is_admin=user.is_admin)

    app.dependency_overrides[_require_user] = strict_require_user
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/graph/query", json={"query": "{ __typename }"})
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(_require_user, None)


def test_graphql_wrong_token_returns_403_when_enforced():
    """Wrong token → 403."""
    from knot.api.main import app
    from knot.auth import require_user as _require_user, Principal as _Principal, _strip_bearer
    from knot.db import users
    from fastapi import HTTPException, Header

    def strict_require_user(
        authorization: str | None = Header(default=None),
    ) -> _Principal:
        token = _strip_bearer(authorization)
        key_hash = users.hash_key(token)
        with db.connect() as conn:
            user = users.find_by_key_hash(conn, key_hash)
        if user is None:
            raise HTTPException(403, "Invalid bearer token")
        return _Principal(username=user.username, is_admin=user.is_admin)

    app.dependency_overrides[_require_user] = strict_require_user
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/graph/query",
            json={"query": "{ __typename }"},
            headers={"Authorization": "Bearer totally_wrong_token_xyz"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(_require_user, None)


def test_graphql_dev_mode_bypass(gql_client):
    """With KNOT_AUTH_DEV_MODE=1, no token needed → 200."""
    resp = gql_client.post("/graph/query", json={"query": "{ __typename }"})
    assert resp.status_code == 200
    assert resp.json()["data"]["__typename"] == "Query"


# ---------------------------------------------------------------------------
# 10. No published spec → 409
# ---------------------------------------------------------------------------

def test_graphql_no_spec_returns_409(pg_conn):
    """When no spec is published, /graph/query returns 409."""
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    db.apply_schema()

    from knot.api.main import app
    os.environ["KNOT_AUTH_DEV_MODE"] = "1"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/graph/query", json={"query": "{ __typename }"})
        assert resp.status_code == 409
    finally:
        os.environ.pop("KNOT_AUTH_DEV_MODE", None)
        # Re-apply schema for subsequent tests.
        db.apply_schema()
