"""Trust orchestration — per-source scores + per-(source, slot) Beta posteriors.

Composes ``knot.db.trust_config`` (scalar trust) and
``knot.db.trust_posteriors`` (Beta posteriors). Validates that the source
or slot is on the published spec before delegating; raises typed
exceptions on failure.
"""

from __future__ import annotations

import psycopg

from knot.db import trust_config, trust_posteriors
from knot.db.trust_posteriors import Posterior
from knot.spec import Spec


class SourceNotOnSpecError(Exception):
    """Raised when ``source`` isn't a Source on the published spec."""

    def __init__(self, source: str) -> None:
        self.source = source
        super().__init__(f"Source {source!r} not on the published spec.")


class SlotNotOnSpecError(Exception):
    """Raised when ``slot`` isn't a Slot on the published spec."""

    def __init__(self, slot: str) -> None:
        self.slot = slot
        super().__init__(f"Slot {slot!r} not on the published spec.")


def _check_source(spec: Spec, source: str) -> None:
    if not any(s.name == source for s in spec.sources):
        raise SourceNotOnSpecError(source)


def _check_slot(spec: Spec, slot: str) -> None:
    if not any(s.name == slot for s in spec.slots):
        raise SlotNotOnSpecError(slot)


# ─── Scalar trust config ────────────────────────────────────────────────────


async def list_trust_scores(conn: psycopg.AsyncConnection) -> dict[str, float]:
    """All configured per-source trust scores."""
    return await trust_config.list_scores(conn)


async def get_trust_score(conn: psycopg.AsyncConnection, *, spec: Spec, source: str) -> float:
    """Trust score for one source. Raises if the source isn't on spec."""
    _check_source(spec, source)
    return await trust_config.get_score(conn, source)


async def set_trust_score(
    conn: psycopg.AsyncConnection, *, spec: Spec, source: str, score: float
) -> None:
    """Upsert one source's trust score. Raises if the source isn't on spec."""
    _check_source(spec, source)
    await trust_config.set_score(conn, source, score)


# ─── Beta posteriors ────────────────────────────────────────────────────────


async def list_posteriors(conn: psycopg.AsyncConnection) -> list[Posterior]:
    """All Beta posteriors recorded so far."""
    return await trust_posteriors.list_posteriors(conn)


async def get_posterior(
    conn: psycopg.AsyncConnection, *, spec: Spec, source: str, slot: str
) -> Posterior:
    """Posterior for ``(source, slot)``. Validates source+slot on spec."""
    _check_source(spec, source)
    _check_slot(spec, slot)
    return await trust_posteriors.get_posterior(conn, source, slot)


async def reset_posterior(conn: psycopg.AsyncConnection, *, source: str, slot: str) -> bool:
    """Drop the per-(source, slot) posterior, reverting to the uniform prior.

    No spec validation: a posterior may exist for sources/slots that have
    since been removed from the spec; admins should still be able to clean
    those up.
    """
    return await trust_posteriors.reset_posterior(conn, source, slot)


async def record_feedback(
    conn: psycopg.AsyncConnection,
    *,
    spec: Spec,
    source: str,
    slot: str,
    success: bool,
) -> Posterior:
    """Record one Bernoulli observation. Validates source+slot on spec."""
    _check_source(spec, source)
    _check_slot(spec, slot)
    return await trust_posteriors.record_feedback(conn, source, slot, success)
