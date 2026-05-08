"""Tests for POST /graph/ingest/{source_name}?validate_constraints=true.

Coverage:
  1. Happy path: rows pass all ERROR constraints → ingest succeeds, rows land.
  2. Violation path: row fails constraint with flag → 422, no rows land.
  3. WARNING-severity constraint with flag → does NOT block ingest.
  4. Without the flag: violations don't block (existing behaviour preserved).
  5. Multiple constraints, first violated → 422 with that constraint reported.
  6. Multiple constraints, all pass → ingest succeeds.
  7. Multiple violations across multiple constraints → all reported.
  8. Only constraints whose primary_class matches the source's class are checked.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.db import graph_store, spec_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.metaschema import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
    Literal_,
    Severity,
    SlotPath,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _build_spec_with_constraints(constraints: list[Constraint]) -> tuple[Spec, OntologyClass, Source]:
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="ingest_constraint_test",
        version="1.0.0",
        types=[st, it],
        slots=[imdb_id, year],
        classes=[movie],
        sources=[src],
        constraints=constraints,
    )
    return spec, movie, src


def _year_gte_constraint(movie_cls: OntologyClass, threshold: int, name: str = "year_gte_1888",
                          severity: Severity = Severity.ERROR) -> Constraint:
    year_slot = next(s for s in movie_cls.slots if s.name == "year")
    path = SlotPath(from_class=movie_cls, slots=[year_slot])
    body = Compare(op=CompareOp.GTE, left=path, right=Literal_(value=threshold))
    return Constraint(name=name, primary=movie_cls, body=body, severity=severity)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_db(pg_conn):
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


def _publish_spec(conn, spec: Spec) -> int:
    rev = create_draft(conn)
    update_draft(conn, rev, spec)
    publish_draft(conn, rev)
    return rev


# ---------------------------------------------------------------------------
# 1. Happy path: rows pass all constraints → ingest succeeds
# ---------------------------------------------------------------------------

def test_valid_rows_with_flag_succeed(clean_db, client):
    """Rows that satisfy the constraint land successfully with the flag."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    # Add constraint: year >= 1888
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    _publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt0000001", "year": 1994}]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted"] == 1

    # Row should be in the DB.
    with db.connect() as conn2:
        rows = graph_store.query_rows(conn2, cls=movie, predicate_sql=None, predicate_params=[])
    assert len(rows) == 1
    assert rows[0]["imdb_id"] == "tt0000001"


# ---------------------------------------------------------------------------
# 2. Violation path: row fails constraint with flag → 422, no rows land
# ---------------------------------------------------------------------------

def test_violating_row_with_flag_returns_422_and_rolls_back(clean_db, client_no_exc):
    """year=1500 violates year >= 1888 → 422, no row persisted."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    _publish_spec(conn, spec)

    resp = client_no_exc.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_bad", "year": 1500}]},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "violations" in detail
    violations = detail["violations"]
    assert len(violations) >= 1
    assert violations[0]["rule_id"] == "year_gte_1888"

    # No rows should have landed.
    with db.connect() as conn2:
        rows = graph_store.query_rows(conn2, cls=movie, predicate_sql=None, predicate_params=[])
    assert rows == [], f"expected no rows, got {rows}"


# ---------------------------------------------------------------------------
# 3. WARNING-severity constraint: never blocks ingest
# ---------------------------------------------------------------------------

def test_warning_constraint_does_not_block_ingest(clean_db, client):
    """A WARNING constraint violation with validate_constraints=true still allows ingest."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    warn_c = _year_gte_constraint(movie, 1888, name="year_warning", severity=Severity.WARNING)
    spec.constraints.append(warn_c)
    _publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_old", "year": 1500}]},
    )
    # WARNING should not block — expect 200.
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    # Row landed.
    with db.connect() as conn2:
        rows = graph_store.query_rows(conn2, cls=movie, predicate_sql=None, predicate_params=[])
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. Without the flag: violations do not block (existing behaviour)
# ---------------------------------------------------------------------------

def test_violation_without_flag_does_not_block(clean_db, client):
    """Without validate_constraints=true, a violating row ingests without error."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    _publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb",   # no ?validate_constraints
        json={"rows": [{"imdb_id": "tt_old", "year": 1500}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    # Row landed despite violating the constraint.
    with db.connect() as conn2:
        rows = graph_store.query_rows(conn2, cls=movie, predicate_sql=None, predicate_params=[])
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. Multiple constraints, one violated → 422 with that constraint reported
# ---------------------------------------------------------------------------

def test_multiple_constraints_one_violated_reports_violation(clean_db, client_no_exc):
    """Two constraints; year=1500 fails the first, passes the second (year <= 9999)."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])

    year_slot = next(s for s in movie.slots if s.name == "year")
    # Constraint 1: year >= 1888 (violated by 1500)
    c1 = _year_gte_constraint(movie, 1888, name="year_min")
    # Constraint 2: year <= 9999 (satisfied by 1500)
    path = SlotPath(from_class=movie, slots=[year_slot])
    body2 = Compare(op=CompareOp.LTE, left=path, right=Literal_(value=9999))
    c2 = Constraint(name="year_max", primary=movie, body=body2, severity=Severity.ERROR)

    spec.constraints.extend([c1, c2])
    _publish_spec(conn, spec)

    resp = client_no_exc.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_bad", "year": 1500}]},
    )
    assert resp.status_code == 422, resp.text
    violations = resp.json()["detail"]["violations"]
    rule_ids = {v["rule_id"] for v in violations}
    assert "year_min" in rule_ids
    assert "year_max" not in rule_ids


# ---------------------------------------------------------------------------
# 6. Multiple constraints, all pass → ingest succeeds
# ---------------------------------------------------------------------------

def test_multiple_constraints_all_pass(clean_db, client):
    """Multiple constraints all satisfied → ingest succeeds."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])

    year_slot = next(s for s in movie.slots if s.name == "year")
    c1 = _year_gte_constraint(movie, 1888, name="year_min")
    path = SlotPath(from_class=movie, slots=[year_slot])
    body2 = Compare(op=CompareOp.LTE, left=path, right=Literal_(value=9999))
    c2 = Constraint(name="year_max", primary=movie, body=body2, severity=Severity.ERROR)
    spec.constraints.extend([c1, c2])
    _publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_ok", "year": 2000}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1


# ---------------------------------------------------------------------------
# 7. Multiple violations in one batch → all reported
# ---------------------------------------------------------------------------

def test_multiple_violating_rows_reported(clean_db, client_no_exc):
    """Batch with two violating rows: both violations are reported."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    _publish_spec(conn, spec)

    resp = client_no_exc.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [
            {"imdb_id": "tt_bad1", "year": 1000},
            {"imdb_id": "tt_bad2", "year": 1500},
        ]},
    )
    assert resp.status_code == 422, resp.text
    violations = resp.json()["detail"]["violations"]
    offending_pks = {v["offending_pk"] for v in violations}
    assert "tt_bad1" in offending_pks
    assert "tt_bad2" in offending_pks

    # No rows landed.
    with db.connect() as conn2:
        rows = graph_store.query_rows(conn2, cls=movie, predicate_sql=None, predicate_params=[])
    assert rows == []


# ---------------------------------------------------------------------------
# 8. Constraint on a different class does not block ingest
# ---------------------------------------------------------------------------

def test_constraint_on_other_class_not_checked(clean_db, client):
    """A constraint on a class other than the source's class is not checked."""
    conn = clean_db

    # Build spec with Movie (source) + a second class with a constraint.
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)

    # Second class: Person (no source in this test, just a class with a constraint)
    pid = Slot(name="pid", range=st, identifier=True, required=True)
    age = Slot(name="age", range=it)
    person = OntologyClass(name="Person", slots=[pid, age])

    path = SlotPath(from_class=person, slots=[age])
    body = Compare(op=CompareOp.GTE, left=path, right=Literal_(value=0))
    person_constraint = Constraint(
        name="age_non_negative", primary=person, body=body, severity=Severity.ERROR
    )
    psrc = Source(name="people", entity_class=person, identifier_slot=pid)

    spec = Spec(
        id="multi_class_test",
        version="1.0.0",
        types=[st, it],
        slots=[imdb_id, year, pid, age],
        classes=[movie, person],
        sources=[src, psrc],
        constraints=[person_constraint],
    )
    rev = create_draft(conn)
    update_draft(conn, rev, spec)
    publish_draft(conn, rev)

    # Ingesting to Movie source — Person constraint must not interfere.
    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt0000001", "year": 2000}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1
