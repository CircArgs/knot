"""ER feature routes - POST /resolve."""

from fastapi import APIRouter
from pydantic import BaseModel

from knot_er.strategies import identifier_passthrough


class ResolveRequest(BaseModel):
    source: str
    class_name: str
    identifier_slot: str  # the slot name to use for default identifier-passthrough
    rows: list[dict]


class ResolveResponse(BaseModel):
    canonical_ids: list[str]


router = APIRouter()


@router.post("/resolve", response_model=ResolveResponse)
async def resolve(body: ResolveRequest) -> ResolveResponse:
    """STUB. Default strategy is identifier-passthrough; real strategies
    (fuzzy, ML, embedding-similarity) plug in here."""
    canonical_ids = identifier_passthrough(body.rows, body.identifier_slot)
    return ResolveResponse(canonical_ids=canonical_ids)
