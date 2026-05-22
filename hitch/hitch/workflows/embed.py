"""EmbedWorkflow — one per (source, class, vector_slot)."""

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
class EmbedWorkflow:
    """Pull unembedded rows → compute → write. Loops until empty."""

    @workflow.run
    async def run(
        self,
        source_name: str,
        class_name: str,
        slot_name: str,
        batch_size: int = 32,
    ) -> dict:
        total = 0
        loops = 0
        while True:
            loops += 1
            rows = await workflow.execute_activity(
                activities.fetch_titles_to_embed,
                args=[source_name, class_name, slot_name, batch_size],
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_RETRY,
            )
            if not rows:
                break
            texts = [r["text"] for r in rows]
            vectors = await workflow.execute_activity(
                activities.compute_embeddings,
                args=[texts],
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=_RETRY,
            )
            payload = [
                {"source_identifier": r["source_identifier"], slot_name: v}
                for r, v in zip(rows, vectors, strict=True)
            ]
            written = await workflow.execute_activity(
                activities.write_embeddings,
                args=[source_name, class_name, slot_name, payload],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=_RETRY,
            )
            total += written
            if len(rows) < batch_size:
                break
            if loops > 50:  # safety valve
                break
        return {"embedded": total, "loops": loops}
