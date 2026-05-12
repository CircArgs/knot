"""Tests for the master dispatcher + ER extension scaffold.

Coverage:
  1. Dispatcher priority ordering (lower number runs first)
  2. Multiple handlers on same event type all run
  3. isinstance matching works for subclasses
  4. No matching handler is a no-op (event unchanged)
  5. ER module HTTP-delegates to the er sibling service when
     KNOT_ER_URL is set (httpx mocked).
  6. End-to-end via ingest route: built-in identifier-slot fallback sets
     _canonical_id when no team handler is registered.
"""

from __future__ import annotations

import importlib

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.api.row_models import build_row_model
from knot.db import graph_store
from knot.extensions import RequestContext, Session, _Dispatcher
from knot.extensions.events import RowsIngested, RowsIngesting
from knot.spec import OntologyClass, Primitive, Slot, Source, Spec
from knot.spec.metaschema import SourceBinding
from tests._helpers import publish_spec

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _build_movie_spec() -> tuple[Spec, OntologyClass, Source, SourceBinding]:
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    spec = Spec(
        id="ext_test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )
    return spec, movie, src, binding


def _make_ctx(conn) -> RequestContext:
    return RequestContext(db=Session(conn), spec_revision=0, request_id="test-req")


def _make_event(
    src: Source,
    rows: list[dict] | None = None,
    *,
    spec: Spec | None = None,
    binding: SourceBinding | None = None,
) -> RowsIngesting:
    if binding is None:
        _, _, _, binding = _build_movie_spec()
    RowModel = build_row_model(binding)
    typed = [RowModel.model_validate(r) for r in (rows or [])]
    if spec is None:
        spec, _, _, _ = _build_movie_spec()
    return RowsIngesting(source=src, binding=binding, spec=spec, rows=typed)


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

    _spec, _movie, src, _binding = _build_movie_spec()
    ev = _make_event(src, binding=_binding)
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

    _spec, _movie, src, _binding = _build_movie_spec()
    ev = _make_event(src, binding=_binding)

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

    spec, _movie, src, binding = _build_movie_spec()
    ctx = _make_ctx(pg_conn)
    await d.dispatch(_make_event(src, spec=spec, binding=binding), ctx)
    await d.dispatch(
        RowsIngested(
            source=src, binding=binding, spec=spec, rows=[], inserted_count=0, canonical_ids=[]
        ),
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
    _spec, _movie, src, _binding = _build_movie_spec()
    await d.dispatch(_make_event(src, binding=_binding), _make_ctx(pg_conn))
    assert calls == []


# ---------------------------------------------------------------------------
# 5. ER module HTTP-delegates when KNOT_ER_URL is set
# ---------------------------------------------------------------------------


async def test_er_handler_http_delegates_when_url_set(pg_conn, monkeypatch):
    """With KNOT_ER_URL set, the ER handler POSTs to the configured URL
    and uses the returned canonical_ids. httpx is mocked to avoid an
    actual network call.
    """
    monkeypatch.setenv("KNOT_ER_URL", "http://er.test")

    # Reload the er module so the @dispatch.on registration fires.
    from knot.extensions import dispatch as _dispatch
    from knot.extensions import er as er_module

    # Snapshot dispatcher state so we can restore it; reload may push a
    # _delegate entry that survives a subsequent reload-with-env-unset.
    handlers_before = list(_dispatch._handlers)
    # Drop any pre-existing _delegate so reload sees a clean namespace.
    er_module.__dict__.pop("_delegate", None)
    importlib.reload(er_module)

    posted: dict = {}

    class _FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"canonical_ids": ["resolved:x1", "resolved:x2"]}

    class _FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, url, json, timeout):  # noqa: ARG002
            posted["url"] = url
            posted["json"] = json
            return _FakeResponse()

    # Patch httpx in the lazily-imported module path. The handler does
    # `import httpx` at call time, so swap the attribute on the
    # already-loaded module.
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)

    try:
        id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True, required=True)
        cls = OntologyClass(name="X", slots=[id_slot])
        src = Source(name="_http_delegate_test")
        er_binding = SourceBinding(source=src, class_=cls, identifier_slot=id_slot)  # type: ignore[call-arg]
        spec = Spec(
            id="http_delegate_test",
            version="1.0.0",
            classes=[cls],
            sources=[src],
            source_bindings=[er_binding],
        )
        RowModel = build_row_model(er_binding)
        typed = [
            RowModel.model_validate({"id": "x1"}),
            RowModel.model_validate({"id": "x2"}),
        ]

        ev = RowsIngesting(source=src, binding=er_binding, spec=spec, rows=typed)
        ctx = _make_ctx(pg_conn)
        # Drive the freshly-loaded handler directly. The master dispatch
        # also has it registered (priority 50), but calling the function
        # avoids cross-test interference with other registered handlers.
        await er_module._delegate(ev, ctx)

        assert ev.canonical_ids == ["resolved:x1", "resolved:x2"]
        assert posted["url"] == "http://er.test/resolve"
        assert posted["json"]["source"] == "_http_delegate_test"
        assert posted["json"]["class_name"] == "X"
        assert posted["json"]["identifier_slot"] == "id"
        assert posted["json"]["rows"] == [{"id": "x1"}, {"id": "x2"}]
    finally:
        monkeypatch.delenv("KNOT_ER_URL", raising=False)
        er_module.__dict__.pop("_delegate", None)
        importlib.reload(er_module)
        # Restore the dispatcher's pre-test handler list so the leaked
        # _delegate from the reload-with-env-set doesn't fire in later tests.
        _dispatch._handlers[:] = handlers_before


# ---------------------------------------------------------------------------
# 6. End-to-end via ingest route: built-in identifier-slot fallback
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

    spec, movie, src, _binding = _build_movie_spec()
    rev = await publish_spec(pg_conn, spec)

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
    """No KNOT_ER_URL set, no team handler registered for 'imdb' -> the
    built-in identifier-slot passthrough sets _canonical_id to the
    imdb_id value.
    """
    conn, movie, src, rev = ingest_db

    resp = ingest_client.post(
        "/graph/ingest/imdb",
        json={"rows": [{"imdb_id": "tt9999999", "title": "Test Film"}]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["accepted"] == 1

    rows = await graph_store.list_rows(conn, cls=movie)
    # canonical_id follows {source}:{source_row_id} format
    matching = [r for r in rows if "tt9999999" in r.get("_canonical_id", "")]
    assert matching, f"Row with canonical_id containing 'tt9999999' not found; got {rows}"
    assert matching[0]["title"] == "Test Film"
