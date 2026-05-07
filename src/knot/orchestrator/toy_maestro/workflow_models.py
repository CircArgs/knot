"""Pydantic models matching Maestro's externally-visible workflow definition shape.

Sources consulted:
  maestro-common/src/main/java/com/netflix/maestro/models/definition/Workflow.java
  maestro-common/src/main/java/com/netflix/maestro/models/definition/Step.java
  maestro-common/src/main/java/com/netflix/maestro/models/definition/TypedStep.java
  maestro-common/src/main/java/com/netflix/maestro/models/definition/StepType.java
  maestro-common/src/main/java/com/netflix/maestro/models/instance/WorkflowInstance.java
  maestro-common/src/main/java/com/netflix/maestro/models/instance/StepInstance.java
  maestro-common/src/main/java/com/netflix/maestro/models/instance/StepRuntimeState.java
  maestro-common/src/main/java/com/netflix/maestro/models/api/WorkflowStartResponse.java

Naming and status strings are taken verbatim from Maestro's enums.

Scope: we implement the subset of Maestro's shape relevant to lake-query
workflows (sparksql_step, trino_step) executed synchronously via DuckDB.
Unsupported Maestro fields (triggers, tag permits, titus, kubernetes, signal
dependencies) are excluded rather than carried as opaque dicts — they are
simply not part of this toy's surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Step type + sub_type vocabulary (matches Maestro's StepType enum values)
# ---------------------------------------------------------------------------


class StepType(str, Enum):
    """Maestro step types that the toy supports (leaf steps only)."""

    NOOP = "NoOp"
    # Maestro does not have SPARKSQL / TRINO as first-class StepType values.
    # In Maestro these are represented as StepType=Titus|Kubernetes with
    # sub_type set to the engine name.  For the toy we treat them as TITUS
    # (the closest Maestro analogue) with sub_type "sparksql" / "trino" so
    # the JSON shape is faithful.  SQL execution routes to DuckDB locally.
    TITUS = "Titus"


class SqlSubType(str, Enum):
    """sub_type values the toy recognises for lake-query steps."""

    SPARKSQL = "sparksql"
    TRINO = "trino"


# ---------------------------------------------------------------------------
# Step definition  (matches Maestro TypedStep JSON shape)
# ---------------------------------------------------------------------------


class StepTransition(BaseModel):
    """Maestro step transition — which steps to run after this one."""

    model_config = {"extra": "forbid"}

    successors: list[str] = Field(default_factory=list)
    # Maestro also supports failure_successors; omitted — not in scope.


class Step(BaseModel):
    """Single step in a workflow definition.

    Mirrors Maestro's TypedStep with the fields relevant to lake-query steps.
    ``params`` is the primary payload: for sql steps it must contain ``sql``.
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str | None = None
    description: str | None = None
    type: StepType = StepType.TITUS
    sub_type: SqlSubType | None = None
    # Runtime parameter definitions — for SQL steps, key "sql" is required.
    params: dict[str, Any] = Field(default_factory=dict)
    transition: StepTransition = Field(default_factory=StepTransition)
    timeout: int | None = None  # seconds; None = no timeout


# ---------------------------------------------------------------------------
# Workflow definition  (matches Maestro's Workflow JSON shape)
# ---------------------------------------------------------------------------


class Workflow(BaseModel):
    """Workflow definition submitted to register_workflow().

    Field names match Maestro's snake_case JSON property names.
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str | None = None
    description: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    steps: list[Step]


# ---------------------------------------------------------------------------
# Run-instance lifecycle  (matches Maestro's WorkflowInstance.Status enum)
# ---------------------------------------------------------------------------


class WorkflowStatus(str, Enum):
    """Maestro WorkflowInstance.Status — verbatim from the Java enum."""

    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    TIMED_OUT = "TIMED_OUT"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    SUCCEEDED = "SUCCEEDED"


class StepStatus(str, Enum):
    """Maestro StepInstance.Status — verbatim from the Java enum.

    Only the subset that the toy transitions through is used; the full set is
    listed for fidelity.
    """

    NOT_CREATED = "NOT_CREATED"
    CREATED = "CREATED"
    INITIALIZED = "INITIALIZED"
    PAUSED = "PAUSED"
    WAITING_FOR_SIGNALS = "WAITING_FOR_SIGNALS"
    EVALUATING_PARAMS = "EVALUATING_PARAMS"
    WAITING_FOR_PERMITS = "WAITING_FOR_PERMITS"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    FINISHING = "FINISHING"
    DISABLED = "DISABLED"
    UNSATISFIED = "UNSATISFIED"
    SKIPPED = "SKIPPED"
    SUCCEEDED = "SUCCEEDED"
    COMPLETED_WITH_ERROR = "COMPLETED_WITH_ERROR"
    USER_FAILED = "USER_FAILED"
    PLATFORM_FAILED = "PLATFORM_FAILED"
    FATALLY_FAILED = "FATALLY_FAILED"
    INTERNALLY_FAILED = "INTERNALLY_FAILED"
    STOPPED = "STOPPED"
    TIMEOUT_FAILED = "TIMEOUT_FAILED"
    TIMED_OUT = "TIMED_OUT"


TERMINAL_STEP_STATUSES: frozenset[StepStatus] = frozenset(
    {
        StepStatus.DISABLED,
        StepStatus.UNSATISFIED,
        StepStatus.SKIPPED,
        StepStatus.SUCCEEDED,
        StepStatus.COMPLETED_WITH_ERROR,
        StepStatus.USER_FAILED,
        StepStatus.PLATFORM_FAILED,
        StepStatus.FATALLY_FAILED,
        StepStatus.INTERNALLY_FAILED,
        StepStatus.STOPPED,
        StepStatus.TIMEOUT_FAILED,
        StepStatus.TIMED_OUT,
    }
)

TERMINAL_WORKFLOW_STATUSES: frozenset[WorkflowStatus] = frozenset(
    {
        WorkflowStatus.TIMED_OUT,
        WorkflowStatus.STOPPED,
        WorkflowStatus.FAILED,
        WorkflowStatus.SUCCEEDED,
    }
)


# ---------------------------------------------------------------------------
# Step run result  (runtime, per step)
# ---------------------------------------------------------------------------


class StepRunResult(BaseModel):
    """Runtime outcome for a single step execution."""

    model_config = {"extra": "forbid"}

    step_id: str
    step_attempt_id: int = 1  # Maestro: step_attempt_id
    status: StepStatus = StepStatus.NOT_CREATED
    start_time: datetime | None = None
    end_time: datetime | None = None
    duration_ms: int | None = None
    row_count: int | None = None
    error: str | None = None
    # Captured output rows (for small result sets; truncated at 1000 rows)
    output_rows: list[dict[str, Any]] | None = None


# ---------------------------------------------------------------------------
# Run instance  (the runtime record for one workflow execution)
# ---------------------------------------------------------------------------


class RunInstance(BaseModel):
    """Runtime record for one workflow execution.

    Field names mirror Maestro's WorkflowInstance JSON property names.
    workflow_instance_id + workflow_run_id are always 1 in the toy
    (no multi-instance / multi-run complexity needed).
    """

    model_config = {"extra": "forbid"}

    workflow_id: str
    workflow_instance_id: int = 1  # Maestro: long, min=1
    workflow_run_id: int = 1  # Maestro: long, min=1
    workflow_uuid: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_params: dict[str, Any] = Field(default_factory=dict)
    status: WorkflowStatus = WorkflowStatus.CREATED
    request_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    create_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    start_time: datetime | None = None
    end_time: datetime | None = None
    step_runs: list[StepRunResult] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Run context  (passed to executors)
# ---------------------------------------------------------------------------


class RunCtx(BaseModel):
    """Immutable context passed to each step executor."""

    model_config = {"extra": "forbid"}

    run_id: str
    workflow_id: str
    workflow_uuid: str
    run_params: dict[str, Any] = Field(default_factory=dict)
    lake_path: str | None = None
