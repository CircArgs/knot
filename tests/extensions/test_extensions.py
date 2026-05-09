"""Tests for the master dispatcher + ER extension scaffold.

Coverage:
  1. Dispatcher priority ordering (lower number runs first)
  2. Multiple handlers on same event type all run
  3. isinstance matching works for subclasses
  4. No matching handler is a no-op (event unchanged)
  5. ER source-specific resolver overrides the built-in default
  6. ER register raises on duplicate registration
  7. End-to-end via ingest route: built-in identifier-slot fallback sets
     _canonical_id when no team resolver is registered.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.api.row_models import build_row_model
from knot.db import graph_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.extensions import RequestContext, Session, _Dispatcher
from knot.extensions.events import RowsIngested, RowsIngesting
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition

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


def _make_ctx(conn) -> RequestContext:
    return RequestContext(db=Session(conn), spec_revision=0, request_id="test-req")


def _make_event(
    src: Source, rows: list[dict] | None = None, *, spec: Spec | None = None
) -> RowsIngesting:
    RowModel = build_row_model(src)
    typed = [RowModel.model_validate(r) for r in (rows or [])]
    if spec is None:
        spec, _, _ = _build_movie_spec()
    return RowsIngesting(source=src, spec=spec, rows=typed)


# ---------------------------------------------------------------------------
# 1. Dispatcher priority ordering
# ---------------------------------------------------------------------------


async def test_dispatcher_priority_ordering(pg_conn):
    """Lower priority number runs first."""
    d = _Dispatcher()
    order: list[int] = []

    @d.on(RowsIngesting, priority=200)
    async def _high(ev, ctx):
        order.append(200)

    @d.on(RowsIngesting, priority=10)
    async def _low(ev, ctx):
        order.append(10)

    @d.on(RowsIngesting, priority=100)
    async def _mid(ev, ctx):
        order.append(100)

    _spec, _movie, src = _build_movie_spec()
    ev = _make_event(src)
    await d.dispatch(ev, _make_ctx(pg_conn))

    assert order == [10, 100, 200]


# ---------------------------------------------------------------------------
# 2. Multiple handlers on same event type all run
# ---------------------------------------------------------------------------


async def test_dispatcher_multiple_handlers_all_run(pg_conn):
    d = _Dispatcher()
    calls: list[str] = []

    @d.on(RowsIngesting)
    async def _a(ev, ctx):
        calls.append("a")

    @d.on(RowsIngesting)
    async def _b(ev, ctx):
        calls.append("b")

    _spec, _movie, src = _build_movie_spec()
    ev = _make_event(src)

    await d.dispatch(ev, _make_ctx(pg_conn))
    assert "a" in calls
    assert "b" in calls
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# 3. isinstance matching works for subclasses
# ---------------------------------------------------------------------------


async def test_dispatcher_isinstance_matches_subclass(pg_conn):
    """A handler registered on a base class fires for subclass events.

    RowsIngested is a sibling of RowsIngesting, both extending RowEvent;
    a handler on RowEvent should fire for either.
    """
    from knot.extensions.events import RowEvent

    d = _Dispatcher()
    fired: list[type] = []

    @d.on(RowEvent)
    async def _base_handler(ev, ctx):
        fired.append(type(ev))

    spec, _movie, src = _build_movie_spec()
    ctx = _make_ctx(pg_conn)
    await d.dispatch(_make_event(src, spec=spec), ctx)
    await d.dispatch(
        RowsIngested(source=src, spec=spec, rows=[], inserted_count=0, canonical_ids=[]),
        ctx,
    )

    assert fired == [RowsIngesting, RowsIngested]


# ---------------------------------------------------------------------------
# 4. No matching handler is a no-op
# ---------------------------------------------------------------------------


async def test_dispatcher_no_handler_is_noop(pg_conn):
    d = _Dispatcher()
    calls: list[str] = []

    @d.on(RowsIngested)
    async def _target(ev, ctx):
        calls.append("fired")

    # Dispatching a RowsIngesting must NOT trigger the RowsIngested handler.
    _spec, _movie, src = _build_movie_spec()
    await d.dispatch(_make_event(src), _make_ctx(pg_conn))
    assert calls == []


# ---------------------------------------------------------------------------
# 5. ER source-specific resolver overrides the built-in default
# ---------------------------------------------------------------------------


async def test_er_source_specific_resolver_runs_when_registered(pg_conn):
    """Registering a resolver for a source name routes to that resolver via
    the master dispatcher. The ER scaffold's RowsIngesting handler picks
    it up and sets canonical_ids.
    """
    from knot.extensions import dispatch
    from knot.extensions import er as er_module

    test_source_name = "_test_source_override"

    assert test_source_name not in er_module._RESOLVERS

    @er_module.register(test_source_name)
    def _custom(rows, source):
        return [f"custom:{r.id}" for r in rows]

    try:
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True, required=True)
        cls = OntologyClass(name="X", slots=[id_slot])
        src = Source(name=test_source_name, entity_class=cls, identifier_slot=id_slot)
        spec = Spec(
            id="custom_resolver_test",
            version="1.0.0",
            types=[st],
            slots=[id_slot],
            classes=[cls],
            sources=[src],
        )
        RowModel = build_row_model(src)
        typed = [RowModel.model_validate({"id": "x1"}), RowModel.model_validate({"id": "x2"})]

        ev = RowsIngesting(source=src, spec=spec, rows=typed)
        await dispatch.dispatch(ev, _make_ctx(pg_conn))

        assert ev.canonical_ids == ["custom:x1", "custom:x2"]
    finally:
        del er_module._RESOLVERS[test_source_name]


# ---------------------------------------------------------------------------
# 6. ER register raises on duplicate registration
# ---------------------------------------------------------------------------


def test_er_register_duplicate_raises():
    from knot.extensions import er as er_module

    name = "_dup_test_source"
    er_module._RESOLVERS[name] = lambda rows, source: []
    try:
        with pytest.raises(ValueError, match="already registered"):

            @er_module.register(name)
            def _fn(rows, source):
                return []
    finally:
        del er_module._RESOLVERS[name]


# ---------------------------------------------------------------------------
# 7. End-to-end via ingest route: built-in identifier-slot fallback
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ingest_db(pg_conn):
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec, movie, src = _build_movie_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    yield pg_conn, movie, src, rev


@pytest.fixture
def ingest_client():
    from knot.api.main import app

    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


async def test_ingest_route_canonical_id_from_builtin_fallback(ingest_db, ingest_client):
    """No team resolver registered for 'imdb' → built-in identifier-slot
    passthrough sets _canonical_id to the imdb_id value."""
    conn, movie, src, rev = ingest_db

    resp = ingest_client.post(
        "/graph/ingest/imdb",
        json={"rows": [{"imdb_id": "tt9999999", "title": "Test Film"}]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted"] == 1

    rows = await graph_store.list_rows(conn, cls=movie)
    matching = [r for r in rows if r.get("_canonical_id") == "tt9999999"]
    assert matching, f"Row with _canonical_id='tt9999999' not found; got {rows}"
    assert matching[0]["title"] == "Test Film"
