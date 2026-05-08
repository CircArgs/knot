"""Data-quality observation tests.

Coverage:
  1. Incremental write on /graph/ingest emits one observation per stored slot.
  2. Stats math: row_count, null_count, distinct_count, min, max.
  3. Add correction emits incremental obs under the _user_corrections source.
  4. Property correction emits an obs only for the touched slot.
  5. Full scan reads aggregate stats from the per-class table and writes
     full_scan rows.
  6. /dq/observations time-series read with filters.
  7. /dq/observations/summary roll-up: null_rate is total_nulls / total_rows.
  8. /dq/scan rejects when no spec is published.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.main import app
from knot.security import Principal, require_user
from knot.db import dq, spec_store
from knot.db._naming import USER_CORRECTIONS_SOURCE
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.graph.corrections import apply_add, apply_property_correction
from knot.ontology import OntologyClass, Slot, Source, Spec, TypeDefinition


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _spec() -> tuple[Spec, OntologyClass, Source]:
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="dq", version="1.0.0",
        types=[st, it],
        slots=[imdb_id, title, year],
        classes=[movie],
        sources=[src],
    )
    return spec, movie, src


@pytest.fixture
def clean(pg_conn):
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE dq_observations CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()
    yield pg_conn


@pytest.fixture
def client():
    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


@pytest.fixture
def published(clean):
    spec, movie, src = _spec()
    rev = create_draft(clean)
    update_draft(clean, rev, spec)
    publish_draft(clean, rev)
    yield clean, movie, src, rev


# ---------------------------------------------------------------------------
# 1. Ingest emits one observation per stored slot
# ---------------------------------------------------------------------------

def test_ingest_emits_dq_observations(published, client):
    conn, movie, src, rev = published
    r = client.post(
        "/graph/ingest/imdb",
        json={
            "rows": [
                {"imdb_id": "tt1", "title": "Shawshank", "year": 1994},
                {"imdb_id": "tt2", "title": None, "year": 1972},
                {"imdb_id": "tt3", "title": "Pulp Fiction", "year": 1994},
            ],
        },
    )
    assert r.status_code == 200, r.text

    obs = dq.query_observations(conn, source="imdb")
    by_slot = {o["slot"]: o for o in obs}
    assert set(by_slot) == {"imdb_id", "title", "year"}

    # imdb_id: 3 rows, 0 nulls, 3 distinct
    o = by_slot["imdb_id"]
    assert o["row_count"] == 3
    assert o["null_count"] == 0
    assert o["distinct_count"] == 3

    # title: 3 rows, 1 null, 2 distinct (None doesn't count toward distinct)
    o = by_slot["title"]
    assert o["row_count"] == 3
    assert o["null_count"] == 1
    assert o["distinct_count"] == 2

    # year: 3 rows, 0 nulls, 2 distinct (1994 appears twice)
    o = by_slot["year"]
    assert o["row_count"] == 3
    assert o["null_count"] == 0
    assert o["distinct_count"] == 2
    assert o["min_value"] == "1972"
    assert o["max_value"] == "1994"

    # All tagged incremental and share the same batch_id (request id).
    assert all(o["kind"] == "incremental" for o in obs)
    batch_ids = {o["batch_id"] for o in obs}
    assert len(batch_ids) == 1


# ---------------------------------------------------------------------------
# 2. Add correction emits obs under _user_corrections
# ---------------------------------------------------------------------------

def test_add_correction_emits_dq(published):
    conn, movie, src, rev = published
    apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_synth",
        values={"imdb_id": "tt_synth", "title": "Synthetic", "year": 2026},
        spec_revision=rev,
    )
    obs = dq.query_observations(conn, source=USER_CORRECTIONS_SOURCE)
    by_slot = {o["slot"]: o for o in obs}
    assert by_slot["title"]["row_count"] == 1
    assert by_slot["title"]["null_count"] == 0
    assert by_slot["year"]["row_count"] == 1


# ---------------------------------------------------------------------------
# 3. Property correction emits obs ONLY for the touched slot
# ---------------------------------------------------------------------------

def test_property_correction_emits_dq_for_one_slot(published):
    conn, movie, src, rev = published
    apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_prop",
        values={"imdb_id": "tt_prop", "title": "Old", "year": 2000},
        spec_revision=rev,
    )

    # Clear add-correction obs from the prior call so we isolate the
    # property-correction effect.
    conn.execute("DELETE FROM dq_observations")

    apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_prop",
        slot_name="title",
        value="New Title",
        spec_revision=rev,
    )

    obs = dq.query_observations(conn, source=USER_CORRECTIONS_SOURCE)
    assert {o["slot"] for o in obs} == {"title"}
    assert obs[0]["row_count"] == 1
    assert obs[0]["null_count"] == 0
    assert obs[0]["distinct_count"] == 1


# ---------------------------------------------------------------------------
# 4. Full scan aggregates from the per-class table
# ---------------------------------------------------------------------------

def test_full_scan_writes_full_scan_rows(published, client):
    conn, movie, src, rev = published
    r = client.post(
        "/graph/ingest/imdb",
        json={
            "rows": [
                {"imdb_id": "tt1", "title": "A", "year": 1950},
                {"imdb_id": "tt2", "title": None, "year": 2000},
                {"imdb_id": "tt3", "title": "C", "year": 1950},
            ],
        },
    )
    assert r.status_code == 200, r.text
    # Drop prior incremental obs so the scan rows are clearly distinguishable.
    conn.execute("DELETE FROM dq_observations")

    r = client.post("/dq/scan?source=imdb")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["observations_inserted"] >= 3  # one per stored slot

    obs = dq.query_observations(conn, source="imdb", kind="full_scan")
    by_slot = {o["slot"]: o for o in obs}
    assert by_slot["title"]["row_count"] == 3
    assert by_slot["title"]["null_count"] == 1
    # full_scan rows have NULL batch_id.
    assert all(o["batch_id"] is None for o in obs)


# ---------------------------------------------------------------------------
# 5. GET /dq/observations API surface (filters + limit)
# ---------------------------------------------------------------------------

def test_api_observations_endpoint(published, client):
    conn, movie, src, rev = published
    r = client.post(
        "/graph/ingest/imdb",
        json={"rows": [{"imdb_id": "tt1", "title": "x", "year": 2000}]},
    )
    assert r.status_code == 200

    r = client.get("/dq/observations?source=imdb&class=Movie&slot=title")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["source"] == "imdb"
    assert rows[0]["class"] == "Movie"
    assert rows[0]["slot"] == "title"


# ---------------------------------------------------------------------------
# 6. GET /dq/observations/summary — null_rate computation
# ---------------------------------------------------------------------------

def test_api_summary_null_rate(published, client):
    conn, movie, src, rev = published
    r = client.post(
        "/graph/ingest/imdb",
        json={
            "rows": [
                {"imdb_id": "tt1", "title": "a", "year": 2000},
                {"imdb_id": "tt2", "title": None, "year": 2001},
            ],
        },
    )
    assert r.status_code == 200

    r = client.get("/dq/observations/summary")
    assert r.status_code == 200, r.text
    rows = r.json()
    title_row = next(r for r in rows if r["slot"] == "title")
    assert title_row["total_rows"] == 2
    assert title_row["total_nulls"] == 1
    assert title_row["null_rate"] == 0.5


# ---------------------------------------------------------------------------
# 7. /dq/scan rejects when no spec is published
# ---------------------------------------------------------------------------

def test_scan_rejected_without_published_spec(clean, client):
    r = client.post("/dq/scan")
    assert r.status_code == 409
    assert "no spec is published" in r.json()["detail"].lower()
