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

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.db import graph_store
from knot.spec import OntologyClass, Primitive, Slot, Source, Spec
from knot.spec.metaschema import (
    Constraint,
    Severity,
    SourceBinding,
)
from tests._helpers import publish_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _build_spec_with_constraints(
    constraints: list[Constraint],
) -> tuple[Spec, OntologyClass, Source]:
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    spec = Spec(
        id="ingest_constraint_test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=constraints,
    )
    return spec, movie, src


def _year_gte_constraint(
    movie_cls: OntologyClass,
    threshold: int,
    name: str = "year_gte_1888",
    severity: Severity = Severity.ERROR,
) -> Constraint:
    return Constraint(name=name, primary=movie_cls, body=f"year >= {threshold}", severity=severity)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_db(pg_conn):
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


# ---------------------------------------------------------------------------
# 1. Happy path: rows pass all constraints → ingest succeeds
# ---------------------------------------------------------------------------


async def test_valid_rows_with_flag_succeed(clean_db, client):
    """Rows that satisfy the constraint land successfully with the flag."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    # Add constraint: year >= 1888
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    await publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt0000001", "year": 1994}]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted"] == 1

    # Row should be in the DB.
    async with db.connect() as conn2:
        rows = await graph_store.query_rows(
            conn2, cls=movie, predicate_sql=None, predicate_params=[]
        )
    assert len(rows) == 1
    assert rows[0]["imdb_id"] == "tt0000001"


# ---------------------------------------------------------------------------
# 2. Violation path: row fails constraint with flag → 422, no rows land
# ---------------------------------------------------------------------------


async def test_violating_row_with_flag_returns_422_and_rolls_back(clean_db, client_no_exc):
    """year=1500 violates year >= 1888 → 422, no row persisted."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    await publish_spec(conn, spec)

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
    async with db.connect() as conn2:
        rows = await graph_store.query_rows(
            conn2, cls=movie, predicate_sql=None, predicate_params=[]
        )
    assert rows == [], f"expected no rows, got {rows}"


# ---------------------------------------------------------------------------
# 3. WARNING-severity constraint: never blocks ingest
# ---------------------------------------------------------------------------


async def test_warning_constraint_does_not_block_ingest(clean_db, client):
    """A WARNING constraint violation with validate_constraints=true still allows ingest."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    warn_c = _year_gte_constraint(movie, 1888, name="year_warning", severity=Severity.WARNING)
    spec.constraints.append(warn_c)
    await publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_old", "year": 1500}]},
    )
    # WARNING should not block — expect 200.
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    # Row landed.
    async with db.connect() as conn2:
        rows = await graph_store.query_rows(
            conn2, cls=movie, predicate_sql=None, predicate_params=[]
        )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. Without the flag: violations do not block (existing behaviour)
# ---------------------------------------------------------------------------


async def test_violation_without_flag_does_not_block(clean_db, client):
    """Without validate_constraints=true, a violating row ingests without error."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    await publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb",  # no ?validate_constraints
        json={"rows": [{"imdb_id": "tt_old", "year": 1500}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    # Row landed despite violating the constraint.
    async with db.connect() as conn2:
        rows = await graph_store.query_rows(
            conn2, cls=movie, predicate_sql=None, predicate_params=[]
        )
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. Multiple constraints, one violated → 422 with that constraint reported
# ---------------------------------------------------------------------------


async def test_multiple_constraints_one_violated_reports_violation(clean_db, client_no_exc):
    """Two constraints; year=1500 fails the first, passes the second (year <= 9999)."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])

    # Constraint 1: year >= 1888 (violated by 1500)
    c1 = _year_gte_constraint(movie, 1888, name="year_min")
    # Constraint 2: year <= 9999 (satisfied by 1500)
    c2 = Constraint(name="year_max", primary=movie, body="year <= 9999", severity=Severity.ERROR)

    spec.constraints.extend([c1, c2])
    await publish_spec(conn, spec)

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


async def test_multiple_constraints_all_pass(clean_db, client):
    """Multiple constraints all satisfied → ingest succeeds."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])

    c1 = _year_gte_constraint(movie, 1888, name="year_min")
    c2 = Constraint(name="year_max", primary=movie, body="year <= 9999", severity=Severity.ERROR)
    spec.constraints.extend([c1, c2])
    await publish_spec(conn, spec)

    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_ok", "year": 2000}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1


# ---------------------------------------------------------------------------
# 7. Multiple violations in one batch → all reported
# ---------------------------------------------------------------------------


async def test_multiple_violating_rows_reported(clean_db, client_no_exc):
    """Batch with two violating rows: both violations are reported."""
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    c = _year_gte_constraint(movie, 1888)
    spec.constraints.append(c)
    await publish_spec(conn, spec)

    resp = client_no_exc.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={
            "rows": [
                {"imdb_id": "tt_bad1", "year": 1000},
                {"imdb_id": "tt_bad2", "year": 1500},
            ]
        },
    )
    assert resp.status_code == 422, resp.text
    violations = resp.json()["detail"]["violations"]
    offending_pks = {v["offending_pk"] for v in violations}
    assert any("tt_bad1" in pk for pk in offending_pks)
    assert any("tt_bad2" in pk for pk in offending_pks)

    # No rows landed.
    async with db.connect() as conn2:
        rows = await graph_store.query_rows(
            conn2, cls=movie, predicate_sql=None, predicate_params=[]
        )
    assert rows == []


# ---------------------------------------------------------------------------
# 8. Constraint on a different class does not block ingest
# ---------------------------------------------------------------------------


async def test_constraint_on_other_class_not_checked(clean_db, client):
    """A constraint on a class other than the source's class is not checked."""
    conn = clean_db

    # Build spec with Movie (source) + a second class with a constraint.
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb")
    movie_binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]

    # Second class: Person (no source in this test, just a class with a constraint)
    pid = Slot(name="pid", type=Primitive(name="string"), identifier=True, required=True)
    age = Slot(name="age", type=Primitive(name="integer"))
    person = OntologyClass(name="Person", slots=[pid, age])

    person_constraint = Constraint(
        name="age_non_negative", primary=person, body="age >= 0", severity=Severity.ERROR
    )
    psrc = Source(name="people")
    person_binding = SourceBinding(source=psrc, class_=person, identifier_slot=pid)  # type: ignore[call-arg]

    spec = Spec(
        id="multi_class_test",
        version="1.0.0",
        classes=[movie, person],
        sources=[src, psrc],
        source_bindings=[movie_binding, person_binding],
        constraints=[person_constraint],
    )
    await publish_spec(conn, spec)

    # Ingesting to Movie source — Person constraint must not interfere.
    resp = client.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt0000001", "year": 2000}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1


# ---------------------------------------------------------------------------
# 9. Compile failure surfaces as synthetic violation
# ---------------------------------------------------------------------------


async def test_compile_failure_surfaces_as_synthetic_violation(
    clean_db, client_no_exc, monkeypatch
):
    """When compile_constraint raises, the built-in constraint check
    reports it as a synthetic violation row (offending_pk='*', detail
    starts 'compile failure: ...') and rolls back the INSERT.
    """
    conn = clean_db
    spec, movie, src = _build_spec_with_constraints([])
    spec.constraints.append(_year_gte_constraint(movie, 1888, name="year_min"))
    await publish_spec(conn, spec)

    # Monkeypatch the compiler the ingest path calls, so any constraint
    # compile blows up. The built-in check is expected to catch and
    # surface it as synthetic.
    from knot.spec.compile import postgres as postgres_compile

    def _broken(constraint, cls):
        raise RuntimeError("synthetic boom")

    monkeypatch.setattr(postgres_compile, "compile_constraint", _broken)

    resp = client_no_exc.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_ok", "year": 2000}]},
    )
    assert resp.status_code == 422, resp.text
    violations = resp.json()["detail"]["violations"]
    assert len(violations) == 1
    v = violations[0]
    assert v["rule_id"] == "year_min"
    assert v["offending_pk"] == "*"
    assert v["detail"].startswith("compile failure:")
    assert "synthetic boom" in v["detail"]

    # Rollback: even on synthetic-violation surface, the txn rolls back.
    async with db.connect() as conn2:
        rows = await graph_store.query_rows(
            conn2, cls=movie, predicate_sql=None, predicate_params=[]
        )
    assert rows == []
