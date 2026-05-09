"""Management REST router for data-quality observations.

Mounted at ``/dq``. Distinct from the consumer ``/graph/*`` surface — DQ
is operator-facing data, not part of the data plane consumers traverse.

Endpoints:
  - ``GET  /dq/observations``         time-series query with filters
  - ``GET  /dq/observations/summary`` per-(source, class, slot) roll-up
  - ``POST /dq/scan``                 snapshot the current data plane
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from knot import db
from knot.api.auth.security import require_user
from knot.db import dq, spec_store

router = APIRouter(prefix="/dq", tags=["dq"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------
# Observation and SummaryRow are returned as raw dicts (the shape comes
# straight from the dq module). The "class" key would collide with the
# Python keyword if modeled as a Pydantic field, and the alias dance for
# round-tripping is more friction than it earns on an internal surface.


class ScanResponse(BaseModel):
    observations_inserted: int
    spec_revision: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/observations", dependencies=[Depends(require_user)])
async def list_observations(
    source: str | None = Query(None),
    class_name: str | None = Query(None, alias="class"),
    slot: str | None = Query(None),
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
    kind: str | None = Query(None, pattern="^(incremental|full_scan)$"),
    limit: int = Query(1000, ge=1, le=10_000),
) -> list[dict[str, Any]]:
    """Time-series of DQ observations (newest first), with optional filters."""
    async with db.connect() as conn:
        return await dq.query_observations(
            conn,
            source=source,
            class_name=class_name,
            slot=slot,
            since=since,
            until=until,
            kind=kind,
            limit=limit,
        )


@router.get("/observations/summary", dependencies=[Depends(require_user)])
async def summary(
    since: datetime | None = Query(None),
    until: datetime | None = Query(None),
) -> list[dict[str, Any]]:
    """Roll-up per (source, class, slot) over the time window."""
    async with db.connect() as conn:
        return await dq.summarize(conn, since=since, until=until)


@router.post(
    "/scan",
    response_model=ScanResponse,
    dependencies=[Depends(require_user)],
)
async def scan(
    source: str | None = Query(None, description="Restrict scan to one source name."),
    class_name: str | None = Query(
        None,
        alias="class",
        description="Restrict scan to one class.",
    ),
) -> ScanResponse:
    """Snapshot per-(source, class, slot) stats from the current data plane."""
    async with db.connect() as conn:
        spec = await spec_store.get_published(conn)
        if spec is None:
            raise HTTPException(409, "No spec is published yet.")
        revision = await spec_store.get_published_revision(conn)
        inserted = await dq.full_scan(
            conn,
            spec,
            source_filter=source,
            class_filter=class_name,
        )
    return ScanResponse(observations_inserted=inserted, spec_revision=revision or 0)
