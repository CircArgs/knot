"""Per-(source, slot) Beta posteriors for bandit-style trust resolution.

Each ``(source, slot)`` pair has a Beta(α, β) posterior over the
"this source got this slot right" probability. Default prior is
Beta(1, 1) — uniform. Each Bernoulli observation increments α on
success or β on failure.

The posteriors drive ``ResolutionPolicy.THOMPSON_SAMPLING`` (sample
θ ~ Beta(α, β) per source-slot, argmax θ) and ``UCB1`` (μ + c√(ln N / n))
in ``knot.db.resolve``.
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
        """Number of observations seen (excludes the (α=1, β=1) prior)."""
        return max(self.alpha + self.beta - (PRIOR_ALPHA + PRIOR_BETA), 0.0)


def get_posterior(conn: psycopg.Connection, source: str, slot: str) -> Posterior:
    """Posterior for ``(source, slot)``. Falls back to the uniform prior
    if no observations recorded yet."""
    row = conn.execute(
        "SELECT alpha, beta FROM trust_posteriors "
        "WHERE source_name = %s AND slot_name = %s",
        (source, slot),
    ).fetchone()
    if row:
        return Posterior(source=source, slot=slot, alpha=row[0], beta=row[1])
    return Posterior(source=source, slot=slot, alpha=PRIOR_ALPHA, beta=PRIOR_BETA)


def list_posteriors(conn: psycopg.Connection) -> list[Posterior]:
    rows = conn.execute(
        "SELECT source_name, slot_name, alpha, beta FROM trust_posteriors "
        "ORDER BY source_name, slot_name"
    ).fetchall()
    return [
        Posterior(source=r[0], slot=r[1], alpha=r[2], beta=r[3]) for r in rows
    ]


def list_for_slot(conn: psycopg.Connection, slot: str) -> list[Posterior]:
    rows = conn.execute(
        "SELECT source_name, slot_name, alpha, beta FROM trust_posteriors "
        "WHERE slot_name = %s ORDER BY source_name",
        (slot,),
    ).fetchall()
    return [
        Posterior(source=r[0], slot=r[1], alpha=r[2], beta=r[3]) for r in rows
    ]


def record_feedback(
    conn: psycopg.Connection,
    source: str,
    slot: str,
    success: bool,
) -> Posterior:
    """Update the ``(source, slot)`` posterior with one Bernoulli observation."""
    current = get_posterior(conn, source, slot)
    new_alpha = current.alpha + (1.0 if success else 0.0)
    new_beta = current.beta + (0.0 if success else 1.0)
    conn.execute(
        "INSERT INTO trust_posteriors (source_name, slot_name, alpha, beta) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (source_name, slot_name) DO UPDATE "
        "SET alpha = EXCLUDED.alpha, beta = EXCLUDED.beta, updated_at = now()",
        (source, slot, new_alpha, new_beta),
    )
    return Posterior(source=source, slot=slot, alpha=new_alpha, beta=new_beta)


def reset_posterior(
    conn: psycopg.Connection,
    source: str,
    slot: str,
) -> bool:
    """Drop the posterior row, reverting the pair to the uniform prior."""
    cur = conn.execute(
        "DELETE FROM trust_posteriors "
        "WHERE source_name = %s AND slot_name = %s",
        (source, slot),
    )
    return cur.rowcount > 0
