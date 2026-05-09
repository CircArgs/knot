"""Entity resolution extension — default and per-class resolver registry.

Default resolver: identifier-slot passthrough.
Override per class: @er.register("Movie") def my_resolver(ev): ...
"""

from __future__ import annotations

from collections.abc import Callable

from knot.extensions import dispatch
from knot.extensions.events import IngestResolveCanonical

# Internal sub-dispatch keyed by OntologyClass.name.
_RESOLVERS: dict[str, Callable[[IngestResolveCanonical], list[str]]] = {}


def register(class_name: str) -> Callable:
    """Decorator: register a class-specific ER resolver."""

    def deco(fn: Callable) -> Callable:
        if class_name in _RESOLVERS:
            raise ValueError(f"Resolver already registered for {class_name!r}")
        _RESOLVERS[class_name] = fn
        return fn

    return deco


def _default(ev: IngestResolveCanonical) -> list[str]:
    id_name = ev.source.identifier_slot.name
    return [str(r[id_name]) for r in ev.incoming]


@dispatch.on(IngestResolveCanonical, priority=50)
def _resolve(ev: IngestResolveCanonical) -> None:
    handler = _RESOLVERS.get(ev.cls.name, _default)
    ev.canonical_ids = handler(ev)
