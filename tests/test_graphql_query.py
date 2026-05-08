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
  - Pagination metadata: total, limit, offset, asOf fields in MoviePage.
  - orderBy ASC and DESC on a numeric field.
  - orderBy on a string field.
  - Single-entity lookup via movieByCanonicalId.
  - Single-entity lookup with missing canonical_id returns null.
  - Resolved view via movieResolved.
  - Resolved view with missing canonical_id returns null.
  - count_rows uses predicate when filtering.

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
# 2. Query without filter → all rows (page shape)
# ---------------------------------------------------------------------------

def test_query_no_filter_returns_all_rows(gql_client):
    result = _post(gql_client, "{ movie { rows total limit offset } }")
    assert "data" in result
    page = result["data"]["movie"]
    assert page["total"] == 5
    assert len(page["rows"]) == 5
    assert page["limit"] == 100
    assert page["offset"] == 0
    # Each row is a JSON string with the expected keys.
    first = json.loads(page["rows"][0])
    assert "imdb_id" in first or "_canonical_id" in first


# ---------------------------------------------------------------------------
# 3. Compare filter: year >= 1990
# ---------------------------------------------------------------------------

def test_query_filter_year_gte(gql_client):
    query = "{ movie(where: { year: { gte: 1990 } }) { rows total } }"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    rows = [json.loads(r) for r in page["rows"]]
    years = [r["year"] for r in rows]
    assert all(y >= 1990 for y in years), f"unexpected years: {years}"
    assert len(rows) == 3  # 1994, 1993, 2003
    assert page["total"] == 3


# ---------------------------------------------------------------------------
# 4. Multiple Compare filters (AND): 1990 <= year <= 2000
# ---------------------------------------------------------------------------

def test_query_filter_year_range(gql_client):
    query = "{ movie(where: { year: { gte: 1990, lte: 2000 } }) { rows total } }"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    rows = [json.loads(r) for r in page["rows"]]
    years = [r["year"] for r in rows]
    assert all(1990 <= y <= 2000 for y in years)
    assert len(rows) == 2  # 1994, 1993
    assert page["total"] == 2


# ---------------------------------------------------------------------------
# 5. Pagination: limit / offset
# ---------------------------------------------------------------------------

def test_query_limit(gql_client):
    query = "{ movie(limit: 2) { rows total limit offset } }"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert len(page["rows"]) == 2
    assert page["total"] == 5  # total is unfiltered count
    assert page["limit"] == 2
    assert page["offset"] == 0


def test_query_offset(gql_client):
    all_result = _post(gql_client, "{ movie { rows } }")
    all_rows = [json.loads(r) for r in all_result["data"]["movie"]["rows"]]

    offset_result = _post(gql_client, "{ movie(limit: 10, offset: 2) { rows offset } }")
    offset_page = offset_result["data"]["movie"]
    offset_rows = [json.loads(r) for r in offset_page["rows"]]

    # Offset skips first 2 rows; results should be the tail of all rows.
    assert len(offset_rows) == 3
    assert offset_page["offset"] == 2
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
    query = f"{{ movie(asOf: {rev - 1}) {{ rows total }} }}"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert page["rows"] == [], f"expected empty, got {page['rows']}"
    assert page["total"] == 0


def test_query_as_of_current_revision_returns_rows(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f"{{ movie(asOf: {rev}) {{ rows total }} }}"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert len(page["rows"]) == 5
    assert page["total"] == 5


# ---------------------------------------------------------------------------
# 7. Class with no rows returns empty list
# ---------------------------------------------------------------------------

def test_query_class_with_no_rows(gql_client):
    query = "{ movie(where: { year: { gt: 9999 } }) { rows total } }"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert page["rows"] == []
    assert page["total"] == 0


# ---------------------------------------------------------------------------
# 8. LIKE / Matches predicate
# ---------------------------------------------------------------------------

def test_query_like_filter(gql_client):
    query = '{ movie(where: { title: { like: "The%" } }) { rows total } }'
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    rows = [json.loads(r) for r in page["rows"]]
    titles = [r["title"] for r in rows]
    assert all(t.startswith("The") for t in titles), f"unexpected titles: {titles}"
    assert len(rows) == 3  # "The Shawshank Redemption", "The Godfather", "The Return of the King"
    assert page["total"] == 3


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


# ---------------------------------------------------------------------------
# 11. Pagination metadata fields
# ---------------------------------------------------------------------------

def test_page_metadata_fields(gql_client):
    """Verify all page metadata fields are present and correct."""
    query = "{ movie(limit: 3, offset: 1) { rows total limit offset } }"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert page["total"] == 5
    assert page["limit"] == 3
    assert page["offset"] == 1
    assert len(page["rows"]) == 3


def test_page_total_reflects_predicate(gql_client):
    """total should count only rows matching the where filter."""
    query = "{ movie(where: { year: { gte: 1990 } }) { rows total } }"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert page["total"] == 3
    assert len(page["rows"]) == 3


def test_page_as_of_field_present(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f"{{ movie(asOf: {rev}) {{ rows total asOf }} }}"
    result = _post(gql_client, query)
    page = result["data"]["movie"]
    assert page["asOf"] == rev
    assert page["total"] == 5


# ---------------------------------------------------------------------------
# 12. orderBy: numeric field ASC / DESC
# ---------------------------------------------------------------------------

def test_order_by_year_asc(gql_client):
    query = "{ movie(orderBy: [{ field: year, direction: ASC }]) { rows } }"
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    years = [r["year"] for r in rows]
    assert years == sorted(years), f"expected ASC, got {years}"


def test_order_by_year_desc(gql_client):
    query = "{ movie(orderBy: [{ field: year, direction: DESC }]) { rows } }"
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    years = [r["year"] for r in rows]
    assert years == sorted(years, reverse=True), f"expected DESC, got {years}"


def test_order_by_title_asc(gql_client):
    query = "{ movie(orderBy: [{ field: title, direction: ASC }]) { rows } }"
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    titles = [r["title"] for r in rows]
    assert titles == sorted(titles), f"expected ASC, got {titles}"


def test_order_by_year_desc_with_limit(gql_client):
    """orderBy DESC with limit → first N in descending order."""
    query = "{ movie(orderBy: [{ field: year, direction: DESC }], limit: 2) { rows } }"
    result = _post(gql_client, query)
    rows = [json.loads(r) for r in result["data"]["movie"]["rows"]]
    years = [r["year"] for r in rows]
    assert len(years) == 2
    assert years[0] >= years[1], f"expected DESC, got {years}"
    assert years[0] == 2003  # The Return of the King


# ---------------------------------------------------------------------------
# 13. Single-entity lookup: movieByCanonicalId
# ---------------------------------------------------------------------------

def test_by_canonical_id_found(gql_client):
    query = '{ movieByCanonicalId(canonicalId: "tt0111161") }'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    raw = result["data"]["movieByCanonicalId"]
    assert raw is not None
    row = json.loads(raw)
    assert row["imdb_id"] == "tt0111161"
    assert row["title"] == "The Shawshank Redemption"
    assert row["year"] == 1994


def test_by_canonical_id_not_found_returns_null(gql_client):
    query = '{ movieByCanonicalId(canonicalId: "tt9999999") }'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    assert result["data"]["movieByCanonicalId"] is None


def test_by_canonical_id_as_of_before_ingest_returns_null(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieByCanonicalId(canonicalId: "tt0111161", asOf: {rev - 1}) }}'
    result = _post(gql_client, query)
    assert result["data"]["movieByCanonicalId"] is None


def test_by_canonical_id_as_of_current_returns_record(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieByCanonicalId(canonicalId: "tt0068646", asOf: {rev}) }}'
    result = _post(gql_client, query)
    raw = result["data"]["movieByCanonicalId"]
    assert raw is not None
    row = json.loads(raw)
    assert row["title"] == "The Godfather"


# ---------------------------------------------------------------------------
# 14. Resolved view: movieResolved
# ---------------------------------------------------------------------------

def test_resolved_found(gql_client):
    query = '{ movieResolved(canonicalId: "tt0050083") }'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    raw = result["data"]["movieResolved"]
    assert raw is not None
    row = json.loads(raw)
    assert row["_canonical_id"] == "tt0050083"
    assert row["title"] == "12 Angry Men"
    assert row["year"] == 1957


def test_resolved_not_found_returns_null(gql_client):
    query = '{ movieResolved(canonicalId: "tt9999999") }'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    assert result["data"]["movieResolved"] is None


def test_resolved_as_of_before_ingest_returns_null(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieResolved(canonicalId: "tt0111161", asOf: {rev - 1}) }}'
    result = _post(gql_client, query)
    assert result["data"]["movieResolved"] is None


def test_resolved_as_of_current_returns_record(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieResolved(canonicalId: "tt0167260", asOf: {rev}) }}'
    result = _post(gql_client, query)
    raw = result["data"]["movieResolved"]
    assert raw is not None
    row = json.loads(raw)
    assert row["title"] == "The Return of the King"


# ---------------------------------------------------------------------------
# 15. count_rows predicate filtering (unit-level via graph_store directly)
# ---------------------------------------------------------------------------

def test_count_rows_with_predicate(gql_db):
    """count_rows should honour the predicate and return filtered count."""
    from knot.db.sql_compiler import CompileContext, compile_predicate
    from knot.ontology.metaschema import Compare, CompareOp, Literal_, SlotPath

    conn, spec, src, rev = gql_db
    movie_cls = next(c for c in spec.classes if c.name == "Movie")
    year_slot = next(s for s in movie_cls.slots if s.name == "year")

    path = SlotPath(from_class=movie_cls, slots=[year_slot])
    node = Compare(op=CompareOp.GTE, left=path, right=Literal_(value=1990))
    ctx = CompileContext(primary_class=movie_cls, alias="s")
    pred_sql = compile_predicate(node, ctx)

    total_unfiltered = graph_store.count_rows(conn, cls=movie_cls)
    assert total_unfiltered == 5

    total_filtered = graph_store.count_rows(
        conn, cls=movie_cls,
        predicate_sql=pred_sql, predicate_params=ctx.params,
    )
    assert total_filtered == 3  # 1994, 1993, 2003
