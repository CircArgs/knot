# Toy Maestro — dev/test workflow scheduler mimic

**Status:** decided.  Implementation lives at
`src/knot/orchestrator/toy_maestro/`.

---

## What we're mimicking and why

Netflix Maestro is the production workflow orchestrator knot targets.  Knot's
future `Orchestrator` interface (`ToySchedulerOrchestrator` /
`MaestroOrchestrator`) translates a compiled `WorkflowSpec` into Maestro's
native workflow-definition JSON and dispatches it.  Before that wiring exists,
developers and CI need a local target that accepts the **same JSON shape** —
so the dispatch code can be written once and switched by config.  The toy
runs lake steps via DuckDB instead of Spark/Trino.  Every other behavioral
detail (status terminology, step identity fields, API URL structure) is taken
verbatim from Maestro's open-source codebase so the shapes are interchangeable.
The toy is anchored on Maestro because that's the production target; inventing
a new schema would produce a local tool that helps nobody.

---

## Maestro workflow-definition shape we adopt

Source paths (Netflix/maestro, commit as cloned):

- `maestro-common/src/main/java/com/netflix/maestro/models/definition/Workflow.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/definition/Step.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/definition/TypedStep.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/definition/StepType.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/instance/WorkflowInstance.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/instance/StepInstance.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/instance/StepRuntimeState.java`
- `maestro-common/src/main/java/com/netflix/maestro/models/api/WorkflowStartResponse.java`

Concrete workflow registration example:

```json
POST /maestro/api/v3/workflows
{
  "id": "movie_count_workflow",
  "name": "Movie count",
  "steps": [
    {
      "step": {
        "id": "count_movies",
        "type": "Titus",
        "sub_type": "sparksql",
        "params": {
          "sql": "SELECT count(*) AS n FROM read_csv_auto('/data/imdb_movies.csv')"
        },
        "transition": {"successors": []}
      }
    }
  ]
}
```

Step types in Maestro are `StepType` (Titus, Kubernetes, Notebook, …) with an
optional `sub_type` string for engine-specific routing.  Maestro does not have
first-class `SPARKSQL` or `TRINO` enum values; those names live in `sub_type`
on a `Titus` or `Kubernetes` step.  The toy follows that shape: `type=Titus`,
`sub_type=sparksql` | `sub_type=trino`, with SQL in `params.sql`.

---

## What we implement vs what we skip

**Implemented:**

- Workflow definition registration (`POST /maestro/api/v3/workflows`)
- Workflow definition retrieval (`GET /maestro/api/v3/workflows/{id}`)
- Run trigger (`POST /maestro/api/v3/workflows/{id}/instances`)
- Run status query (`GET /maestro/api/v3/workflows/{id}/instances/{run_id}`)
- Per-step status query (`GET /maestro/api/v3/workflows/{id}/instances/{run_id}/steps`)
- `sparksql` and `trino` sub-types — both execute via DuckDB locally
- Pydantic models with `extra="forbid"` matching Maestro's JSON property names
- Postgres persistence with `maestro_` table prefix (non-conflicting)
- Multi-step DAG with topological BFS execution and fail-fast halt
- `run_params` with `{key}` substitution into SQL
- Duration, row count, and output rows captured per step (capped at 1 000 rows)
- Full Maestro status vocabulary: `WorkflowInstance.Status` and
  `StepInstance.Status` strings verbatim

**Skipped — not needed for the single-team lake-query use case:**

- Time triggers / signal triggers — the toy is triggered via direct API calls
- Retry policy — no retries; single attempt per step
- Tag permits / tag rate limiting — no concurrency controls needed
- `foreach` / `while` / `subworkflow` / `template` steps — not in scope for
  lake-query workflows; skipped without hedging
- `Notebook` step runtime — Maestro runs these via Kubernetes + Papermill;
  requires a container environment, not meaningful for DuckDB-local dev
- `PAUSED` / `STOPPED` run actions — no pause/stop API in the toy
- Signal dependencies on steps — not needed for SQL steps
- Multi-instance / multi-run-id per workflow — toy always produces
  `workflow_instance_id=1`, `workflow_run_id=1`
- Async / background execution — toy runs steps synchronously for honesty and
  simplicity; no hidden threads

---

## Step types supported

| Maestro type | Maestro sub_type | Toy execution | Notes |
|---|---|---|---|
| `Titus` | `sparksql` | DuckDB | Full SQL support |
| `Titus` | `trino` | DuckDB | Identical local path |
| `NoOp` | — | Instant SUCCEEDED | For workflow structure tests |

---

## Run-instance lifecycle — exact Maestro status strings

Workflow (`WorkflowInstance.Status`):

| Status | Terminal | Meaning |
|---|---|---|
| `CREATED` | no | Instance created, not yet running |
| `IN_PROGRESS` | no | Executing |
| `PAUSED` | no | (not reached by toy) |
| `TIMED_OUT` | yes | Timed out |
| `STOPPED` | yes | Explicitly stopped |
| `FAILED` | yes | One or more steps failed |
| `SUCCEEDED` | yes | All steps succeeded |

Step (`StepInstance.Status`, full Maestro set reproduced in `workflow_models.py`):

Key terminal statuses the toy produces: `SUCCEEDED`, `USER_FAILED`,
`PLATFORM_FAILED`.  Non-terminal transient: `RUNNING`.  The full enum is kept
in code for fidelity; the toy only transitions through the above.

---

## API surface

Base URL: `http://localhost:8001` (separate port from knot's spec router).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/maestro/health` | Liveness check |
| `POST` | `/maestro/api/v3/workflows` | Register workflow definition |
| `GET` | `/maestro/api/v3/workflows/{workflow_id}` | Fetch workflow definition |
| `POST` | `/maestro/api/v3/workflows/{workflow_id}/instances` | Start a run |
| `GET` | `/maestro/api/v3/workflows/{workflow_id}/instances/{run_id}` | Run status |
| `GET` | `/maestro/api/v3/workflows/{workflow_id}/instances/{run_id}/steps` | Step statuses |

URL prefix `/maestro/api/v3/workflows` mirrors Maestro's
`@RequestMapping(value = "/api/v3/workflows")` from
`maestro-server/src/main/java/com/netflix/maestro/server/controllers/WorkflowController.java`.

---

## Where this fits in knot-as-compiler.md

`knot-as-compiler.md` describes three reference `Orchestrator` impls:

1. `ToySchedulerOrchestrator` — translates `WorkflowSpec` to calls against
   this toy service.
2. `MaestroOrchestrator` — translates to Netflix-internal Maestro.
3. `AirflowOrchestrator` — translates to Airflow DAG via REST.

**That `Orchestrator` interface and the `ToySchedulerOrchestrator` impl are
explicitly out of scope for this work.**  What exists here is the toy service
itself — the thing `ToySchedulerOrchestrator` would call.  The wiring from
knot's compile path to this toy's REST endpoints is a future modeling-router
task.

---

## Postgres tables

All tables use the `maestro_` prefix (no schema isolation needed; the prefix
is sufficient to avoid conflicts with `spec_revisions`):

- `maestro_workflows` — registered definitions (JSONB)
- `maestro_workflow_runs` — run instances (JSONB + denormalized status)
- `maestro_step_runs` — per-step status rows

Schema is applied idempotently at startup via `storage.apply_schema(dsn)`.
