"""Integration tests for POST /graph/query (GraphQL endpoint).

Coverage:
  - Schema regenerates on spec publish (content_hash changes → new schema()).
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

import os

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from knot import db
from knot.db import graph_store, spec_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.spec import Array, ClassRef, OntologyClass, Primitive, Slot, Source, Spec
from knot.spec.metaschema import SourceBinding

# ---------------------------------------------------------------------------
# Spec + data builders
# ---------------------------------------------------------------------------


def _build_spec() -> tuple[Spec, OntologyClass, Source]:
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    spec = Spec(
        id="gql_test",
        version="1.0.0",
        slots=[imdb_id, title, year],
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
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


@pytest_asyncio.fixture
async def gql_db(pg_conn):
    """Publish a Movie spec and insert sample rows. Yields (conn, spec, src, rev)."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec, movie, src = _build_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    _rows = _sample_rows()
    movie = next(c for c in spec.classes if c.name == "Movie")
    await graph_store.insert_rows(
        pg_conn,
        source=src,
        cls=movie,
        spec_revision=rev,
        rows=_rows,
        canonical_ids=[str(r["imdb_id"]) for r in _rows],
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


async def test_schema_cache_changes_on_new_publish(gql_db):
    """Content hash changes after a new publish → schema cache miss → new schema."""
    from knot.spec.compile.graphql import get_or_build_schema

    conn, spec, src, rev = gql_db

    hash1 = await spec_store.get_published_content_hash(conn)
    schema1 = get_or_build_schema(spec, hash1)

    # Publish a new (identical-content but new revision) draft.
    spec2, movie2, src2 = _build_spec()
    # Tweak version so content hash differs.
    spec2.version = "2.0.0"
    rev2 = await create_draft(conn)
    await update_draft(conn, rev2, spec2)
    await publish_draft(conn, rev2)

    hash2 = await spec_store.get_published_content_hash(conn)
    assert hash1 != hash2

    schema2 = get_or_build_schema(spec2, hash2)
    assert schema1 is not schema2


# ---------------------------------------------------------------------------
# 2. Query without filter → all rows (page shape)
# ---------------------------------------------------------------------------


def test_query_no_filter_returns_all_rows(gql_client):
    result = _post(gql_client, "{ movie { imdbId title year canonicalId } movieCount }")
    assert "data" in result
    rows = result["data"]["movie"]
    assert result["data"]["movieCount"] == 5
    assert len(rows) == 5
    assert "imdbId" in rows[0] or "canonicalId" in rows[0]


# ---------------------------------------------------------------------------
# 3. Compare filter: year >= 1990
# ---------------------------------------------------------------------------


def test_query_filter_year_gte(gql_client):
    query = (
        "{ movie(where: { year: { gte: 1990 } }) { imdbId title year } "
        "  movieCount(where: { year: { gte: 1990 } }) }"
    )
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    years = [r["year"] for r in rows]
    assert all(y >= 1990 for y in years), f"unexpected years: {years}"
    assert len(rows) == 3  # 1994, 1993, 2003
    assert result["data"]["movieCount"] == 3


# ---------------------------------------------------------------------------
# 4. Multiple Compare filters (AND): 1990 <= year <= 2000
# ---------------------------------------------------------------------------


def test_query_filter_year_range(gql_client):
    query = (
        "{ movie(where: { year: { gte: 1990, lte: 2000 } }) { imdbId title year } "
        "  movieCount(where: { year: { gte: 1990, lte: 2000 } }) }"
    )
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    years = [r["year"] for r in rows]
    assert all(1990 <= y <= 2000 for y in years)
    assert len(rows) == 2  # 1994, 1993
    assert result["data"]["movieCount"] == 2


# ---------------------------------------------------------------------------
# 5. Pagination: limit / offset
# ---------------------------------------------------------------------------


def test_query_limit(gql_client):
    query = "{ movie(limit: 2) { imdbId title year } movieCount }"
    result = _post(gql_client, query)
    assert len(result["data"]["movie"]) == 2
    assert result["data"]["movieCount"] == 5  # unfiltered total


def test_query_offset(gql_client):
    all_result = _post(gql_client, "{ movie { imdbId title year } }")
    all_rows = all_result["data"]["movie"]

    offset_result = _post(gql_client, "{ movie(limit: 10, offset: 2) { imdbId title year } }")
    offset_rows = offset_result["data"]["movie"]

    assert len(offset_rows) == 3
    assert [r["imdbId"] for r in offset_rows] == [r["imdbId"] for r in all_rows[2:]]


# ---------------------------------------------------------------------------
# 6. as_of revision pin
# ---------------------------------------------------------------------------


def test_query_as_of_past_revision_returns_empty(gql_db, gql_client):
    """Rows inserted at spec_revision=rev; as_of=rev-1 returns nothing."""
    conn, spec, src, rev = gql_db
    query = f"{{ movie(asOf: {rev - 1}) {{ imdbId }} movieCount(asOf: {rev - 1}) }}"
    result = _post(gql_client, query)
    assert result["data"]["movie"] == []
    assert result["data"]["movieCount"] == 0


def test_query_as_of_current_revision_returns_rows(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f"{{ movie(asOf: {rev}) {{ imdbId }} movieCount(asOf: {rev}) }}"
    result = _post(gql_client, query)
    assert len(result["data"]["movie"]) == 5
    assert result["data"]["movieCount"] == 5


# ---------------------------------------------------------------------------
# 7. Class with no rows returns empty list
# ---------------------------------------------------------------------------


def test_query_class_with_no_rows(gql_client):
    query = (
        "{ movie(where: { year: { gt: 9999 } }) { imdbId } "
        "  movieCount(where: { year: { gt: 9999 } }) }"
    )
    result = _post(gql_client, query)
    assert result["data"]["movie"] == []
    assert result["data"]["movieCount"] == 0


# ---------------------------------------------------------------------------
# 8. LIKE / Matches predicate
# ---------------------------------------------------------------------------


def test_query_like_filter(gql_client):
    query = (
        '{ movie(where: { title: { like: "The%" } }) { title } '
        '  movieCount(where: { title: { like: "The%" } }) }'
    )
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    titles = [r["title"] for r in rows]
    assert all(t.startswith("The") for t in titles), f"unexpected titles: {titles}"
    assert len(rows) == 3
    assert result["data"]["movieCount"] == 3


# ---------------------------------------------------------------------------
# 9. Auth paths
# ---------------------------------------------------------------------------


def test_graphql_no_auth_returns_401_when_enforced():
    """Without KNOT_AUTH_DEV_MODE=1, missing token → 401."""
    from fastapi import Header, HTTPException

    from knot.api.auth.security import Principal as _Principal
    from knot.api.auth.security import _strip_bearer
    from knot.api.auth.security import require_user as _require_user
    from knot.api.main import app
    from knot.db import users

    async def strict_require_user(
        authorization: str | None = Header(default=None),
    ) -> _Principal:
        token = _strip_bearer(authorization)
        key_hash = users.hash_key(token)
        async with db.connect() as conn:
            user = await users.find_by_key_hash(conn, key_hash)
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
    from fastapi import Header, HTTPException

    from knot.api.auth.security import Principal as _Principal
    from knot.api.auth.security import _strip_bearer
    from knot.api.auth.security import require_user as _require_user
    from knot.api.main import app
    from knot.db import users

    async def strict_require_user(
        authorization: str | None = Header(default=None),
    ) -> _Principal:
        token = _strip_bearer(authorization)
        key_hash = users.hash_key(token)
        async with db.connect() as conn:
            user = await users.find_by_key_hash(conn, key_hash)
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


async def test_graphql_no_spec_returns_409(pg_conn):
    """When no spec is published, /graph/query returns 409."""
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await db.apply_schema()

    from knot.api.main import app

    os.environ["KNOT_AUTH_DEV_MODE"] = "1"
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/graph/query", json={"query": "{ __typename }"})
        assert resp.status_code == 409
    finally:
        os.environ.pop("KNOT_AUTH_DEV_MODE", None)
        # Re-apply schema for subsequent tests.
        await db.apply_schema()


# ---------------------------------------------------------------------------
# 11. Pagination metadata fields
# ---------------------------------------------------------------------------


def test_pagination_window(gql_client):
    """limit and offset honour the requested window; movieCount returns total."""
    query = "{ movie(limit: 3, offset: 1) { imdbId } movieCount }"
    result = _post(gql_client, query)
    assert len(result["data"]["movie"]) == 3
    assert result["data"]["movieCount"] == 5  # unfiltered total


def test_count_reflects_predicate(gql_client):
    """movieCount should return only rows matching the where filter."""
    query = (
        "{ movie(where: { year: { gte: 1990 } }) { imdbId title year } "
        "  movieCount(where: { year: { gte: 1990 } }) }"
    )
    result = _post(gql_client, query)
    assert result["data"]["movieCount"] == 3
    assert len(result["data"]["movie"]) == 3


def test_as_of_propagates_through_count(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f"{{ movie(asOf: {rev}) {{ imdbId }} movieCount(asOf: {rev}) }}"
    result = _post(gql_client, query)
    assert len(result["data"]["movie"]) == 5
    assert result["data"]["movieCount"] == 5


# ---------------------------------------------------------------------------
# 12. orderBy: numeric field ASC / DESC
# ---------------------------------------------------------------------------


def test_order_by_year_asc(gql_client):
    query = "{ movie(orderBy: [{ field: year, direction: ASC }]) { imdbId title year } }"
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    years = [r["year"] for r in rows]
    assert years == sorted(years), f"expected ASC, got {years}"


def test_order_by_year_desc(gql_client):
    query = "{ movie(orderBy: [{ field: year, direction: DESC }]) { imdbId title year } }"
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    years = [r["year"] for r in rows]
    assert years == sorted(years, reverse=True), f"expected DESC, got {years}"


def test_order_by_title_asc(gql_client):
    query = "{ movie(orderBy: [{ field: title, direction: ASC }]) { imdbId title year } }"
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    titles = [r["title"] for r in rows]
    assert titles == sorted(titles), f"expected ASC, got {titles}"


def test_order_by_year_desc_with_limit(gql_client):
    """orderBy DESC with limit → first N in descending order."""
    query = "{ movie(orderBy: [{ field: year, direction: DESC }], limit: 2) { imdbId title year } }"
    result = _post(gql_client, query)
    rows = result["data"]["movie"]
    years = [r["year"] for r in rows]
    assert len(years) == 2
    assert years[0] >= years[1], f"expected DESC, got {years}"
    assert years[0] == 2003  # The Return of the King


# ---------------------------------------------------------------------------
# 13. Single-entity lookup: movieByCanonicalId
# ---------------------------------------------------------------------------

_BY_ID_FIELDS = "{ imdbId title year canonicalId }"


def test_by_canonical_id_found(gql_client):
    query = f'{{ movieByCanonicalId(canonicalId: "tt0111161") {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    row = result["data"]["movieByCanonicalId"]
    assert row is not None
    assert row["imdbId"] == "tt0111161"
    assert row["title"] == "The Shawshank Redemption"
    assert row["year"] == 1994


def test_by_canonical_id_not_found_returns_null(gql_client):
    query = f'{{ movieByCanonicalId(canonicalId: "tt9999999") {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    assert result["data"]["movieByCanonicalId"] is None


def test_by_canonical_id_as_of_before_ingest_returns_null(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieByCanonicalId(canonicalId: "tt0111161", asOf: {rev - 1}) {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    assert result["data"]["movieByCanonicalId"] is None


def test_by_canonical_id_as_of_current_returns_record(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieByCanonicalId(canonicalId: "tt0068646", asOf: {rev}) {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    row = result["data"]["movieByCanonicalId"]
    assert row is not None
    assert row["title"] == "The Godfather"


# ---------------------------------------------------------------------------
# 14. Resolved view: movieResolved
# ---------------------------------------------------------------------------


def test_resolved_found(gql_client):
    query = f'{{ movieResolved(canonicalId: "tt0050083") {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    row = result["data"]["movieResolved"]
    assert row is not None
    assert row["canonicalId"] == "tt0050083"
    assert row["title"] == "12 Angry Men"
    assert row["year"] == 1957


def test_resolved_not_found_returns_null(gql_client):
    query = f'{{ movieResolved(canonicalId: "tt9999999") {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    assert result["data"]["movieResolved"] is None


def test_resolved_as_of_before_ingest_returns_null(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieResolved(canonicalId: "tt0111161", asOf: {rev - 1}) {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    assert result["data"]["movieResolved"] is None


def test_resolved_as_of_current_returns_record(gql_db, gql_client):
    conn, spec, src, rev = gql_db
    query = f'{{ movieResolved(canonicalId: "tt0167260", asOf: {rev}) {_BY_ID_FIELDS} }}'
    result = _post(gql_client, query)
    row = result["data"]["movieResolved"]
    assert row is not None
    assert row["title"] == "The Return of the King"


# ---------------------------------------------------------------------------
# 15. count_rows predicate filtering (unit-level via graph_store directly)
# ---------------------------------------------------------------------------


async def test_count_rows_with_predicate(gql_db):
    """count_rows should honour the predicate and return filtered count."""
    from knot.spec.compile.postgres import CompileContext, compile_predicate
    from knot.spec.metaschema import Compare, CompareOp, Literal_, SlotPath

    conn, spec, src, rev = gql_db
    movie_cls = next(c for c in spec.classes if c.name == "Movie")
    year_slot = next(s for s in movie_cls.slots if s.name == "year")

    path = SlotPath(from_class=movie_cls, slots=[year_slot])
    node = Compare(op=CompareOp.GTE, left=path, right=Literal_(value=1990))
    ctx = CompileContext(primary_class=movie_cls, alias="s")
    pred_sql = compile_predicate(node, ctx)

    total_unfiltered = await graph_store.count_rows(conn, cls=movie_cls)
    assert total_unfiltered == 5

    total_filtered = await graph_store.count_rows(
        conn,
        cls=movie_cls,
        predicate_sql=pred_sql,
        predicate_params=ctx.params,
    )
    assert total_filtered == 3  # 1994, 1993, 2003


# ===========================================================================
# 16. Slice A — derived-slot WHERE / ORDER BY / aggregation
# ===========================================================================

# ---------------------------------------------------------------------------
# Fixtures: Movie + Credit spec with a derived slot (credit_count)
# ---------------------------------------------------------------------------


def _build_derived_spec():
    """Movie + Credit spec.  Movie.credit_count is a derived slot
    (RelationCount over Credit rows whose movie FK = movie canonical_id).
    """
    from knot.spec.metaschema import (
        RelationCount,
        ReverseRelation,
    )

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    year = Slot(name="year", type=Primitive(name="integer"))
    movie_cls = OntologyClass(name="Movie", slots=[imdb_id, title, year])

    credit_id = Slot(name="credit_id", type=Primitive(name="string"), identifier=True, required=True)
    credit_movie = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_role = Slot(name="role", type=Primitive(name="string"))
    credit_cls = OntologyClass(name="Credit", slots=[credit_id, credit_movie, credit_role])

    credit_count_deriv = RelationCount(
        relation=ReverseRelation(target_class=credit_cls, fk_slot=credit_movie),
    )
    credit_count_slot = Slot(name="credit_count", type=Primitive(name="integer"), derivation=credit_count_deriv)
    movie_cls.slots = [imdb_id, title, year, credit_count_slot]

    movie_src = Source(name="imdb")
    credit_src = Source(name="credits")
    movie_binding = SourceBinding(source=movie_src, class_=movie_cls, identifier_slot=imdb_id)  # type: ignore[call-arg]
    credit_binding = SourceBinding(source=credit_src, class_=credit_cls, identifier_slot=credit_id)  # type: ignore[call-arg]

    spec = Spec(
        id="derived_gql_test",
        version="1.0.0",
        slots=[imdb_id, title, year, credit_count_slot, credit_id, credit_movie, credit_role],
        classes=[movie_cls, credit_cls],
        sources=[movie_src, credit_src],
        source_bindings=[movie_binding, credit_binding],
    )
    return spec, movie_cls, credit_cls, movie_src, credit_src


@pytest_asyncio.fixture
async def derived_db(pg_conn):
    """Publish the Movie+Credit spec with derived credit_count, insert rows."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec, movie_cls, credit_cls, movie_src, credit_src = _build_derived_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    # Ingest 3 movies.
    await graph_store.insert_rows(
        pg_conn,
        source=movie_src,
        cls=movie_cls,
        spec_revision=rev,
        rows=[
            {"imdb_id": "m1", "title": "Film One", "year": 1990},
            {"imdb_id": "m2", "title": "Film Two", "year": 2000},
            {"imdb_id": "m3", "title": "Film Three", "year": 2010},
        ],
        canonical_ids=[
            str(r["imdb_id"])
            for r in [
                {"imdb_id": "m1", "title": "Film One", "year": 1990},
                {"imdb_id": "m2", "title": "Film Two", "year": 2000},
                {"imdb_id": "m3", "title": "Film Three", "year": 2010},
            ]
        ],
    )
    # m1 has 3 credits; m2 has 1 credit; m3 has 0
    await graph_store.insert_rows(
        pg_conn,
        source=credit_src,
        cls=credit_cls,
        spec_revision=rev,
        rows=[
            {"credit_id": "c1", "movie": "m1", "role": "director"},
            {"credit_id": "c2", "movie": "m1", "role": "actor"},
            {"credit_id": "c3", "movie": "m1", "role": "writer"},
            {"credit_id": "c4", "movie": "m2", "role": "director"},
        ],
        canonical_ids=[
            str(r["credit_id"])
            for r in [
                {"credit_id": "c1", "movie": "m1", "role": "director"},
                {"credit_id": "c2", "movie": "m1", "role": "actor"},
                {"credit_id": "c3", "movie": "m1", "role": "writer"},
                {"credit_id": "c4", "movie": "m2", "role": "director"},
            ]
        ],
    )
    yield pg_conn, spec, movie_src, credit_src, rev


@pytest.fixture
def derived_client(derived_db):
    from knot.api.main import app

    os.environ["KNOT_AUTH_DEV_MODE"] = "1"
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        os.environ.pop("KNOT_AUTH_DEV_MODE", None)


# ---------------------------------------------------------------------------
# 16a. WHERE filter on a derived slot returns expected rows
# ---------------------------------------------------------------------------


def test_where_on_derived_slot_returns_matching_rows(derived_client):
    """WHERE credit_count >= 2 should return only m1 (3 credits)."""
    query = (
        "{ movie(where: { creditCount: { gte: 2 } }) { imdbId creditCount } "
        "  movieCount(where: { creditCount: { gte: 2 } }) }"
    )
    result = _post(derived_client, query)
    assert "errors" not in result, result.get("errors")
    rows = result["data"]["movie"]
    ids = {r["imdbId"] for r in rows}
    assert ids == {"m1"}, f"expected only m1, got {ids}"
    assert result["data"]["movieCount"] == 1


def test_where_on_derived_slot_eq_zero(derived_client):
    """WHERE credit_count = 0 should return only m3 (no credits)."""
    query = "{ movie(where: { creditCount: { eq: 0 } }) { imdbId creditCount } }"
    result = _post(derived_client, query)
    assert "errors" not in result, result.get("errors")
    ids = {r["imdbId"] for r in result["data"]["movie"]}
    assert ids == {"m3"}, f"expected only m3, got {ids}"


def test_where_combined_derived_and_stored(derived_client):
    """WHERE year >= 2000 AND credit_count >= 1 → only m2 (year=2000, 1 credit)."""
    query = (
        "{ movie(where: { year: { gte: 2000 }, creditCount: { gte: 1 } }) "
        "  { imdbId creditCount } }"
    )
    result = _post(derived_client, query)
    assert "errors" not in result, result.get("errors")
    ids = {r["imdbId"] for r in result["data"]["movie"]}
    assert ids == {"m2"}, f"expected only m2, got {ids}"


# ---------------------------------------------------------------------------
# 16b. ORDER BY derived slot ASC + DESC
# ---------------------------------------------------------------------------


def test_order_by_derived_slot_asc(derived_client):
    """ORDER BY credit_count ASC → m3(0), m2(1), m1(3)."""
    query = "{ movie(orderBy: [{ field: creditCount, direction: ASC }]) { imdbId creditCount } }"
    result = _post(derived_client, query)
    assert "errors" not in result, result.get("errors")
    counts = [r["creditCount"] for r in result["data"]["movie"]]
    assert counts == sorted(counts), f"expected ASC, got {counts}"
    assert counts[0] == 0
    assert counts[-1] == 3


def test_order_by_derived_slot_desc(derived_client):
    """ORDER BY credit_count DESC → m1(3), m2(1), m3(0)."""
    query = "{ movie(orderBy: [{ field: creditCount, direction: DESC }]) { imdbId creditCount } }"
    result = _post(derived_client, query)
    assert "errors" not in result, result.get("errors")
    counts = [r["creditCount"] for r in result["data"]["movie"]]
    assert counts == sorted(counts, reverse=True), f"expected DESC, got {counts}"
    assert counts[0] == 3
    assert counts[-1] == 0


# ---------------------------------------------------------------------------
# 16c. Aggregation queries — count, sum, avg, min, max
# ---------------------------------------------------------------------------


def test_aggregate_count(gql_client):
    """movieAggregate returns correct count of all rows."""
    query = "{ movieAggregate { count } }"
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    agg = result["data"]["movieAggregate"]
    assert agg["count"] == 5


def test_aggregate_sum_avg_min_max(gql_client):
    """movieAggregate returns correct sum/avg/min/max for year."""
    query = "{ movieAggregate { count sumYear avgYear minYear maxYear } }"
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    agg = result["data"]["movieAggregate"]
    # years: 1994, 1972, 1993, 2003, 1957
    assert agg["count"] == 5
    assert agg["sumYear"] == pytest.approx(1994 + 1972 + 1993 + 2003 + 1957)
    assert agg["minYear"] == pytest.approx(1957)
    assert agg["maxYear"] == pytest.approx(2003)
    expected_avg = (1994 + 1972 + 1993 + 2003 + 1957) / 5
    assert agg["avgYear"] == pytest.approx(expected_avg, rel=1e-4)


def test_aggregate_with_where_filter(gql_client):
    """movieAggregate with where filter counts only matching rows."""
    query = "{ movieAggregate(where: { year: { gte: 1990 } }) { count sumYear minYear maxYear } }"
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    agg = result["data"]["movieAggregate"]
    # years >= 1990: 1994, 1993, 2003
    assert agg["count"] == 3
    assert agg["sumYear"] == pytest.approx(1994 + 1993 + 2003)
    assert agg["minYear"] == pytest.approx(1993)
    assert agg["maxYear"] == pytest.approx(2003)


def test_aggregate_empty_result(gql_client):
    """movieAggregate with no matching rows returns count=0 and null aggregates."""
    query = "{ movieAggregate(where: { year: { gt: 9999 } }) { count sumYear avgYear } }"
    result = _post(gql_client, query)
    assert "errors" not in result, result.get("errors")
    agg = result["data"]["movieAggregate"]
    assert agg["count"] == 0
    # SQL aggregate over zero rows returns NULL → None in Python
    assert agg["sumYear"] is None
    assert agg["avgYear"] is None


def test_aggregate_respects_as_of(derived_db, derived_client):
    """movieAggregate with asOf before any rows → count=0."""
    pg_conn, spec, movie_src, credit_src, rev = derived_db
    query = f"{{ movieAggregate(asOf: {rev - 1}) {{ count }} }}"
    result = _post(derived_client, query)
    assert "errors" not in result, result.get("errors")
    agg = result["data"]["movieAggregate"]
    assert agg["count"] == 0
