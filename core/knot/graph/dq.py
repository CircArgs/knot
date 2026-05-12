"""Data-quality orchestration — observation queries + scan trigger.

The list/summarize wrappers are passthroughs over ``knot.db.dq`` for the
"every route's primary op lives in graph/" rule. ``scan`` does the
spec lookup + classify + delegate.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import psycopg

from knot.db import dq as db_dq
from knot.db import spec_store


class NoSpecPublishedError(Exception):
    """Raised when no spec is published; ``scan`` has nothing to scan against."""


async def list_observations(
    conn: psycopg.AsyncConnection,
    *,
    source: str | None = None,
    class_name: str | None = None,
    property: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Time-series read of observations with optional filters."""
    return await db_dq.query_observations(
        conn,
        source=source,
        class_name=class_name,
        slot=property,
        since=since,
        until=until,
        kind=kind,
        limit=limit,
    )


async def summarize(
    conn: psycopg.AsyncConnection,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Per-(source, class, property) roll-up over a time window."""
    return await db_dq.summarize(conn, since=since, until=until)


async def scan(
    conn: psycopg.AsyncConnection,
    *,
    source_filter: str | None = None,
    class_filter: str | None = None,
) -> tuple[int, int]:
    """Snapshot per-(source, class, property) stats from the current data plane.

    Returns ``(observations_inserted, spec_revision)``. Raises
    ``NoSpecPublishedError`` if no spec is currently published.
    """
    spec = await spec_store.get_published(conn)
    if spec is None:
        raise NoSpecPublishedError("No spec is published yet.")
    revision = await spec_store.get_published_revision(conn) or 0
    inserted = await db_dq.full_scan(
        conn,
        spec,
        source_filter=source_filter,
        class_filter=class_filter,
    )
    return inserted, revision
