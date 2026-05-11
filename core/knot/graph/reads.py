"""Read-side orchestration — list rows, get contributions, resolved view.

Wraps ``knot.db.graph_store`` for the consumer-facing GET endpoints under
``/graph/classes/*``. Pure composition: no SQL strings, no psycopg.

Typed exceptions (``CanonicalNotFoundError``) replace HTTP at this layer;
the route catches and maps.
"""

from __future__ import annotations

from typing import Any

import psycopg

from knot.db import graph_store
from knot.spec import OntologyClass


class CanonicalNotFoundError(Exception):
    """Raised when a canonical_id has no contributions for the given class."""

    def __init__(self, class_name: str, canonical_id: str, *, as_of: int | None = None) -> None:
        self.class_name = class_name
        self.canonical_id = canonical_id
        self.as_of = as_of
        suffix = f" as_of={as_of}" if as_of is not None else ""
        super().__init__(f"No contributions for {class_name}/{canonical_id}{suffix}")


async def list_entities(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    limit: int,
    offset: int,
    as_of: int | None = None,
    include_tombstoned: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """List per-source contribution rows; returns ``(rows, total)``."""
    rows = await graph_store.list_rows(
        conn,
        cls=cls,
        limit=limit,
        offset=offset,
        as_of=as_of,
        include_tombstoned=include_tombstoned,
    )
    total = await graph_store.count_rows(
        conn,
        cls=cls,
        as_of=as_of,
        include_tombstoned=include_tombstoned,
    )
    return rows, total


async def get_entity_contributions(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
    include_tombstoned: bool = False,
) -> list[dict[str, Any]]:
    """All per-source contributions for a single canonical_id.

    Raises ``CanonicalNotFoundError`` if no contributions are found.
    """
    contributions = await graph_store.get_canonical_contributions(
        conn,
        cls=cls,
        canonical_id=canonical_id,
        as_of=as_of,
        include_tombstoned=include_tombstoned,
    )
    if not contributions:
        raise CanonicalNotFoundError(cls.name, canonical_id, as_of=as_of)
    return contributions


async def get_resolved_entity(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
) -> dict[str, Any]:
    """One trust-resolved record for the canonical_id.

    Raises ``CanonicalNotFoundError`` if no contributions exist.
    """
    from knot.graph import resolve

    record = await resolve.resolve_entity(
        conn,
        cls=cls,
        canonical_id=canonical_id,
        as_of=as_of,
    )
    if record is None:
        raise CanonicalNotFoundError(cls.name, canonical_id, as_of=as_of)
    return record
