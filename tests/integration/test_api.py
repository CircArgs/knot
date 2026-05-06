"""Integration tests for knot's FastAPI surface.

Uses FastAPI TestClient backed by a live postgres instance.
Requires the control stack to be up (bash scripts/up.sh).

The conftest.py in this directory (tests/integration/) overrides
_apply_control_schema as a no-op for compiler tests that don't need postgres.
These tests need postgres, so they apply the schema themselves via the
postgres_dsn fixture from tests/conftest.py.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from knot.control_db import apply_schema


# ---------------------------------------------------------------------------
# Minimal impl source strings for testing
# ---------------------------------------------------------------------------

_GOOD_IMPL_SOURCE = """\
from knot.protocols import ERProtocol

class MyERImpl(ERProtocol):
    pass
"""

_BAD_IMPL_SOURCE = """\
from knot.protocols import ERProtocol
from tests.fixtures.B2.spec import Movie

class BrokenImpl(ERProtocol):
    from knot.protocols import DataContext
    ctx = DataContext(primary=Movie, where=Movie.nonexistent_slot.is_not_null())
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module", autouse=True)
def _ensure_schema(postgres_dsn):
    """Apply control schema before API tests (module-scoped so it runs once)."""
    apply_schema(postgres_dsn)


@pytest.fixture(scope="module")
def client():
    """FastAPI TestClient with the knot app."""
    # Import here so DSN is already set via env when running against test postgres.
    from knot.api import app, DSN
    import knot.api as api_module

    # Point the module-level DSN to the test database.
    api_module.DSN = "postgresql://knot:knot@localhost:5432/knot_control"

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def pg(postgres_dsn):
    """Psycopg connection for asserting DB state."""
    conn = psycopg.connect(postgres_dsn, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def _clean_tables(pg):
    """Truncate test-owned rows before each test to ensure isolation."""
    # Order matters: FK deps.
    pg.execute("DELETE FROM _user_er_decisions")
    pg.execute("DELETE FROM _user_corrections")
    pg.execute("DELETE FROM bound_impls")
    pg.execute("DELETE FROM impl_config")
    pg.execute("DELETE FROM pipeline_runs")
    # compiled_workflows rows are content-addressed / immutable — leave them.
    pg.execute("DELETE FROM impl_revision")
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_post_impl_revision_validates_and_stores(client, pg):
    """Registering a valid impl source returns 200 and writes a row."""
    resp = client.post("/impls/MyERImpl/revisions", json={"source_bytes": _GOOD_IMPL_SOURCE})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["revision"] == 1
    assert len(data["content_hash"]) == 64
    assert len(data["pinned_spec_hash"]) == 64

    row = pg.execute(
        "SELECT name, revision, content_hash FROM impl_revision WHERE name = 'MyERImpl'",
    ).fetchone()
    assert row is not None
    assert row[0] == "MyERImpl"
    assert row[1] == 1
    assert row[2] == data["content_hash"]


def test_post_impl_revision_increments_revision(client, pg):
    """Posting a second revision increments to 2."""
    client.post("/impls/MyERImpl/revisions", json={"source_bytes": _GOOD_IMPL_SOURCE})
    resp = client.post("/impls/MyERImpl/revisions", json={"source_bytes": _GOOD_IMPL_SOURCE})
    assert resp.status_code == 200, resp.text
    assert resp.json()["revision"] == 2


def test_post_impl_revision_rejects_broken_datacontext(client):
    """Impl with a nonexistent slot reference returns 400 with field path info."""
    resp = client.post("/impls/BrokenImpl/revisions", json={"source_bytes": _BAD_IMPL_SOURCE})
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    # Error message includes the bad reference (nonexistent_slot or similar)
    assert "nonexistent_slot" in detail or "BrokenImpl" in detail or "Slot" in detail


def test_post_config_revision(client, pg):
    """POST /configs writes a config snapshot and returns revision + hash."""
    config_payload = {"threshold": 0.8, "mode": "strict"}
    resp = client.post("/configs/MyERImpl/revisions", json={"config": config_payload})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["revision"] == 1
    assert len(data["content_hash"]) == 64

    row = pg.execute(
        "SELECT impl_name, revision, config_snapshot FROM impl_config WHERE impl_name = 'MyERImpl'",
    ).fetchone()
    assert row is not None
    assert row[0] == "MyERImpl"
    assert row[1] == 1
    snap = row[2] if isinstance(row[2], dict) else json.loads(row[2])
    assert snap["threshold"] == 0.8


def test_put_bound_impl(client, pg):
    """Register impl + config, bind to (resolve, Movie), verify bound_impls row."""
    # Register impl.
    r1 = client.post("/impls/MyERImpl/revisions", json={"source_bytes": _GOOD_IMPL_SOURCE})
    assert r1.status_code == 200
    impl_rev = r1.json()["revision"]

    # Register config.
    r2 = client.post("/configs/MyERImpl/revisions", json={"config": {"k": "v"}})
    assert r2.status_code == 200
    cfg_rev = r2.json()["revision"]

    # Bind.
    r3 = client.put(
        "/bound_impls/resolve/Movie",
        json={"impl_name": "MyERImpl", "revision": impl_rev, "config_revision": cfg_rev},
    )
    assert r3.status_code == 200, r3.text
    data = r3.json()
    assert data["stage"] == "resolve"
    assert data["class_name"] == "Movie"
    assert data["impl_name"] == "MyERImpl"
    assert data["current_revision"] == impl_rev
    assert data["current_config_revision"] == cfg_rev

    row = pg.execute(
        "SELECT impl_name, current_revision FROM bound_impls WHERE stage='resolve' AND class_name='Movie'",
    ).fetchone()
    assert row is not None
    assert row[0] == "MyERImpl"


def test_post_runs_compiles_and_persists(client, pg):
    """POST /runs with scope='Movie' returns 200 with compile_hash; verifies DB rows."""
    resp = client.post("/runs", json={"scope": "Movie"})
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "run_id" in data
    assert len(data["compile_hash"]) == 64
    assert "workflow_spec" in data
    assert "stages" in data["workflow_spec"]

    # compiled_workflows row exists.
    wf_row = pg.execute(
        "SELECT hash FROM compiled_workflows WHERE hash = %s",
        (data["compile_hash"],),
    ).fetchone()
    assert wf_row is not None

    # pipeline_runs row exists.
    run_row = pg.execute(
        "SELECT id, compile_hash, scope, status FROM pipeline_runs WHERE id = %s",
        (data["run_id"],),
    ).fetchone()
    assert run_row is not None
    assert run_row[1].strip() == data["compile_hash"]
    assert run_row[2] == "Movie"
    assert run_row[3] == "pending"


def test_get_run_returns_workflow_spec(client):
    """GET /runs/{run_id} returns the run's status and WorkflowSpec."""
    post_resp = client.post("/runs", json={"scope": "Movie"})
    assert post_resp.status_code == 200
    run_id = post_resp.json()["run_id"]
    compile_hash = post_resp.json()["compile_hash"]

    get_resp = client.get(f"/runs/{run_id}")
    assert get_resp.status_code == 200, get_resp.text
    data = get_resp.json()

    assert data["run_id"] == run_id
    assert data["compile_hash"] == compile_hash
    assert data["scope"] == "Movie"
    assert data["status"] == "pending"
    assert "stages" in data["workflow_spec"]


def test_get_run_404(client):
    """GET /runs/999999 returns 404."""
    resp = client.get("/runs/999999")
    assert resp.status_code == 404


def test_get_spec(client):
    """GET /spec returns canonical-dumped JSON of the current spec."""
    resp = client.get("/spec")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # B2 spec has id "b2_integration" and three classes.
    assert data.get("id") == "b2_integration"
    assert "classes" in data
    # classes list may contain $ref entries for nodes already serialised inline
    # (canonical cycle-breaking); collect names from both full objects and $refs.
    class_names: set[str] = set()
    for c in data["classes"]:
        if "name" in c:
            class_names.add(c["name"])
        elif "$ref" in c:
            class_names.add(c["$ref"])
    assert "Movie" in class_names
    assert "Person" in class_names
    assert "Credit" in class_names


def test_get_bound_impls(client):
    """GET /bound_impls returns current bindings."""
    # Register and bind one impl.
    r1 = client.post("/impls/MyERImpl/revisions", json={"source_bytes": _GOOD_IMPL_SOURCE})
    assert r1.status_code == 200
    rev = r1.json()["revision"]
    client.put("/bound_impls/resolve/Movie", json={"impl_name": "MyERImpl", "revision": rev})

    resp = client.get("/bound_impls")
    assert resp.status_code == 200
    items = resp.json()
    assert any(i["stage"] == "resolve" and i["class_name"] == "Movie" for i in items)


def test_post_corrections(client, pg):
    """POST /corrections writes to _user_corrections."""
    resp = client.post(
        "/corrections",
        json={"class_name": "Movie", "canonical_id": "tt0000001", "slot": "title", "value": "Fixed Title"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["class_name"] == "Movie"
    assert data["slot_name"] == "title"

    row = pg.execute(
        "SELECT class_name, canonical_id, slot_name FROM _user_corrections WHERE id = %s",
        (data["id"],),
    ).fetchone()
    assert row is not None
    assert row[0] == "Movie"
    assert row[1] == "tt0000001"
    assert row[2] == "title"


def test_post_er_decisions(client, pg):
    """POST /er_decisions writes a force_merge decision."""
    resp = client.post(
        "/er_decisions",
        json={
            "class_name": "Movie",
            "decision_type": "force_merge",
            "canonical_ids": ["tt0000001", "tt0000002"],
            "reason": "known duplicate",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["decision_type"] == "force_merge"
    assert set(data["canonical_ids"]) == {"tt0000001", "tt0000002"}

    row = pg.execute(
        "SELECT decision_type, canonical_ids FROM _user_er_decisions WHERE id = %s",
        (data["id"],),
    ).fetchone()
    assert row is not None
    assert row[0] == "force_merge"
    assert set(row[1]) == {"tt0000001", "tt0000002"}


def test_post_er_decisions_invalid_type(client):
    """POST /er_decisions with bad decision_type returns 400."""
    resp = client.post(
        "/er_decisions",
        json={"class_name": "Movie", "decision_type": "bad_type", "canonical_ids": ["x"]},
    )
    assert resp.status_code == 400
