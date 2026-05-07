"""Trust-resolved query layer for the data plane.

Per commitment 7 (multi-valued canonical, query-time trust resolution),
resolution happens at *query time*, not write time. Each Slot declares a
``resolution_policy``; trust state lives in ``knot.db.trust_config``
(scalar per-source) and ``knot.db.trust_posteriors`` (per-(source, slot)
Beta posterior).

Pure logic: no SQL strings, no postgres imports — calls db primitives
for data and computes the resolution in Python.

Resolution policies implemented today:
  - ``ARGMAX_TRUST``     — highest-trust non-null contribution per slot,
                           tie-break by source name. Uses scalar
                           trust_config.
  - ``THOMPSON_SAMPLING`` — sample θ ~ Beta(α, β) per (source, slot) and
                           pick the contribution whose source drew highest.
                           Uses trust_posteriors.
  - ``UCB1``             — pick by upper-confidence bound on the
                           per-(source, slot) Beta mean (μ + c√(ln N / n)).
                           Uses trust_posteriors.

Other policies declared on Slot.resolution_policy raise
``NotImplementedError``: ``MODE``, ``WEIGHTED_VOTE``, ``MEDIAN_NUMERIC``,
``LATEST_WATERMARK``, ``UNIQUE_OR_FAIL``.

Multivalued slots: union of all per-source contributions (dedup),
regardless of policy — multi-valued canonical is the bag of contributions.
"""

from __future__ import annotations

import math
import random
from typing import Any, Optional

import psycopg

from knot.db import graph_store, trust_config, trust_posteriors
from knot.db.trust_posteriors import PRIOR_ALPHA, PRIOR_BETA, Posterior
from knot.ontology import OntologyClass, ResolutionPolicy, Slot


UCB_EXPLORATION = 1.0


def resolve_entity(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
    rng: Optional[random.Random] = None,
) -> dict[str, Any] | None:
    """Build one resolved record for a canonical_id, applying per-slot policy.

    Returns None if the canonical_id has no contributions.
    """
    contribs = graph_store.get_canonical_contributions(
        conn, cls=cls, canonical_id=canonical_id, as_of=as_of,
    )
    if not contribs:
        return None

    scalar_trust = trust_config.list_scores(conn)
    posteriors = {
        (p.source, p.slot): p for p in trust_posteriors.list_posteriors(conn)
    }
    rng = rng or random

    resolved: dict[str, Any] = {"_canonical_id": canonical_id}
    for slot in cls.slots:
        if getattr(slot, "derivation", None) is not None:
            continue
        if slot.multivalued:
            resolved[slot.name] = _union_multivalued(slot, contribs)
        else:
            resolved[slot.name] = _resolve_scalar(
                slot, contribs, scalar_trust, posteriors, rng,
            )
    return resolved


def _resolve_scalar(
    slot: Slot,
    contribs: list[dict[str, Any]],
    scalar_trust: dict[str, float],
    posteriors: dict[tuple[str, str], Posterior],
    rng: random.Random,
) -> Any:
    non_null = [
        (c["_source"], c[slot.name])
        for c in contribs
        if c.get(slot.name) is not None
    ]
    if not non_null:
        return None

    policy = slot.resolution_policy
    if policy == ResolutionPolicy.ARGMAX_TRUST:
        return _argmax_trust(non_null, scalar_trust)
    if policy == ResolutionPolicy.THOMPSON_SAMPLING:
        return _thompson(slot, non_null, posteriors, rng)
    if policy == ResolutionPolicy.UCB1:
        return _ucb1(slot, non_null, posteriors)
    raise NotImplementedError(
        f"Resolution policy {policy.value!r} for slot {slot.name!r} not implemented yet"
    )


def _argmax_trust(
    non_null: list[tuple[str, Any]],
    scalar_trust: dict[str, float],
) -> Any:
    ranked = sorted(
        non_null,
        key=lambda sv: (-scalar_trust.get(sv[0], trust_config.DEFAULT_TRUST), sv[0]),
    )
    return ranked[0][1]


def _post_for(
    posteriors: dict[tuple[str, str], Posterior],
    source: str,
    slot_name: str,
) -> Posterior:
    return posteriors.get(
        (source, slot_name),
        Posterior(source=source, slot=slot_name, alpha=PRIOR_ALPHA, beta=PRIOR_BETA),
    )


def _thompson(
    slot: Slot,
    non_null: list[tuple[str, Any]],
    posteriors: dict[tuple[str, str], Posterior],
    rng: random.Random,
) -> Any:
    samples: list[tuple[float, str, Any]] = []
    for source, value in non_null:
        post = _post_for(posteriors, source, slot.name)
        theta = rng.betavariate(post.alpha, post.beta)
        samples.append((theta, source, value))
    samples.sort(key=lambda t: (-t[0], t[1]))
    return samples[0][2]


def _ucb1(
    slot: Slot,
    non_null: list[tuple[str, Any]],
    posteriors: dict[tuple[str, str], Posterior],
) -> Any:
    posts = [_post_for(posteriors, source, slot.name) for source, _ in non_null]
    total_obs = sum(p.observations for p in posts)
    N = max(total_obs, 1.0)
    scored: list[tuple[float, str, Any]] = []
    for (source, value), post in zip(non_null, posts):
        n = max(post.observations, 1.0)
        bonus = UCB_EXPLORATION * math.sqrt(math.log(N + 1) / n)
        scored.append((post.mean + bonus, source, value))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][2]


def _union_multivalued(slot: Slot, contribs: list[dict[str, Any]]) -> list[Any] | None:
    seen: list[Any] = []
    for c in contribs:
        values = c.get(slot.name)
        if values is None:
            continue
        for v in values:
            if v not in seen:
                seen.append(v)
    return seen if seen else None
