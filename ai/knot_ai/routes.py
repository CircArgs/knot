"""AI feature routes."""

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class SuggestRequest(BaseModel):
    prompt: str
    target: str = "spec"  # "spec" | "graph"


class SuggestResponse(BaseModel):
    query: str
    explanation: str


@router.post("/suggest", response_model=SuggestResponse)
async def suggest(body: SuggestRequest) -> SuggestResponse:
    """STUB. Real impl will call LLM with knot schema context."""
    return SuggestResponse(
        query="# TODO: GraphQL from prompt: " + body.prompt,
        explanation="STUB - replace with LLM-backed implementation.",
    )
