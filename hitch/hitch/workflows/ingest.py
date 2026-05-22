"""IngestWorkflow — one per (source, class)."""

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
class IngestWorkflow:
    """Fetch → validate → bucket → upsert clean."""

    @workflow.run
    async def run(self, source_name: str, class_name: str) -> dict:
        rows = await workflow.execute_activity(
            activities.fetch_seed_batch,
            args=[source_name, class_name],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )
        if not rows:
            return {"fetched": 0, "written": 0, "violations": 0}

        buckets = await workflow.execute_activity(
            activities.validate_and_bucket,
            args=[source_name, class_name, rows],
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=_RETRY,
        )

        written = await workflow.execute_activity(
            activities.upsert_rows,
            args=[source_name, class_name, buckets["clean"]],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=_RETRY,
        )
        return {
            "fetched": len(rows),
            "written": written,
            "violations": len(buckets["violations"]),
        }
