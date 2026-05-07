"""ToyMaestro: in-process, synchronous dispatcher that mimics Maestro's API.

Execution is intentionally synchronous — workflow steps run inline when
``start_run()`` is called.  This is honest for a dev/test toy: no hidden
async, no background threads.

Topological order for multi-step workflows: we walk the ``transition.successors``
DAG from each step and execute in BFS order.  Cycle detection raises immediately.
If any step is FATALLY_FAILED the run halts and the workflow is FAILED.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from knot.orchestrator.toy_maestro import storage
from knot.orchestrator.toy_maestro.executors.sql_step import execute_sql_step
from knot.orchestrator.toy_maestro.workflow_models import (
    RunCtx,
    RunInstance,
    SqlSubType,
    Step,
    StepRunResult,
    StepStatus,
    StepType,
    Workflow,
    WorkflowStatus,
    TERMINAL_STEP_STATUSES,
)


class WorkflowNotFound(KeyError):
    pass


class RunNotFound(KeyError):
    pass


class ToyMaestro:
    """In-process dispatcher whose JSON surface mimics Maestro's REST API.

    All state is persisted to Postgres via ``storage``; the dispatcher is
    stateless between calls (no in-memory caches).
    """

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn
        storage.apply_schema(dsn)

    # ------------------------------------------------------------------
    # Workflow registration
    # ------------------------------------------------------------------

    def register_workflow(self, workflow_def: Workflow) -> str:
        """Persist a workflow definition.  Returns workflow_id."""
        storage.save_workflow(workflow_def, self._dsn)
        return workflow_def.id

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, workflow_id: str, params: dict[str, Any] | None = None) -> str:
        """Start a run synchronously.  Returns run_id.

        Steps execute in topological order before this method returns.
        """
        wf = storage.load_workflow(workflow_id, self._dsn)
        if wf is None:
            raise WorkflowNotFound(workflow_id)

        run_id = str(uuid.uuid4())
        run_params = params or {}

        instance = RunInstance(
            workflow_id=workflow_id,
            run_params=run_params,
            status=WorkflowStatus.IN_PROGRESS,
            start_time=datetime.now(timezone.utc),
        )
        storage.save_run(run_id, instance, self._dsn)

        ctx = RunCtx(
            run_id=run_id,
            workflow_id=workflow_id,
            workflow_uuid=instance.workflow_uuid,
            run_params=run_params,
        )

        try:
            step_results = self._execute_dag(wf, ctx)
        except Exception as exc:
            # Unexpected dispatcher-level error — mark the run FAILED.
            instance.status = WorkflowStatus.FAILED
            instance.end_time = datetime.now(timezone.utc)
            storage.save_run(run_id, instance, self._dsn)
            raise

        # Aggregate workflow outcome from step results
        all_terminal = all(r.status in TERMINAL_STEP_STATUSES for r in step_results)
        any_failed = any(
            r.status
            in {
                StepStatus.USER_FAILED,
                StepStatus.PLATFORM_FAILED,
                StepStatus.FATALLY_FAILED,
                StepStatus.INTERNALLY_FAILED,
                StepStatus.TIMEOUT_FAILED,
                StepStatus.TIMED_OUT,
            }
            for r in step_results
        )

        instance.status = WorkflowStatus.FAILED if any_failed else WorkflowStatus.SUCCEEDED
        instance.end_time = datetime.now(timezone.utc)
        instance.step_runs = step_results
        storage.save_run(run_id, instance, self._dsn)

        return run_id

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> RunInstance:
        """Fetch a run instance by run_id."""
        instance = storage.load_run(run_id, self._dsn)
        if instance is None:
            raise RunNotFound(run_id)
        return instance

    # ------------------------------------------------------------------
    # Step execution helpers
    # ------------------------------------------------------------------

    def step_executor(self, step: Step, ctx: RunCtx) -> StepRunResult:
        """Execute a single step.  Dispatches based on sub_type."""
        sql_sub_types = {SqlSubType.SPARKSQL, SqlSubType.TRINO}
        if step.type == StepType.TITUS and step.sub_type in sql_sub_types:
            result = execute_sql_step(step, ctx)
        elif step.type == StepType.NOOP:
            result = StepRunResult(
                step_id=step.id,
                status=StepStatus.SUCCEEDED,
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                duration_ms=0,
                row_count=0,
            )
        else:
            result = StepRunResult(
                step_id=step.id,
                status=StepStatus.PLATFORM_FAILED,
                error=f"Unsupported step type={step.type!r} sub_type={step.sub_type!r}",
                start_time=datetime.now(timezone.utc),
                end_time=datetime.now(timezone.utc),
                duration_ms=0,
            )
        # Persist immediately
        storage.upsert_step_run(ctx.run_id, result, self._dsn)
        return result

    # ------------------------------------------------------------------
    # DAG traversal
    # ------------------------------------------------------------------

    def _execute_dag(self, wf: Workflow, ctx: RunCtx) -> list[StepRunResult]:
        """Execute steps in topological BFS order.

        Halts the run on any failed step (fail-fast).
        Returns all executed StepRunResults.
        """
        step_map = {s.id: s for s in wf.steps}
        # Build adjacency list from transition.successors
        successors: dict[str, list[str]] = {
            s.id: list(s.transition.successors) for s in wf.steps
        }
        # Build in-degree map to find start nodes
        in_degree: dict[str, int] = {s.id: 0 for s in wf.steps}
        for step in wf.steps:
            for succ in step.transition.successors:
                in_degree[succ] = in_degree.get(succ, 0) + 1

        # BFS queue: start with zero-in-degree nodes (preserving definition order)
        from collections import deque

        queue: deque[str] = deque(
            s.id for s in wf.steps if in_degree[s.id] == 0
        )
        executed: dict[str, StepRunResult] = {}
        results: list[StepRunResult] = []
        visited: set[str] = set()

        while queue:
            step_id = queue.popleft()
            if step_id in visited:
                continue
            visited.add(step_id)

            if step_id not in step_map:
                continue  # referenced but not defined — skip

            step = step_map[step_id]
            result = self.step_executor(step, ctx)
            executed[step_id] = result
            results.append(result)

            failed_statuses = {
                StepStatus.USER_FAILED,
                StepStatus.PLATFORM_FAILED,
                StepStatus.FATALLY_FAILED,
                StepStatus.INTERNALLY_FAILED,
                StepStatus.TIMEOUT_FAILED,
                StepStatus.TIMED_OUT,
            }
            if result.status in failed_statuses:
                # Halt on failure — downstream steps won't run
                break

            for succ_id in successors.get(step_id, []):
                in_degree[succ_id] -= 1
                if in_degree[succ_id] == 0:
                    queue.append(succ_id)

        return results
