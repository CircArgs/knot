"""GET/PUT /graph/trust/* — per-source trust scores and Beta posteriors."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from knot import db
from knot.api.auth.security import require_user
from knot.api.graph._common import StrictBase, published_or_409
from knot.db import trust_posteriors
from knot.graph import trust as graph_trust

router = APIRouter()


class TrustScore(StrictBase):
    source: str
    trust_score: float


class TrustUpdate(StrictBase):
    trust_score: float = Field(..., ge=0.0, le=1.0)


class PosteriorView(StrictBase):
    source: str
    property: str
    alpha: float
    beta: float
    mean: float
    observations: float


class FeedbackBody(StrictBase):
    source: str
    property: str
    success: bool


def _posterior_view(p: trust_posteriors.Posterior) -> PosteriorView:
    return PosteriorView(
        source=p.source,
        slot=p.property,
        alpha=p.alpha,
        beta=p.beta,
        mean=p.mean,
        observations=p.observations,
    )


def _map_validation(exc: Exception) -> HTTPException:
    """Translate orchestration validation errors to 404."""
    return HTTPException(404, str(exc))


# NOTE: literal-path routes are declared BEFORE `{source_name}` so FastAPI
# matches /trust/posteriors and /trust/feedback exactly rather than
# treating them as source names.


@router.get("/trust", response_model=list[TrustScore])
async def list_trust_scores() -> list[TrustScore]:
    """All configured per-source trust scores. Sources without an entry use
    ``trust_config.DEFAULT_TRUST``."""
    async with db.connect() as conn:
        scores = await graph_trust.list_trust_scores(conn)
    return [TrustScore(source=s, trust_score=v) for s, v in scores.items()]


@router.get("/trust/posteriors", response_model=list[PosteriorView])
async def list_posteriors() -> list[PosteriorView]:
    """All Beta posteriors recorded so far. Pairs without a row use the
    uniform prior (Beta(1, 1))."""
    async with db.connect() as conn:
        return [_posterior_view(p) for p in await graph_trust.list_posteriors(conn)]


@router.get(
    "/trust/posteriors/{source_name}/{property_name}",
    response_model=PosteriorView,
)
async def get_posterior(source_name: str, property_name: str) -> PosteriorView:
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        try:
            post = await graph_trust.get_posterior(
                conn, spec=spec, source=source_name, slot=property_name
            )
        except (graph_trust.SourceNotOnSpecError, graph_trust.SlotNotOnSpecError) as exc:
            raise _map_validation(exc) from exc
        return _posterior_view(post)


@router.delete(
    "/trust/posteriors/{source_name}/{property_name}",
    dependencies=[Depends(require_user)],
)
async def reset_posterior(source_name: str, property_name: str) -> dict[str, Any]:
    """Drop the per-(source, property) posterior, reverting it to the uniform prior."""
    async with db.connect() as conn:
        existed = await graph_trust.reset_posterior(conn, source=source_name, slot=property_name)
    return {"reset": existed, "source": source_name, "slot": property_name}


@router.post(
    "/trust/feedback",
    response_model=PosteriorView,
    dependencies=[Depends(require_user)],
)
async def submit_feedback(body: FeedbackBody) -> PosteriorView:
    """Record one Bernoulli observation (source, property, success) — increments
    α on success, β on failure. Source and slot must be on the published spec."""
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        try:
            post = await graph_trust.record_feedback(
                conn,
                spec=spec,
                source=body.source,
                slot=body.property,
                success=body.success,
            )
        except (graph_trust.SourceNotOnSpecError, graph_trust.SlotNotOnSpecError) as exc:
            raise _map_validation(exc) from exc
    return _posterior_view(post)


# Parameterized `/trust/{source_name}` routes go LAST so the literal-path
# routes above (/trust/posteriors, /trust/feedback) take precedence.


@router.get("/trust/{source_name}", response_model=TrustScore)
async def get_trust_score(source_name: str) -> TrustScore:
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        try:
            score = await graph_trust.get_trust_score(conn, spec=spec, source=source_name)
        except graph_trust.SourceNotOnSpecError as exc:
            raise _map_validation(exc) from exc
    return TrustScore(source=source_name, trust_score=score)


@router.put(
    "/trust/{source_name}",
    response_model=TrustScore,
    dependencies=[Depends(require_user)],
)
async def set_trust_score(source_name: str, body: TrustUpdate) -> TrustScore:
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        try:
            await graph_trust.set_trust_score(
                conn, spec=spec, source=source_name, score=body.trust_score
            )
        except graph_trust.SourceNotOnSpecError as exc:
            raise _map_validation(exc) from exc
    return TrustScore(source=source_name, trust_score=body.trust_score)
