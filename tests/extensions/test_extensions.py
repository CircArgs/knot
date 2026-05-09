"""Tests for the master dispatcher + ER extension.

Coverage:
  1. Dispatcher priority ordering (lower number runs first)
  2. Multiple handlers on same event type all run
  3. isinstance matching works for subclasses
  4. No matching handler is a no-op (event unchanged)
  5. ER default resolver returns identifier-slot values
  6. ER class-specific resolver overrides the default
  7. End-to-end via ingest route: _canonical_id set from default ER
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.db import graph_store, spec_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.extensions import _Dispatcher
from knot.extensions.events import IngestResolveCanonical
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.api.auth.security import Principal, require_user


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _build_movie_spec() -> tuple[Spec, OntologyClass, Source]:
    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="ext_test",
        version="1.0.0",
        types=[st],
        slots=[imdb_id, title],
        classes=[movie],
        sources=[src],
    )
    return spec, movie, src


# ---------------------------------------------------------------------------
# 1. Dispatcher priority ordering
# ---------------------------------------------------------------------------


def test_dispatcher_priority_ordering():
    """Lower priority number runs first."""
    d = _Dispatcher()
    order: list[int] = []

    @d.on(IngestResolveCanonical, priority=200)
    def _high(ev):
        order.append(200)

    @d.on(IngestResolveCanonical, priority=10)
    def _low(ev):
        order.append(10)

    @d.on(IngestResolveCanonical, priority=100)
    def _mid(ev):
        order.append(100)

    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="s", entity_class=movie, identifier_slot=imdb_id)

    ev = IngestResolveCanonical(cls=movie, source=src, incoming=[])
    d.dispatch(ev)

    assert order == [10, 100, 200]


# ---------------------------------------------------------------------------
# 2. Multiple handlers on same event type all run
# ---------------------------------------------------------------------------


def test_dispatcher_multiple_handlers_all_run():
    d = _Dispatcher()
    calls: list[str] = []

    @d.on(IngestResolveCanonical)
    def _a(ev):
        calls.append("a")

    @d.on(IngestResolveCanonical)
    def _b(ev):
        calls.append("b")

    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="s", entity_class=movie, identifier_slot=imdb_id)
    ev = IngestResolveCanonical(cls=movie, source=src, incoming=[])

    d.dispatch(ev)
    assert "a" in calls
    assert "b" in calls
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 3. isinstance matching works for subclasses
# ---------------------------------------------------------------------------


def test_dispatcher_isinstance_matches_subclass():
    """A handler registered on a base class fires for subclass events."""
    d = _Dispatcher()
    fired: list[bool] = []

    class BaseEvent:
        pass

    class DerivedEvent(BaseEvent):
        pass

    @d.on(BaseEvent)
    def _base_handler(ev):
        fired.append(True)

    d.dispatch(DerivedEvent())
    assert fired == [True]


# ---------------------------------------------------------------------------
# 4. No matching handler is a no-op
# ---------------------------------------------------------------------------


def test_dispatcher_no_handler_is_noop():
    d = _Dispatcher()

    class UnrelatedEvent:
        pass

    class TargetEvent:
        pass

    calls: list[str] = []

    @d.on(TargetEvent)
    def _target(ev):
        calls.append("fired")

    d.dispatch(UnrelatedEvent())
    assert calls == []


# ---------------------------------------------------------------------------
# 5. ER default resolver returns identifier-slot values
# ---------------------------------------------------------------------------


def test_er_default_resolver_returns_id_slot_values():
    """The default ER resolver returns str(row[identifier_slot.name])."""
    from knot.extensions import er

    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)

    rows = [
        {"imdb_id": "tt0000001", "title": "Film A"},
        {"imdb_id": "tt0000002", "title": "Film B"},
    ]
    ev = IngestResolveCanonical(cls=movie, source=src, incoming=rows)
    result = er._default(ev)

    assert result == ["tt0000001", "tt0000002"]


# ---------------------------------------------------------------------------
# 6. ER class-specific resolver overrides the default
# ---------------------------------------------------------------------------


def test_er_class_specific_resolver_overrides_default():
    """Registering a resolver for a class name routes to that resolver."""
    from knot.extensions import er as er_module

    # Temporarily register a resolver for a test class name
    test_class_name = "_TestClass_override"

    assert test_class_name not in er_module._RESOLVERS

    @er_module.register(test_class_name)
    def _custom(ev: IngestResolveCanonical) -> list[str]:
        return [f"custom:{r['id']}" for r in ev.incoming]

    try:
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True, required=True)
        cls = OntologyClass(name=test_class_name, slots=[id_slot])
        src = Source(name="src", entity_class=cls, identifier_slot=id_slot)
        rows = [{"id": "x1"}, {"id": "x2"}]

        ev = IngestResolveCanonical(cls=cls, source=src, incoming=rows)
        # Dispatch via the global dispatcher so the ER handler fires
        from knot.extensions import dispatch
        dispatch.dispatch(ev)

        assert ev.canonical_ids == ["custom:x1", "custom:x2"]
    finally:
        del er_module._RESOLVERS[test_class_name]


def test_er_register_duplicate_raises():
    from knot.extensions import er as er_module

    name = "_dup_test_class"
    er_module._RESOLVERS[name] = lambda ev: []
    try:
        with pytest.raises(ValueError, match="already registered"):
            @er_module.register(name)
            def _fn(ev):
                return []
    finally:
        del er_module._RESOLVERS[name]


# ---------------------------------------------------------------------------
# 7. End-to-end via ingest route: _canonical_id set from default ER
# ---------------------------------------------------------------------------


@pytest.fixture
def ingest_db(pg_conn):
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()

    spec, movie, src = _build_movie_spec()
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    yield pg_conn, movie, src, rev


@pytest.fixture
def ingest_client():
    from knot.api.main import app
    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


def test_ingest_route_canonical_id_from_default_er(ingest_db, ingest_client):
    """Ingest one row via the route; verify _canonical_id equals the imdb_id value."""
    conn, movie, src, rev = ingest_db

    resp = ingest_client.post(
        "/graph/ingest/imdb",
        json={"rows": [{"imdb_id": "tt9999999", "title": "Test Film"}]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted"] == 1

    rows = graph_store.list_rows(conn, cls=movie)
    matching = [r for r in rows if r.get("_canonical_id") == "tt9999999"]
    assert matching, f"Row with _canonical_id='tt9999999' not found; got {rows}"
    assert matching[0]["title"] == "Test Film"
