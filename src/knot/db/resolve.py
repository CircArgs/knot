"""Trust-resolved query layer for the data plane.

Per commitment 7 (multi-valued canonical, query-time trust resolution),
resolution happens at *query time*, not write time. Each Slot declares a
``resolution_policy``; per-source trust scores live in ``trust_config``;
resolution is **per-slot** (each slot's value comes from whichever source
wins for that slot — not from one row).

Resolution policies implemented today:
  - ``ARGMAX_TRUST`` (the default): highest-trust non-null contribution
    per slot, tie-break by source name.

Other policies declared on Slot.resolution_policy raise
``NotImplementedError`` until they land:
  ``MODE``, ``WEIGHTED_VOTE``, ``MEDIAN_NUMERIC``, ``LATEST_WATERMARK``,
  ``UNIQUE_OR_FAIL``.

Multivalued slots: union of all per-source contributions (dedup),
regardless of policy — multi-valued canonical is the bag of contributions.
"""

from __future__ import annotations

from typing import Any

import psycopg

from knot.db import graph_store, trust_config
from knot.ontology import OntologyClass, ResolutionPolicy, Slot


def resolve_entity(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
) -> dict[str, Any] | None:
    """Build one resolved record for a canonical_id, applying per-slot policy.

    Returns None if the canonical_id has no contributions.
    """
    contribs = graph_store.get_canonical_contributions(
        conn, cls=cls, canonical_id=canonical_id, as_of=as_of,
    )
    if not contribs:
        return None

    trust = trust_config.list_scores(conn)
    resolved: dict[str, Any] = {"_canonical_id": canonical_id}
    for slot in cls.slots:
        if getattr(slot, "derivation", None) is not None:
            continue
        if slot.multivalued:
            resolved[slot.name] = _union_multivalued(slot, contribs)
        else:
            resolved[slot.name] = _resolve_scalar(slot, contribs, trust)
    return resolved


def _resolve_scalar(
    slot: Slot,
    contribs: list[dict[str, Any]],
    trust: dict[str, float],
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
        ranked = sorted(
            non_null,
            key=lambda sv: (-trust.get(sv[0], trust_config.DEFAULT_TRUST), sv[0]),
        )
        return ranked[0][1]
    raise NotImplementedError(
        f"Resolution policy {policy.value!r} for slot {slot.name!r} not implemented yet"
    )


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
