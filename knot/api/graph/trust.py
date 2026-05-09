"""GET/PUT /graph/trust/* — per-source trust scores and Beta posteriors."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from knot import db
from knot.api.auth.security import require_user
from knot.api.graph._common import StrictBase, published_or_409
from knot.db import trust_config, trust_posteriors

router = APIRouter()


class TrustScore(StrictBase):
    source: str
    trust_score: float


class TrustUpdate(StrictBase):
    trust_score: float = Field(..., ge=0.0, le=1.0)


class PosteriorView(StrictBase):
    source: str
    slot: str
    alpha: float
    beta: float
    mean: float
    observations: float


class FeedbackBody(StrictBase):
    source: str
    slot: str
    success: bool


def _posterior_view(p: trust_posteriors.Posterior) -> PosteriorView:
    return PosteriorView(
        source=p.source,
        slot=p.slot,
        alpha=p.alpha,
        beta=p.beta,
        mean=p.mean,
        observations=p.observations,
    )


# NOTE: literal-path routes are declared BEFORE `{source_name}` so FastAPI
# matches /trust/posteriors and /trust/feedback exactly rather than
# treating them as source names.


@router.get("/trust", response_model=list[TrustScore])
def list_trust_scores() -> list[TrustScore]:
    """All configured per-source trust scores. Sources without an entry use
    ``trust_config.DEFAULT_TRUST``."""
    with db.connect() as conn:
        scores = trust_config.list_scores(conn)
    return [TrustScore(source=s, trust_score=v) for s, v in scores.items()]


@router.get("/trust/posteriors", response_model=list[PosteriorView])
def list_posteriors() -> list[PosteriorView]:
    """All Beta posteriors recorded so far. Pairs without a row use the
    uniform prior (Beta(1, 1))."""
    with db.connect() as conn:
        return [_posterior_view(p) for p in trust_posteriors.list_posteriors(conn)]


@router.get(
    "/trust/posteriors/{source_name}/{slot_name}",
    response_model=PosteriorView,
)
def get_posterior(source_name: str, slot_name: str) -> PosteriorView:
    with db.connect() as conn:
        spec = published_or_409(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        if not any(s.name == slot_name for s in spec.slots):
            raise HTTPException(404, f"Slot {slot_name!r} not on the published spec.")
        return _posterior_view(trust_posteriors.get_posterior(conn, source_name, slot_name))


@router.delete(
    "/trust/posteriors/{source_name}/{slot_name}",
    dependencies=[Depends(require_user)],
)
def reset_posterior(source_name: str, slot_name: str) -> dict[str, Any]:
    """Drop the per-(source, slot) posterior, reverting it to the uniform prior."""
    with db.connect() as conn:
        existed = trust_posteriors.reset_posterior(conn, source_name, slot_name)
    return {"reset": existed, "source": source_name, "slot": slot_name}


@router.post(
    "/trust/feedback",
    response_model=PosteriorView,
    dependencies=[Depends(require_user)],
)
def submit_feedback(body: FeedbackBody) -> PosteriorView:
    """Record one Bernoulli observation (source, slot, success) — increments
    α on success, β on failure. Source and slot must be on the published spec."""
    with db.connect() as conn:
        spec = published_or_409(conn)
        if not any(s.name == body.source for s in spec.sources):
            raise HTTPException(404, f"Source {body.source!r} not on the published spec.")
        if not any(s.name == body.slot for s in spec.slots):
            raise HTTPException(404, f"Slot {body.slot!r} not on the published spec.")
        post = trust_posteriors.record_feedback(conn, body.source, body.slot, body.success)
    return _posterior_view(post)


# Parameterized `/trust/{source_name}` routes go LAST so the literal-path
# routes above (/trust/posteriors, /trust/feedback) take precedence.


@router.get("/trust/{source_name}", response_model=TrustScore)
def get_trust_score(source_name: str) -> TrustScore:
    with db.connect() as conn:
        spec = published_or_409(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        score = trust_config.get_score(conn, source_name)
    return TrustScore(source=source_name, trust_score=score)


@router.put(
    "/trust/{source_name}",
    response_model=TrustScore,
    dependencies=[Depends(require_user)],
)
def set_trust_score(source_name: str, body: TrustUpdate) -> TrustScore:
    with db.connect() as conn:
        spec = published_or_409(conn)
        if not any(s.name == source_name for s in spec.sources):
            raise HTTPException(404, f"Source {source_name!r} not on the published spec.")
        trust_config.set_score(conn, source_name, body.trust_score)
    return TrustScore(source=source_name, trust_score=body.trust_score)
