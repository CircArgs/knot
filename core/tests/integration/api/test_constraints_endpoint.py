"""Tests for POST /spec/drafts/{draft_id}/constraints.

Covers:
  - Simple SQL constraint (year >= 1888)
  - SQL AND constraint (year >= 1888 AND year <= 2100)
  - 404 on unknown class
  - 409 on duplicate constraint name (case-twin)
  - 422 on invalid name pattern
  - End-to-end: POST constraint → publish → ingest violating row → check → violation reported
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.db.spec_store import (
    create_draft,
    get_revision,
    update_draft,
)
from knot.spec import OntologyClass, Primitive, Property, Source, Spec
from knot.spec.metaschema import SourceBinding

# ---------------------------------------------------------------------------
# Helpers — minimal valid spec factory
# ---------------------------------------------------------------------------


def _make_spec() -> Spec:
    """Spec with Movie class having year (int) and imdb_id (str identifier)."""
    imdb_id = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year = Property(name="year", type=Primitive(name="integer"), required=False)
    movie = OntologyClass(name="Movie", properties=[imdb_id, year])
    src = Source(name="imdb_movies")
    binding = SourceBinding(source=src, class_=movie, identifier_property=imdb_id)  # type: ignore[call-arg]
    return Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_db(pg_conn):
    """Reset all spec-related state to a clean slate before each test."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()
    yield pg_conn


@pytest.fixture
def client():
    from knot.api.main import app

    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


@pytest.fixture
def client_no_exc():
    from knot.api.main import app

    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(require_user, None)


@pytest.fixture
async def draft_with_movie(clean_db):
    """Create a draft with the Movie spec; yield (conn, draft_id)."""
    conn = clean_db
    rev = await create_draft(conn)
    await update_draft(conn, rev, _make_spec())
    return conn, rev


# ---------------------------------------------------------------------------
# 1. Simple Compare constraint — year >= 1888
# ---------------------------------------------------------------------------


def test_add_compare_constraint_returns_mutation_response(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": "year >= 1888",
        "severity": "error",
        "message": "Year must be >= 1888",
    }
    resp = client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["draft_revision"] == draft_id
    assert data["spec_summary"]["constraints"] == 1


async def test_add_compare_constraint_persisted_on_spec(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": "year >= 1888",
    }
    client.post(f"/spec/drafts/{draft_id}/constraints", json=body)

    spec = await get_revision(conn, draft_id)
    assert len(spec.constraints) == 1
    con = spec.constraints[0]
    assert con.name == "year_not_before_cinema"
    assert con.primary.name == "Movie"


# ---------------------------------------------------------------------------
# 2. BoolExpr(AND, [Compare>=1888, Compare<=2100])
# ---------------------------------------------------------------------------


async def test_add_bool_expr_constraint(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_plausible_range",
        "primary_class_name": "Movie",
        "body": "year >= 1888 AND year <= 2100",
        "severity": "warning",
        "message": "Year should be between 1888 and 2100",
    }
    resp = client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["spec_summary"]["constraints"] == 1

    spec = await get_revision(conn, draft_id)
    con = spec.constraints[0]
    assert con.name == "year_plausible_range"
    assert isinstance(con.body, str)
    assert "1888" in con.body
    assert "2100" in con.body


# ---------------------------------------------------------------------------
# 3. 404 on unknown class
# ---------------------------------------------------------------------------


def test_unknown_primary_class_returns_404(draft_with_movie, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "bad_constraint",
        "primary_class_name": "DoesNotExist",
        "body": "year >= 0",
    }
    resp = client_no_exc.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. 422 on invalid SQL in body
# ---------------------------------------------------------------------------


def test_invalid_sql_in_body_returns_422(draft_with_movie, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "bad_sql",
        "primary_class_name": "Movie",
        "body": "SELECT * FROM foo",  # full SELECT not allowed — must be predicate
    }
    resp = client_no_exc.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. 409 on duplicate constraint name (case-twin)
# ---------------------------------------------------------------------------


def test_duplicate_constraint_name_returns_409(draft_with_movie, client, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_check",
        "primary_class_name": "Movie",
        "body": "year >= 1888",
    }
    resp = client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 200

    # Case-twin collision: "YEAR_CHECK" vs "year_check"
    body2 = {**body, "name": "YEAR_CHECK"}
    resp2 = client_no_exc.post(f"/spec/drafts/{draft_id}/constraints", json=body2)
    assert resp2.status_code == 409


# ---------------------------------------------------------------------------
# 6. 422 on invalid name pattern
# ---------------------------------------------------------------------------


def test_invalid_name_pattern_returns_422(draft_with_movie, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "bad name with spaces",  # fails _NAME_PATTERN
        "primary_class_name": "Movie",
        "body": "year >= 0",
    }
    resp = client_no_exc.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 7. End-to-end: POST constraint → publish → ingest violating row → check
# ---------------------------------------------------------------------------


def test_e2e_constraint_violation_reported(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    # Add a constraint: year must be >= 1888
    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": "year >= 1888",
        "severity": "error",
    }
    resp = client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 200

    # Publish the draft (no existing data → constraint gate passes)
    pub_resp = client.post(f"/spec/drafts/{draft_id}/publish")
    assert pub_resp.status_code == 200, pub_resp.text

    # Ingest a valid row
    ingest_resp = client.post(
        "/graph/ingest/imdb_movies",
        json={"rows": [{"imdb_id": "tt0000001", "year": 1888}]},
    )
    assert ingest_resp.status_code == 200

    # Ingest a violating row (year = 1800 < 1888) — ERROR constraints always block at ingest.
    ingest_resp2 = client.post(
        "/graph/ingest/imdb_movies",
        json={"rows": [{"imdb_id": "tt0000002", "year": 1800}]},
    )
    assert ingest_resp2.status_code == 422
    violations_ingest = ingest_resp2.json()["detail"]["violations"]
    assert len(violations_ingest) >= 1
    assert violations_ingest[0]["rule_id"] == "year_not_before_cinema"

    # The violating row rolled back, so the constraint check endpoint sees no violations.
    check_resp = client.post("/graph/constraints/check")
    assert check_resp.status_code == 200, check_resp.text
    violations = check_resp.json()["violations"]
    assert violations == []


def test_e2e_no_violations_when_all_rows_valid(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": "year >= 1888",
        "severity": "error",
    }
    client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    client.post(f"/spec/drafts/{draft_id}/publish")

    # Ingest only valid rows
    client.post(
        "/graph/ingest/imdb_movies",
        json={
            "rows": [
                {"imdb_id": "tt0000001", "year": 1900},
                {"imdb_id": "tt0000002", "year": 2000},
            ]
        },
    )

    check_resp = client.post("/graph/constraints/check")
    assert check_resp.status_code == 200
    assert check_resp.json()["violations"] == []
