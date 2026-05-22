"""ValidationSweepWorkflow — periodically runs spec.emit_validation()."""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from hitch.workflows import activities


@workflow.defn
class ValidationSweepWorkflow:
    @workflow.run
    async def run(self) -> dict:
        return await workflow.execute_activity(
            activities.run_validation_sweep,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
