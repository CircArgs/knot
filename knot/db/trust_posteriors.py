"""Per-(source, slot) Beta posteriors for bandit-style trust resolution.

Each ``(source, slot)`` pair has a Beta(α, β) posterior over the
"this source got this slot right" probability. Default prior is
Beta(1, 1) — uniform. Each Bernoulli observation increments α on
success or β on failure.

The posteriors drive ``ResolutionPolicy.POSTERIOR_MEAN`` (argmax over
α/(α+β)) and ``LCB`` (argmax over mean − k·stddev) in
``knot.graph.resolve``. Both are deterministic; we don't sample.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

PRIOR_ALPHA = 1.0
PRIOR_BETA = 1.0


@dataclass(frozen=True)
class Posterior:
    source: str
    slot: str
    alpha: float
    beta: float

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def observations(self) -> float:
        """Estimated number of Bernoulli observations recorded for this pair.

        Computed as ``(α + β) − (PRIOR_ALPHA + PRIOR_BETA)``, clamped to 0.

        ASSUMPTION: the row was initialised at the module-level prior
        (``PRIOR_ALPHA=1, PRIOR_BETA=1``). If a row is hand-seeded with
        non-prior starting values (e.g. via a direct INSERT), this property
        will over- or under-count accordingly — it cannot distinguish "prior
        mass" from "manually set mass".  All rows created via
        ``record_feedback`` start at the prior and satisfy the assumption.
        """
        return max(self.alpha + self.beta - (PRIOR_ALPHA + PRIOR_BETA), 0.0)


async def get_posterior(conn: psycopg.AsyncConnection, source: str, slot: str) -> Posterior:
    """Posterior for ``(source, slot)``. Falls back to the uniform prior
    if no observations recorded yet."""
    row = await (
        await conn.execute(
            "SELECT alpha, beta FROM trust_posteriors WHERE source_name = %s AND slot_name = %s",
            (source, slot),
        )
    ).fetchone()
    if row:
        return Posterior(source=source, slot=slot, alpha=row[0], beta=row[1])
    return Posterior(source=source, slot=slot, alpha=PRIOR_ALPHA, beta=PRIOR_BETA)


async def list_posteriors(conn: psycopg.AsyncConnection) -> list[Posterior]:
    rows = await (
        await conn.execute(
            "SELECT source_name, slot_name, alpha, beta FROM trust_posteriors "
            "ORDER BY source_name, slot_name"
        )
    ).fetchall()
    return [Posterior(source=r[0], slot=r[1], alpha=r[2], beta=r[3]) for r in rows]


async def list_for_slot(conn: psycopg.AsyncConnection, slot: str) -> list[Posterior]:
    rows = await (
        await conn.execute(
            "SELECT source_name, slot_name, alpha, beta FROM trust_posteriors "
            "WHERE slot_name = %s ORDER BY source_name",
            (slot,),
        )
    ).fetchall()
    return [Posterior(source=r[0], slot=r[1], alpha=r[2], beta=r[3]) for r in rows]


async def record_feedback(
    conn: psycopg.AsyncConnection,
    source: str,
    slot: str,
    success: bool,
) -> Posterior:
    """Update the ``(source, slot)`` posterior with one Bernoulli observation.

    Atomic at the DB layer — a single UPSERT with delta math, so concurrent
    feedback on the same (source, slot) doesn't lose increments.
    """
    delta_alpha = 1.0 if success else 0.0
    delta_beta = 0.0 if success else 1.0
    row = await (
        await conn.execute(
            "INSERT INTO trust_posteriors (source_name, slot_name, alpha, beta) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (source_name, slot_name) DO UPDATE "
            "SET alpha = trust_posteriors.alpha + %s, "
            "    beta  = trust_posteriors.beta  + %s, "
            "    updated_at = now() "
            "RETURNING alpha, beta",
            (
                source,
                slot,
                PRIOR_ALPHA + delta_alpha,
                PRIOR_BETA + delta_beta,
                delta_alpha,
                delta_beta,
            ),
        )
    ).fetchone()
    return Posterior(source=source, slot=slot, alpha=row[0], beta=row[1])


async def reset_posterior(
    conn: psycopg.AsyncConnection,
    source: str,
    slot: str,
) -> bool:
    """Drop the posterior row, reverting the pair to the uniform prior."""
    cur = await conn.execute(
        "DELETE FROM trust_posteriors WHERE source_name = %s AND slot_name = %s",
        (source, slot),
    )
    return cur.rowcount > 0
