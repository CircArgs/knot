"""ERWorkflow — assigns canonical_ids across all sources of a class.

Two policies:

- **Embedding k-NN** (Person, Studio, Movie) — each unresolved binding's
  identity-embedding is k-NN-matched against the same class's
  already-stamped bindings in OTHER sources. Within-threshold match
  reuses the neighbor's canonical_id; above-threshold mints a fresh
  sha1-of-identity (deterministic for replay safety). Fuses
  cross-source variants ("Miramax" / "Miramax Films",
  "P. T. Anderson" / "Paul Thomas Anderson") that the prior sha1-of-name
  policy split.

- **Composite sha1-of-FKs** (Credit) — Credit's identity is
  (movie_canonical, person_canonical, role). Because Movie + Person
  ER stamps the FK columns canonical via the assign_canonicals fanout
  + translate_fks recovery, by the time Credit ER runs the binding's
  movie/person columns hold canonical-ids already. sha1 over those
  collapses cross-source claims about the same credit cleanly.

Source ordering matters: imdb runs first (no neighbors yet → all
mints fresh), tmdb runs next (k-NN against imdb's stamps), rt runs
last (k-NN against imdb + tmdb).
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from hitch.workflows import activities


_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    maximum_attempts=3,
)

# Classes with an embedding slot — Credit's identity is composite and
# falls through to the sha1-of-FK-canonicals path.
_EMBEDDING_ER_CLASSES = ("Person", "Studio", "Movie")


@workflow.defn
class ERWorkflow:
    @workflow.run
    async def run(
        self,
        source_names: list[str],
        class_name: str,
        threshold: float = 0.35,
    ) -> dict:
        if class_name in _EMBEDDING_ER_CLASSES:
            return await self._embedding_er(source_names, class_name, threshold)
        return await self._sha1_er(source_names, class_name)

    async def _embedding_er(
        self, source_names: list[str], class_name: str, threshold: float
    ) -> dict:
        per_source: dict[str, dict] = {}
        for source_name in source_names:
            r = await workflow.execute_activity(
                activities.er_via_embeddings,
                args=[source_name, class_name, threshold],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_RETRY,
            )
            per_source[source_name] = r
        totals = {
            "matched": sum(r["matched"] for r in per_source.values()),
            "minted": sum(r["minted"] for r in per_source.values()),
            "candidates": sum(r["candidates"] for r in per_source.values()),
        }
        return {"policy": "embedding_knn", **totals, "per_source": per_source}

    async def _sha1_er(self, source_names: list[str], class_name: str) -> dict:
        total = 0
        per_source: dict[str, int] = {}
        for source_name in source_names:
            rows = await workflow.execute_activity(
                activities.fetch_unresolved,
                args=[source_name, class_name],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            if not rows:
                per_source[source_name] = 0
                continue
            decisions = await workflow.execute_activity(
                activities.decide_canonicals,
                args=[source_name, class_name, rows],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            stamped = await workflow.execute_activity(
                activities.assign_canonicals,
                args=[source_name, class_name, decisions],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_RETRY,
            )
            per_source[source_name] = stamped
            total += stamped
        return {"policy": "sha1_of_fks", "resolved": total, "per_source": per_source}
