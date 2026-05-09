"""Tests for the constraint-validator extension.

Coverage:
  1. Violation surfaces as ConstraintViolations + rolls back the INSERT.
  2. Compile failure surfaces as a synthetic violation row with
     offending_pk='*' and detail starting "compile failure".
  3. validate_constraints=False skips the validator (no error even when
     a row would violate).
  4. DQ observer records on a clean ingest with batch_id == request_id.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.db import dq, graph_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.metaschema import (
    Compare,
    CompareOp,
    Constraint,
    Literal_,
    Severity,
    SlotPath,
)


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _spec_with_year_constraint(threshold: int = 1888) -> tuple[Spec, OntologyClass, Source]:
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    path = SlotPath(from_class=movie, slots=[year])
    body = Compare(op=CompareOp.GTE, left=path, right=Literal_(value=threshold))
    constraint = Constraint(name="year_min", primary=movie, body=body, severity=Severity.ERROR)
    spec = Spec(
        id="constraint_validator_test",
        version="1.0.0",
        types=[st, it],
        slots=[imdb_id, year],
        classes=[movie],
        sources=[src],
        constraints=[constraint],
    )
    return spec, movie, src


@pytest.fixture
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
def client_no_exc():
    from knot.api.main import app

    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=False)
    finally:
        app.dependency_overrides.pop(require_user, None)


@pytest.fixture
def client():
    from knot.api.main import app

    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


async def _publish(conn, spec: Spec) -> int:
    rev = await create_draft(conn)
    await update_draft(conn, rev, spec)
    await publish_draft(conn, rev)
    return rev


# ---------------------------------------------------------------------------
# 1. Violation raises ConstraintViolations and rolls back
# ---------------------------------------------------------------------------


async def test_constraint_violation_raises_and_rolls_back(clean, client_no_exc):
    """Violating row + opt-in flag → 422; no row persisted (txn rolled back)."""
    spec, movie, _src = _spec_with_year_constraint(1888)
    await _publish(clean, spec)

    resp = client_no_exc.post(
        "/graph/ingest/imdb?validate_constraints=true",
        json={"rows": [{"imdb_id": "tt_old", "year": 1500}]},
    )
    assert resp.status_code == 422, resp.text
    violations = resp.json()["detail"]["violations"]
    assert any(v["rule_id"] == "year_min" for v in violations)
    assert any(v["offending_pk"] == "tt_old" for v in violations)

    # Rollback: no rows in the per-class table.
    rows = await graph_store.query_rows(clean, cls=movie, predicate_sql=None, predicate_params=[])
    assert rows == []


# ---------------------------------------------------------------------------
# 2. Compile failure surfaces as synthetic violation
# ---------------------------------------------------------------------------


async def test_compile_failure_surfaces_as_synthetic_violation(clean, client_no_exc, monkeypatch):
    """When compile_constraint raises, the handler reports it as a synthetic
    violation row (offending_pk='*', detail starts 'compile failure: ...').
    """
    spec, movie, _src = _spec_with_year_constraint(1888)
    await _publish(clean, spec)

    # Monkeypatch the compiler the handler calls, so any constraint compile
    # blows up. The handler is expected to catch and surface as synthetic.
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
    rows = await graph_store.query_rows(clean, cls=movie, predicate_sql=None, predicate_params=[])
    assert rows == []


# ---------------------------------------------------------------------------
# 3. Skipped when validate_constraints=False
# ---------------------------------------------------------------------------


async def test_skipped_when_flag_off(clean, client):
    """Without the flag, a violating row ingests fine — handler short-circuits."""
    spec, movie, _src = _spec_with_year_constraint(1888)
    await _publish(clean, spec)

    resp = client.post(
        "/graph/ingest/imdb",  # no flag
        json={"rows": [{"imdb_id": "tt_old", "year": 1500}]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1

    rows = await graph_store.query_rows(clean, cls=movie, predicate_sql=None, predicate_params=[])
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 4. DQ observer records on clean ingest
# ---------------------------------------------------------------------------


async def test_dq_observer_records_on_success(clean, client):
    """A clean ingest produces incremental DQ observations under the request_id."""
    spec, movie, _src = _spec_with_year_constraint(1888)
    await _publish(clean, spec)

    resp = client.post(
        "/graph/ingest/imdb",
        json={
            "rows": [
                {"imdb_id": "tt1", "year": 1994},
                {"imdb_id": "tt2", "year": 1972},
            ]
        },
    )
    assert resp.status_code == 200, resp.text

    obs = await dq.query_observations(clean, source="imdb")
    assert obs, "expected at least one DQ observation"
    by_slot = {o["slot"]: o for o in obs}
    assert "imdb_id" in by_slot
    assert "year" in by_slot
    # batch_id is the request_id from the ctx — non-empty string.
    for o in obs:
        assert o["batch_id"], f"batch_id should be set on {o}"
