"""Tests for POST /spec/drafts/{draft_id}/constraints.

Covers:
  - Simple Compare-based constraint (year >= 1888)
  - BoolExpr(AND, [Compare>=1888, Compare<=2100])
  - 404 on unknown class / slot reference
  - 409 on duplicate constraint name (case-twin)
  - 422 on invalid name pattern
  - End-to-end: POST constraint → publish → ingest violating row → check → violation reported
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.security import Principal, require_user
from knot.db.spec_store import (
    create_draft,
    get_revision,
    publish_draft,
    update_draft,
)
from knot.ontology import OntologyClass, Slot, Source, Spec, TypeDefinition


# ---------------------------------------------------------------------------
# Helpers — minimal valid spec factory
# ---------------------------------------------------------------------------

def _make_spec() -> Spec:
    """Spec with Movie class having year (int) and imdb_id (str identifier)."""
    st_str = TypeDefinition(name="string", base="str")
    st_int = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st_str, identifier=True, required=True)
    year = Slot(name="year", range=st_int, required=False)
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id="test",
        version="1.0.0",
        types=[st_str, st_int],
        slots=[imdb_id, year],
        classes=[movie],
        sources=[src],
    )


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_db(pg_conn):
    """Reset all spec-related state to a clean slate before each test."""
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()
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
def draft_with_movie(clean_db):
    """Create a draft with the Movie spec; yield (conn, draft_id)."""
    conn = clean_db
    rev = create_draft(conn)
    update_draft(conn, rev, _make_spec())
    return conn, rev


# ---------------------------------------------------------------------------
# 1. Simple Compare constraint — year >= 1888
# ---------------------------------------------------------------------------

def test_add_compare_constraint_returns_mutation_response(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": {
            "kind": "compare",
            "op": "gte",
            "left": {"kind": "slot_path", "slots": ["year"]},
            "right": {"kind": "literal", "value": 1888},
        },
        "severity": "error",
        "message": "Year must be >= 1888",
    }
    resp = client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["draft_revision"] == draft_id
    assert data["spec_summary"]["constraints"] == 1


def test_add_compare_constraint_persisted_on_spec(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": {
            "kind": "compare",
            "op": "gte",
            "left": {"kind": "slot_path", "slots": ["year"]},
            "right": {"kind": "literal", "value": 1888},
        },
    }
    client.post(f"/spec/drafts/{draft_id}/constraints", json=body)

    spec = get_revision(conn, draft_id)
    assert len(spec.constraints) == 1
    con = spec.constraints[0]
    assert con.name == "year_not_before_cinema"
    assert con.primary.name == "Movie"


# ---------------------------------------------------------------------------
# 2. BoolExpr(AND, [Compare>=1888, Compare<=2100])
# ---------------------------------------------------------------------------

def test_add_bool_expr_constraint(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_plausible_range",
        "primary_class_name": "Movie",
        "body": {
            "kind": "bool_expr",
            "op": "and",
            "args": [
                {
                    "kind": "compare",
                    "op": "gte",
                    "left": {"kind": "slot_path", "slots": ["year"]},
                    "right": {"kind": "literal", "value": 1888},
                },
                {
                    "kind": "compare",
                    "op": "lte",
                    "left": {"kind": "slot_path", "slots": ["year"]},
                    "right": {"kind": "literal", "value": 2100},
                },
            ],
        },
        "severity": "warning",
        "message": "Year should be between 1888 and 2100",
    }
    resp = client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["spec_summary"]["constraints"] == 1

    spec = get_revision(conn, draft_id)
    con = spec.constraints[0]
    assert con.name == "year_plausible_range"
    from knot.ontology.metaschema import BoolExpr, BoolOpKind
    assert isinstance(con.body, BoolExpr)
    assert con.body.op == BoolOpKind.AND
    assert len(con.body.operands) == 2


# ---------------------------------------------------------------------------
# 3. 404 on unknown class
# ---------------------------------------------------------------------------

def test_unknown_primary_class_returns_404(draft_with_movie, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "bad_constraint",
        "primary_class_name": "DoesNotExist",
        "body": {"kind": "literal", "value": True},
    }
    resp = client_no_exc.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. 404 on unknown slot reference in body
# ---------------------------------------------------------------------------

def test_unknown_slot_in_body_returns_404(draft_with_movie, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "bad_slot_ref",
        "primary_class_name": "Movie",
        "body": {
            "kind": "compare",
            "op": "gte",
            "left": {"kind": "slot_path", "slots": ["no_such_slot"]},
            "right": {"kind": "literal", "value": 0},
        },
    }
    resp = client_no_exc.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 5. 409 on duplicate constraint name (case-twin)
# ---------------------------------------------------------------------------

def test_duplicate_constraint_name_returns_409(draft_with_movie, client, client_no_exc):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_check",
        "primary_class_name": "Movie",
        "body": {
            "kind": "compare",
            "op": "gte",
            "left": {"kind": "slot_path", "slots": ["year"]},
            "right": {"kind": "literal", "value": 1888},
        },
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
        "name": "bad name with spaces",   # fails _NAME_PATTERN
        "primary_class_name": "Movie",
        "body": {"kind": "literal", "value": True},
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
        "body": {
            "kind": "compare",
            "op": "gte",
            "left": {"kind": "slot_path", "slots": ["year"]},
            "right": {"kind": "literal", "value": 1888},
        },
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

    # Ingest a violating row (year = 1800 < 1888)
    ingest_resp2 = client.post(
        "/graph/ingest/imdb_movies",
        json={"rows": [{"imdb_id": "tt0000002", "year": 1800}]},
    )
    assert ingest_resp2.status_code == 200

    # Run constraint check — should report exactly one violation (tt0000002)
    check_resp = client.post("/graph/constraints/check")
    assert check_resp.status_code == 200, check_resp.text
    violations = check_resp.json()["violations"]
    assert len(violations) == 1
    assert violations[0]["rule_id"] == "year_not_before_cinema"
    assert violations[0]["class_name"] == "Movie"


def test_e2e_no_violations_when_all_rows_valid(draft_with_movie, client):
    conn, draft_id = draft_with_movie

    body = {
        "name": "year_not_before_cinema",
        "primary_class_name": "Movie",
        "body": {
            "kind": "compare",
            "op": "gte",
            "left": {"kind": "slot_path", "slots": ["year"]},
            "right": {"kind": "literal", "value": 1888},
        },
        "severity": "error",
    }
    client.post(f"/spec/drafts/{draft_id}/constraints", json=body)
    client.post(f"/spec/drafts/{draft_id}/publish")

    # Ingest only valid rows
    client.post(
        "/graph/ingest/imdb_movies",
        json={"rows": [
            {"imdb_id": "tt0000001", "year": 1900},
            {"imdb_id": "tt0000002", "year": 2000},
        ]},
    )

    check_resp = client.post("/graph/constraints/check")
    assert check_resp.status_code == 200
    assert check_resp.json()["violations"] == []
