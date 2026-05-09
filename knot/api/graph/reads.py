"""GET /graph/classes/* — list rows, single contributions, trust-resolved view."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from knot import db
from knot.api.graph._common import StrictBase, published_or_409, resolve_class
from knot.graph import reads

router = APIRouter()


class ListResponse(StrictBase):
    entity_class: str
    rows: list[dict[str, Any]]
    total: int
    limit: int
    offset: int
    as_of: int | None = None


class EntityResponse(StrictBase):
    entity_class: str
    canonical_id: str
    contributions: list[dict[str, Any]]
    as_of: int | None = None


class ResolvedEntityResponse(StrictBase):
    entity_class: str
    canonical_id: str
    resolved: dict[str, Any]
    as_of: int | None = None


@router.get("/classes/{class_name}", response_model=ListResponse)
async def list_class_rows(
    class_name: str,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    as_of: int | None = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
    include_tombstoned: bool = Query(False, description="Include tombstoned entities"),
) -> ListResponse:
    """List rows for a published class. One row per ``(_canonical_id, _source)``."""
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        cls = resolve_class(spec, class_name)
        rows, total = await reads.list_entities(
            conn,
            cls=cls,
            limit=limit,
            offset=offset,
            as_of=as_of,
            include_tombstoned=include_tombstoned,
        )
    return ListResponse(
        entity_class=cls.name,
        rows=rows,
        total=total,
        limit=limit,
        offset=offset,
        as_of=as_of,
    )


@router.get(
    "/classes/{class_name}/{canonical_id}",
    response_model=EntityResponse,
)
async def get_canonical_entity(
    class_name: str,
    canonical_id: str,
    as_of: int | None = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
    include_tombstoned: bool = Query(False, description="Include tombstoned entities"),
) -> EntityResponse:
    """All per-source contributions for a single canonical entity."""
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        cls = resolve_class(spec, class_name)
        try:
            contributions = await reads.get_entity_contributions(
                conn,
                cls=cls,
                canonical_id=canonical_id,
                as_of=as_of,
                include_tombstoned=include_tombstoned,
            )
        except reads.CanonicalNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    return EntityResponse(
        entity_class=cls.name,
        canonical_id=canonical_id,
        contributions=contributions,
        as_of=as_of,
    )


@router.get(
    "/classes/{class_name}/{canonical_id}/resolved",
    response_model=ResolvedEntityResponse,
)
async def get_resolved_entity(
    class_name: str,
    canonical_id: str,
    as_of: int | None = Query(None, ge=1, description="Pin to spec_revision ≤ N"),
) -> ResolvedEntityResponse:
    """One trust-resolved record for the canonical_id (per-slot resolution)."""
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        cls = resolve_class(spec, class_name)
        try:
            record = await reads.get_resolved_entity(
                conn,
                cls=cls,
                canonical_id=canonical_id,
                as_of=as_of,
            )
        except reads.CanonicalNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    return ResolvedEntityResponse(
        entity_class=cls.name,
        canonical_id=canonical_id,
        resolved=record,
        as_of=as_of,
    )
