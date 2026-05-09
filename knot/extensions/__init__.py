"""Master dispatcher for knot extensions.

Routes emit typed events; every handler whose event_type matches (via
isinstance) runs in priority order (lower number = earlier).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class _Dispatcher:
    """One per process. Routes emit events; every matching handler runs."""

    def __init__(self) -> None:
        self._handlers: list[tuple[type, int, Callable]] = []

    def on(self, event_type: type, *, priority: int = 100) -> Callable:
        def deco(fn: Callable) -> Callable:
            self._handlers.append((event_type, priority, fn))
            self._handlers.sort(key=lambda h: h[1])
            return fn

        return deco

    async def dispatch(self, event: object) -> None:
        for event_type, _, fn in self._handlers:
            if isinstance(event, event_type):
                result = fn(event)
                if asyncio.iscoroutine(result):
                    await result


dispatch = _Dispatcher()

# Side-effect imports — register builtin extensions on the master dispatcher.
from knot.extensions import er  # noqa: E402, F401
