"""Default ER extension — fills RowsIngesting.canonical_ids.

Default: identifier-slot passthrough.
Custom per source: ``@er.register("imdb_movies")``
    ``def my_resolver(rows, source) -> list[str]: ...``
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from knot.extensions import RequestContext, dispatch
from knot.extensions.events import RowsIngesting
from knot.spec import Source

_RESOLVERS: dict[str, Callable[[list[BaseModel], Source], list[str]]] = {}


def register(source_name: str) -> Callable:
    """Decorator — register a resolver for one Source by name."""

    def deco(fn):
        if source_name in _RESOLVERS:
            raise ValueError(f"Resolver already registered for source {source_name!r}")
        _RESOLVERS[source_name] = fn
        return fn

    return deco


def _default(rows: list[BaseModel], source: Source) -> list[str]:
    name = source.identifier_slot.name
    return [str(getattr(r, name)) for r in rows]


@dispatch.on(RowsIngesting, priority=50)
async def _resolve(ev: RowsIngesting, ctx: RequestContext) -> None:
    if ev.canonical_ids is not None:
        return  # another handler already set it; respect that
    resolver = _RESOLVERS.get(ev.source.name, _default)
    ev.canonical_ids = resolver(ev.rows, ev.source)
