"""Spec rollback tests.

Rollback promotes a prior revision back to ``published``. The forward diff
(currently-published → target) is applied to the data plane just like a
normal publish — destructive changes are gated by ``allow_destructive``.

Coverage:
  1. Storage-layer rollback: publish v1, publish v2, rollback to v1 lands
     v1 as published, including the migration that drops the v2 additions.
  2. Rolling back to the currently-published revision is a no-op error
     at the API layer (400).
  3. Rollback to nonexistent revision → 404.
  4. Destructive rollback requires the allow_destructive flag.
  5. Round-trip rollback: v1 → v2 → v1 → v2 (re-promotion works).
  6. After rollback the data-plane schema matches the rolled-back-to spec.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.main import app
from knot.security import Principal, require_user
from knot.db.spec_store import (
    create_draft,
    get_published_revision,
    publish_draft,
    update_draft,
)
from knot.ontology import OntologyClass, Slot, Source, Spec, TypeDefinition


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _string_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _spec_v1() -> Spec:
    """Movie with imdb_id only."""
    st = _string_type()
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id="rollback_test",
        version="1.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[movie],
        sources=[src],
    )


def _spec_v2() -> Spec:
    """Movie with imdb_id + title (extra slot, plus a new Person class)."""
    st = _string_type()
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src_movie = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)

    nm = Slot(name="nm_id", range=st, identifier=True, required=True)
    person = OntologyClass(name="Person", slots=[nm])
    src_person = Source(name="imdb_people", entity_class=person, identifier_slot=nm)

    return Spec(
        id="rollback_test",
        version="2.0.0",
        types=[st],
        slots=[imdb_id, title, nm],
        classes=[movie, person],
        sources=[src_movie, src_person],
    )


@pytest.fixture
def clean(pg_conn):
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
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


# ---------------------------------------------------------------------------
# 1. Storage-layer rollback: publish_draft on the older revision works
# ---------------------------------------------------------------------------

def test_rollback_via_publish_draft(clean):
    """v1 → v2 → rollback to v1 by re-calling publish_draft(v1)."""
    rev1 = create_draft(clean)
    update_draft(clean, rev1, _spec_v1())
    publish_draft(clean, rev1)
    assert get_published_revision(clean) == rev1
    # After v1, knot_data.movie has only imdb_id.
    assert clean.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'knot_data' AND table_name = 'person'"
    ).fetchone()[0] == 0

    rev2 = create_draft(clean, parent_revision=rev1)
    update_draft(clean, rev2, _spec_v2())
    publish_draft(clean, rev2)
    assert get_published_revision(clean) == rev2
    # After v2, knot_data.person exists.
    assert clean.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'knot_data' AND table_name = 'person'"
    ).fetchone()[0] == 1

    # Rollback to v1: requires allow_destructive because the diff drops
    # Person + the title slot.
    publish_draft(clean, rev1, allow_destructive=True)
    assert get_published_revision(clean) == rev1
    # Person table should be gone.
    assert clean.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'knot_data' AND table_name = 'person'"
    ).fetchone()[0] == 0


# ---------------------------------------------------------------------------
# 2. API: rollback to a prior revision via the endpoint
# ---------------------------------------------------------------------------

def test_api_rollback_promotes_target(clean, client):
    rev1 = create_draft(clean)
    update_draft(clean, rev1, _spec_v1())
    publish_draft(clean, rev1)

    rev2 = create_draft(clean, parent_revision=rev1)
    update_draft(clean, rev2, _spec_v2())
    publish_draft(clean, rev2)

    r = client.post(f"/spec/rollback/{rev1}?allow_destructive=true")
    assert r.status_code == 200, r.text
    assert r.json()["revision"] == rev1
    assert get_published_revision(clean) == rev1


# ---------------------------------------------------------------------------
# 3. API: rollback to currently-published is a 400
# ---------------------------------------------------------------------------

def test_api_rollback_to_current_is_rejected(clean, client):
    rev1 = create_draft(clean)
    update_draft(clean, rev1, _spec_v1())
    publish_draft(clean, rev1)

    r = client.post(f"/spec/rollback/{rev1}")
    assert r.status_code == 400
    assert "already the published spec" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 4. API: rollback to nonexistent revision is a 404
# ---------------------------------------------------------------------------

def test_api_rollback_to_nonexistent_is_404(clean, client):
    rev1 = create_draft(clean)
    update_draft(clean, rev1, _spec_v1())
    publish_draft(clean, rev1)

    r = client.post("/spec/rollback/9999999")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 5. API: destructive rollback requires the flag
# ---------------------------------------------------------------------------

def test_api_rollback_destructive_requires_flag(clean, client):
    rev1 = create_draft(clean)
    update_draft(clean, rev1, _spec_v1())
    publish_draft(clean, rev1)

    rev2 = create_draft(clean, parent_revision=rev1)
    update_draft(clean, rev2, _spec_v2())
    publish_draft(clean, rev2)

    # No allow_destructive — diff drops Person and the title slot.
    r = client.post(f"/spec/rollback/{rev1}")
    assert r.status_code == 400
    assert "destructive" in r.json()["detail"].lower()
    # Still on v2.
    assert get_published_revision(clean) == rev2

    # With the flag, rollback succeeds.
    r = client.post(f"/spec/rollback/{rev1}?allow_destructive=true")
    assert r.status_code == 200
    assert get_published_revision(clean) == rev1


# ---------------------------------------------------------------------------
# 6. Round-trip: v1 → v2 → v1 → v2 (re-promote a previously-published)
# ---------------------------------------------------------------------------

def test_rollback_then_forward_again(clean):
    rev1 = create_draft(clean)
    update_draft(clean, rev1, _spec_v1())
    publish_draft(clean, rev1)

    rev2 = create_draft(clean, parent_revision=rev1)
    update_draft(clean, rev2, _spec_v2())
    publish_draft(clean, rev2)

    # Rollback to v1.
    publish_draft(clean, rev1, allow_destructive=True)
    assert get_published_revision(clean) == rev1

    # Re-promote v2 (forward "rollback" — same operation).
    publish_draft(clean, rev2)
    assert get_published_revision(clean) == rev2
    # Person table re-created.
    assert clean.execute(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = 'knot_data' AND table_name = 'person'"
    ).fetchone()[0] == 1
