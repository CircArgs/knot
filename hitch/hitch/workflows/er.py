"""ERWorkflow — assigns canonical_ids across all sources of a class.

For the demo, the policy is deterministic-mint (sha1 over the identity
fields), so re-runs converge to the same canonical_ids — the
replay-safety contract from docs/temporal-adapter.md.
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


@workflow.defn
class ERWorkflow:
    """For each source × class, mint canonical_ids for unresolved rows."""

    @workflow.run
    async def run(
        self,
        source_names: list[str],
        class_name: str,
    ) -> dict:
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
        return {"resolved": total, "per_source": per_source}
