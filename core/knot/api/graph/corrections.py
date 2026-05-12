"""POST /graph/corrections — typed correction discriminated union + audit list."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.api.graph._common import StrictBase, published_or_409, resolve_class
from knot.db import spec_store
from knot.graph import corrections as graph_corrections

router = APIRouter()


# ─── Correction types — discriminated union by ``type`` literal ─────────────


class PropertyCorrection(StrictBase):
    type: Literal["property"] = "property"
    class_name: str
    canonical_id: str
    property: str
    value: Any


class Merge(StrictBase):
    type: Literal["merge"] = "merge"
    class_name: str
    keep_canonical_id: str
    merge_canonical_ids: list[str] = Field(..., max_length=1_000)


class Split(StrictBase):
    type: Literal["split"] = "split"
    class_name: str
    source_canonical_id: str
    partitions: dict[str, list[tuple[str, str]]]


class Add(StrictBase):
    type: Literal["add"] = "add"
    class_name: str
    new_canonical_id: str
    values: dict[str, Any] = Field(default_factory=dict)


class Tombstone(StrictBase):
    type: Literal["tombstone"] = "tombstone"
    class_name: str
    canonical_id: str
    reason: str | None = None


class RejectContribution(StrictBase):
    type: Literal["reject_contribution"] = "reject_contribution"
    class_name: str
    canonical_id: str
    source: str


Correction = Annotated[
    PropertyCorrection | Merge | Split | Add | Tombstone | RejectContribution,
    Field(discriminator="type"),
]


class CorrectionResponse(StrictBase):
    id: int
    correction_type: str
    applied_revision: int


# ─── Endpoint ───────────────────────────────────────────────────────────────


def _map_correction_errors(exc: Exception) -> HTTPException:
    """Translate graph_corrections typed errors to HTTP."""
    if isinstance(exc, graph_corrections.CanonicalNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, graph_corrections.SlotNotOnClassError):
        return HTTPException(404, str(exc))
    if isinstance(exc, graph_corrections.CanonicalAlreadyExistsError):
        return HTTPException(409, str(exc))
    if isinstance(exc, graph_corrections.InvalidPartitionsError):
        return HTTPException(400, str(exc))
    if isinstance(exc, graph_corrections.CorrectionValueError):
        return HTTPException(422, detail=exc.errors)
    return HTTPException(500, str(exc))


async def _apply(
    conn,
    body: Correction,
    spec,
    principal: Principal,
) -> tuple[int, str, int]:
    """Dispatch on correction body type and call the matching apply_*.

    Returns ``(correction_id, correction_type, spec_revision)``.
    """
    cls = resolve_class(spec, body.class_name)
    spec_revision = await spec_store.get_published_revision(conn)

    if isinstance(body, PropertyCorrection):
        correction_id = await graph_corrections.apply_property_correction(
            conn,
            cls=cls,
            canonical_id=body.canonical_id,
            property_name=body.property,
            value=body.value,
            spec_revision=spec_revision,
            applied_by=principal.username,
            payload_for_log=body.model_dump(),
        )
        return correction_id, "property", spec_revision

    if isinstance(body, Merge):
        correction_id = await graph_corrections.apply_merge(
            conn,
            cls=cls,
            keep_canonical_id=body.keep_canonical_id,
            merge_canonical_ids=body.merge_canonical_ids,
            spec_revision=spec_revision,
            spec=spec,
            applied_by=principal.username,
            payload_for_log=body.model_dump(),
        )
        return correction_id, "merge", spec_revision

    if isinstance(body, Split):
        correction_id = await graph_corrections.apply_split(
            conn,
            cls=cls,
            source_canonical_id=body.source_canonical_id,
            partitions=body.partitions,
            spec_revision=spec_revision,
            applied_by=principal.username,
            payload_for_log=body.model_dump(),
        )
        return correction_id, "split", spec_revision

    if isinstance(body, Add):
        correction_id = await graph_corrections.apply_add(
            conn,
            cls=cls,
            new_canonical_id=body.new_canonical_id,
            values=body.values,
            spec_revision=spec_revision,
            applied_by=principal.username,
            payload_for_log=body.model_dump(),
        )
        return correction_id, "add", spec_revision

    if isinstance(body, Tombstone):
        correction_id = await graph_corrections.apply_tombstone(
            conn,
            cls=cls,
            canonical_id=body.canonical_id,
            reason=body.reason,
            spec_revision=spec_revision,
            applied_by=principal.username,
            payload_for_log=body.model_dump(),
        )
        return correction_id, "tombstone", spec_revision

    if isinstance(body, RejectContribution):
        correction_id = await graph_corrections.apply_reject_contribution(
            conn,
            cls=cls,
            canonical_id=body.canonical_id,
            source=body.source,
            spec_revision=spec_revision,
            applied_by=principal.username,
            payload_for_log=body.model_dump(),
        )
        return correction_id, "reject_contribution", spec_revision

    # Unreachable — discriminated union exhausts all variants.
    raise HTTPException(501, f"Correction type {body.type!r} not implemented yet (open).")


@router.post("/corrections", response_model=CorrectionResponse)
async def submit_correction(
    body: Correction,
    principal: Principal = Depends(require_user),
) -> CorrectionResponse:
    """Submit a typed correction. Auto-applies in one transaction:
    audit row + per-class data mutation + bandit feedback against
    disagreeing sources. 422 on payload type mismatch; 404 on unknown
    class/slot/canonical_id."""
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        try:
            correction_id, correction_type, spec_revision = await _apply(
                conn, body, spec, principal
            )
        except (
            graph_corrections.CanonicalNotFoundError,
            graph_corrections.CanonicalAlreadyExistsError,
            graph_corrections.SlotNotOnClassError,
            graph_corrections.InvalidPartitionsError,
            graph_corrections.CorrectionValueError,
        ) as exc:
            raise _map_correction_errors(exc) from exc
    return CorrectionResponse(
        id=correction_id,
        correction_type=correction_type,
        applied_revision=spec_revision,
    )


@router.get("/corrections")
async def list_corrections(limit: int = Query(100, ge=1, le=1000)) -> list[dict[str, Any]]:
    async with db.connect() as conn:
        return await db.corrections.list_audit_log(conn, limit=limit)
