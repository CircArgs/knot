"""Entity Resolution extension scaffold.

ER is the canonical extension surface — teams override canonical_id
generation per source. The default behavior (identifier-slot passthrough)
lives in knot.graph.ingest as a built-in fallback.

Usage in a team's bootstrap module:

    from knot.extensions import dispatch
    from knot.extensions.er import register
    from knot.extensions.events import RowsIngesting

    @register("imdb_movies")
    def my_resolver(rows, source) -> list[str]:
        return [f"movie:{r.imdb_id}:{r.year}" for r in rows]

If no resolver is registered for a source, the built-in identifier-slot
passthrough in graph.ingest_rows is used.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from knot.extensions import RequestContext, dispatch
from knot.extensions.events import RowsIngesting
from knot.spec import Source

_RESOLVERS: dict[str, Callable[[list[BaseModel], Source], list[str]]] = {}


def register(source_name: str) -> Callable:
    """Register a resolver for one Source. Decorator usage."""

    def deco(fn):
        if source_name in _RESOLVERS:
            raise ValueError(f"Resolver already registered for source {source_name!r}")
        _RESOLVERS[source_name] = fn
        return fn

    return deco


@dispatch.on(RowsIngesting, priority=50)
async def _maybe_resolve(ev: RowsIngesting, ctx: RequestContext) -> None:
    """If a team registered a resolver for this source, run it. Otherwise noop —
    graph.ingest_rows uses identifier-slot passthrough as the built-in default.
    """
    resolver = _RESOLVERS.get(ev.source.name)
    if resolver is None:
        return
    ev.canonical_ids = resolver(ev.rows, ev.source)
