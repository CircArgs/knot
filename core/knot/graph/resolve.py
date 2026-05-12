"""Trust-resolved query layer for the data plane.

Per commitment 7 (multi-valued canonical, query-time trust resolution),
resolution happens at *query time*, not write time. Each Slot declares a
``resolution_policy``; trust state lives in ``knot.db.trust_config``
(scalar per-source) and ``knot.db.trust_posteriors`` (per-(source, slot)
Beta posterior).

Pure logic: no SQL strings, no postgres imports — calls db primitives
for data and computes the resolution in Python.

**Deterministic by design.** Knot queries are about surfacing the best
current estimate, not exploring an action space — so policies are
argmax-style, never sampling. Beta posteriors capture state; corrections
update them; queries are deterministic given that state.

Resolution policies implemented today:
  - ``ARGMAX_TRUST``    — highest-trust non-null contribution per slot,
                          tie-break by source name. Uses scalar
                          trust_config (human-set, doesn't learn).
  - ``POSTERIOR_MEAN``  — argmax over α/(α+β) per (source, slot).
                          Uses trust_posteriors; learns from corrections;
                          deterministic and monotone in observations.
  - ``LCB``             — argmax over (mean − k·stddev) per (source, slot).
                          Conservative variant: penalises sources with
                          high uncertainty (low observation count). Same
                          state as POSTERIOR_MEAN, different optimum.

Multivalued slots: union of all per-source contributions (dedup),
regardless of policy — multi-valued canonical is the bag of contributions.
"""

from __future__ import annotations

import math
from typing import Any

import psycopg

from knot.db import graph_store, trust_config, trust_posteriors
from knot.db.trust_posteriors import PRIOR_ALPHA, PRIOR_BETA, Posterior
from knot.spec import Array, OntologyClass, ResolutionPolicy, Slot

LCB_K = 1.0  # stddev multiplier for the Lower Confidence Bound penalty


async def resolve_entity(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
) -> dict[str, Any] | None:
    """Build one resolved record for a canonical_id, applying per-slot policy.

    Returns None if the canonical_id has no contributions.
    """
    contribs = await graph_store.get_canonical_contributions(
        conn,
        cls=cls,
        canonical_id=canonical_id,
        as_of=as_of,
    )
    if not contribs:
        return None

    scalar_trust = await trust_config.list_scores(conn)
    posteriors = {(p.source, p.slot): p for p in await trust_posteriors.list_posteriors(conn)}

    # Walk the full slot set including inherited slots (is_a chain).
    # Defined classes (backed by VIEW) inherit all slots from their parent.
    seen_slot_names: set[str] = set()
    all_slots: list[Slot] = []
    current = cls
    while current is not None:
        for s in current.slots:
            if s.name not in seen_slot_names:
                seen_slot_names.add(s.name)
                all_slots.append(s)
        current = current.is_a

    resolved: dict[str, Any] = {"_canonical_id": canonical_id}
    for slot in all_slots:
        if getattr(slot, "derivation", None) is not None:
            continue
        if isinstance(slot.type, Array):
            resolved[slot.name] = _union_multivalued(slot, contribs)
        else:
            resolved[slot.name] = _resolve_scalar(
                slot,
                contribs,
                scalar_trust,
                posteriors,
            )
    return resolved


def _resolve_scalar(
    slot: Slot,
    contribs: list[dict[str, Any]],
    scalar_trust: dict[str, float],
    posteriors: dict[tuple[str, str], Posterior],
) -> Any:
    non_null = [(c["_source"], c[slot.name]) for c in contribs if c.get(slot.name) is not None]
    if not non_null:
        return None

    policy = slot.resolution_policy
    if policy == ResolutionPolicy.ARGMAX_TRUST:
        return _argmax_trust(non_null, scalar_trust)
    if policy == ResolutionPolicy.POSTERIOR_MEAN:
        return _posterior_mean(slot, non_null, posteriors)
    if policy == ResolutionPolicy.LCB:
        return _lcb(slot, non_null, posteriors)
    raise AssertionError(  # exhaustive over ResolutionPolicy
        f"Unhandled resolution policy {policy!r} for slot {slot.name!r}"
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


def _posterior_mean(
    slot: Slot,
    non_null: list[tuple[str, Any]],
    posteriors: dict[tuple[str, str], Posterior],
) -> Any:
    """Argmax over per-(source, slot) Beta posterior mean. Deterministic;
    same state → same answer."""
    scored = [
        (_post_for(posteriors, source, slot.name).mean, source, value) for source, value in non_null
    ]
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][2]


def _lcb(
    slot: Slot,
    non_null: list[tuple[str, Any]],
    posteriors: dict[tuple[str, str], Posterior],
) -> Any:
    """Argmax over (mean − k·stddev) per (source, slot). Penalises sources
    with high uncertainty (low observation count); deterministic."""
    scored: list[tuple[float, str, Any]] = []
    for source, value in non_null:
        post = _post_for(posteriors, source, slot.name)
        a, b = post.alpha, post.beta
        var = (a * b) / ((a + b) ** 2 * (a + b + 1.0))
        stddev = math.sqrt(var)
        scored.append((post.mean - LCB_K * stddev, source, value))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return scored[0][2]


def _union_multivalued(slot: Slot, contribs: list[dict[str, Any]]) -> list[Any] | None:
    # Flatten all per-source value lists and dedup while preserving order.
    # dict.fromkeys gives O(N) order-preserving dedup for hashable values
    # (postgres arrays of primitives always are); fall back to the O(N²)
    # list-scan path for any unhashable element.
    flat: list[Any] = []
    for c in contribs:
        values = c.get(slot.name)
        if values is None:
            continue
        flat.extend(values)
    if not flat:
        return None
    try:
        return list(dict.fromkeys(flat))
    except TypeError:
        seen: list[Any] = []
        for v in flat:
            if v not in seen:
                seen.append(v)
        return seen
