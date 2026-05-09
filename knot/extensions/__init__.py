"""Master dispatcher for knot extensions.

Routes (via knot/graph/) emit typed events; every handler whose event_type
matches (via isinstance) runs in priority order (lower number = earlier).
Handlers receive (event, ctx) — the event carries source-side facts;
the ctx exposes a connection-bound view of knot.db.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

import psycopg
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from knot.extensions.events import Event


class Session:
    """Connection-bound facade over knot.db submodules.

    Every db function has its ``conn`` parameter pre-injected, e.g.
        await ctx.db.dq.record_incremental(source_name=..., ...)
    Escape hatch: ``ctx.db.conn`` — raw connection for ad-hoc SQL or savepoints.
    """

    def __init__(self, conn: psycopg.AsyncConnection) -> None:
        self._conn = conn
        from knot.db import (
            corrections,
            dq,
            graph_store,
            spec_store,
            trust_config,
            trust_posteriors,
            users,
        )

        self.spec_store = _bind(spec_store, conn)
        self.graph_store = _bind(graph_store, conn)
        self.corrections = _bind(corrections, conn)
        self.dq = _bind(dq, conn)
        self.trust_config = _bind(trust_config, conn)
        self.trust_posteriors = _bind(trust_posteriors, conn)
        self.users = _bind(users, conn)

    @property
    def conn(self) -> psycopg.AsyncConnection:
        return self._conn


def _bind(module: ModuleType, conn: psycopg.AsyncConnection):
    class _Bound:
        def __getattr__(self, name: str):
            attr = getattr(module, name)
            if not callable(attr) or name.startswith("_"):
                return attr

            @functools.wraps(attr)
            async def wrapped(*args, **kwargs):
                return await attr(conn, *args, **kwargs)

            return wrapped

    return _Bound()


class RequestContext(BaseModel):
    """Per-request context passed to every handler."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)
    db: Session
    spec_revision: int
    request_id: str


E = TypeVar("E")


class Handler(Protocol, Generic[E]):
    async def __call__(self, event: E, ctx: RequestContext) -> None: ...


class _Dispatcher:
    def __init__(self) -> None:
        self._handlers: list[tuple[type, int, Callable]] = []

    def on(self, event_type: type[E], *, priority: int = 100):
        def deco(fn: Handler[E]) -> Handler[E]:
            self._handlers.append((event_type, priority, fn))
            self._handlers.sort(key=lambda h: h[1])
            return fn

        return deco

    async def dispatch(self, event: Event, ctx: RequestContext) -> None:
        for et, _, fn in self._handlers:
            if isinstance(event, et):
                await fn(event, ctx)


dispatch = _Dispatcher()

# Side-effect imports — register builtin extensions on the master dispatcher.
from knot.extensions import (  # noqa: E402, F401
    constraint_validator,
    dq_observer,
    er,
)
