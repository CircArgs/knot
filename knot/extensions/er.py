"""ER hook - delegates to the er microservice when configured.

When `KNOT_ER_URL` is set, every RowsIngesting event POSTs to that
service and uses the returned canonical_ids. When unset, no handler
fires and graph.ingest_rows falls back to its built-in identifier-slot
passthrough (defined in knot/graph/ingest.py).

This module is intentionally tiny. The real ER logic lives in the
sibling `er/` service. knot core never owns ER strategy choice.

`httpx` is imported lazily inside the handler so knot core has no new
hard runtime dependency - teams that don't set `KNOT_ER_URL` never
touch this code path."""

from __future__ import annotations

import os

from knot.extensions import RequestContext, dispatch
from knot.extensions.events import RowsIngesting

_ER_URL = os.environ.get("KNOT_ER_URL")


if _ER_URL:

    @dispatch.on(RowsIngesting, priority=50)
    async def _delegate(ev: RowsIngesting, ctx: RequestContext) -> None:
        import httpx

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{_ER_URL.rstrip('/')}/resolve",
                json={
                    "source": ev.source.name,
                    "class_name": ev.source.entity_class.name,
                    "identifier_slot": ev.source.identifier_slot.name,
                    "rows": [row.model_dump(exclude_none=False) for row in ev.rows],
                },
                timeout=30.0,
            )
            r.raise_for_status()
            ev.canonical_ids = r.json()["canonical_ids"]
