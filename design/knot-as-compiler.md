# Knot is a compiler; Maestro is the runtime

**Status:** authoritative. This is the canonical statement of knot's relationship to workflow orchestrators and the layer at which it operates. Other design notes that conflict with this document are wrong.

**Date locked:** 2026-04-29.

---

## The statement

> Knot compiles. Maestro (or Airflow, or Argo, or the toy scheduler) runs.
>
> Knot owns the *spec* — the ontology, the sources, bound ER impls + their configs, bound DQ impls, the trust policy, bound materialization impls — all as versioned nodes (or impl registrations). When a run is requested, knot's compiler reads those nodes and emits a complete `WorkflowSpec` that the orchestrator turns into a native workflow.
>
> The orchestrator runs the workflow with its full retry / alert / monitor / scheduling machinery. Knot polls for completion and persists the run record.
>
> Teams customize knot's behavior by binding their own implementations to the DI seams — `ERProtocol` for entity resolution, the `QueryReader` / `Materializer` / `Introspector` / `ViewManager` protocols for lake execution (per `staging/query-executor.md`), free-form materialization impls per target, optional `DqRunner` for custom data-quality, and `Translator` for consumer query expansion. The compiler emits tasks that call into those interfaces; the bindings determine what actually executes inside each task. (Source ingestion is *not* one of these — see [`source-layer-contract.md`](source-layer-contract.md): teams put rows in the lake themselves; knot's workflows start at normalize.)

Knot does not run jobs. There is no in-process orchestrator. There is no "knot-also-executes" code path. If a developer is tempted to add one, the architectural seam has been violated.

## The flow

1. Curator authors the spec — `Movie` ontology class, `imdb_movies` source schema, `movie_blocking_jaro_v3` ER strategy, DQ checks, materialization target. Every node is a versioned Pydantic spec stored in postgres-control.
2. Merge is triggered for `Movie` (manually, by event, by schedule).
3. **Compiler** reads the active ontology + sources + strategy + DQ + target nodes. Emits a `WorkflowSpec` — a DAG of tasks: ingest-per-source → normalize-per-source → resolve → merge → validate → publish. Each task carries: a reference to the registered handler in the orchestrator, pinned revisions of every spec node that contributed, audit metadata (knot run id, who requested, when).
4. `Orchestrator.submit_workflow(spec)` ships the spec to the configured runtime — Maestro / Airflow / Argo / toy. The runtime translates it to its native workflow format and runs it.
5. Each task in the running workflow calls back into one of the bound DI interfaces. The bindings determine the impl: an ER task calls the bound `ERProtocol` impl with its declared DataContexts materialized; a materialization task calls the bound materialization impl; a `QueryReader`-backed validation task runs knot-emitted validation SQL. Knot doesn't care which backend the binding uses internally — Spark, Trino, DuckDB, etc.
6. Knot polls the orchestrator for completion via `Orchestrator.status(run_handle)`. Writes a `pipeline_runs` row referencing the orchestrator's run id.
7. Audit walk-back: any resolved fact traces to its winning source, the ER strategy revision active at compile time, the orchestrator run that produced it, and the corrections that participated. Mechanical. Deterministic.

## Why this is the right model

It eliminates the "is knot duplicating Maestro" question structurally. They operate at different layers, the same way these well-established patterns do:

| Spec layer | Compiles to | Runtime |
|---|---|---|
| **dbt** models + refs | dbt's compiled DAG | Airflow / dbt-cloud / Snowflake |
| **Kubeflow Pipelines** Python | Argo Workflows YAML | Argo |
| **Apache Beam** code | Translated job graph | Dataflow / Spark / Flink |
| **Terraform** HCL | Execution plan | Provider APIs |
| **knot** ontology + strategy nodes | `WorkflowSpec` | Maestro / Airflow / Argo |

The dbt:Airflow analogy is closest. Nobody asks "why use dbt, isn't Airflow already a scheduler" — dbt owns model semantics + lineage at the column level; Airflow owns scheduling/retries/alerts. Knot:Maestro is the same shape: knot owns ontology + per-fact provenance + trust resolution + corrections audit; Maestro owns workflow execution.

## The "why use knot AND Maestro" answer

Maestro is an *execution* layer. Knot is a *semantic* layer. Both are needed. What Maestro doesn't own:

- **The ontology.** Maestro doesn't know what a `Movie` is or what properties it has. It runs jobs that produce datasets; it doesn't model the domain.
- **Per-fact provenance.** Maestro tracks "this run produced this dataset." Knot tracks "this property's value came from this source's row at trust 0.85, under ER decision DR-7841 from strategy revision v3.2." Order-of-magnitude finer.
- **Trust-weighted resolution as first-class.** Maestro has retry policies; it doesn't have "imdb says 1991, tmdb says 1972, current trust resolves to 1972."
- **Corrections as a versioned artifact AND a high-trust source.** A correction is a versioned node + a fact contributed under `_user_corrections` at trust 0.95. Maestro has no concept of "human override participating in the data pipeline as a source of record."
- **The audit walk-back.** "Why does this graph say X?" walks resolved value → winning source → trust at moment → ER revision → contributing corrections. Mechanical from knot. Manual from Maestro alone.
- **JSON Schema export for LLM extraction.** The same ontology compiles to JSON Schema for structured-output APIs. Doesn't exist in Maestro.
- **Multi-language query translator.** Consumers query the ontology; knot translates against current ontology + materialization layout. Without this, every consumer learns the lake's tables.

The pithy version: *Maestro is what runs the work. Knot is what knows whether the work was correct, who contributed, who disagreed, who won, and how to walk back from a wrong answer.*

## What this means for the codebase

### The compiler

`server/compiler/` produces `WorkflowSpec` from the active ontology + class config + active strategy/dq/target. This is the load-bearing piece that makes knot a compiler. Pure transformation: spec nodes in, `WorkflowSpec` out. No execution, no I/O beyond reading the spec nodes.

### The `WorkflowSpec` type

A Pydantic type — list of `Task` objects with `{name, handler_ref, inputs, outputs, depends_on, retry_policy_hints, audit_metadata}`. Orchestrator-agnostic. Each `Orchestrator` impl translates `WorkflowSpec` to its native format.

### The `Orchestrator` interface, two methods

- `submit_workflow(WorkflowSpec) -> RunHandle` — for compiled DAGs (the primary path).
- `submit_job(JobSpec) -> RunHandle` — for one-off atomic jobs (e.g., a DQ check on demand, an ad-hoc query). Still useful, narrower surface.

### Three reference impls

- `ToySchedulerOrchestrator` — translates `WorkflowSpec` to a sequence of `POST /jobs` against the local scheduler service. Local dev / CI.
- `MaestroOrchestrator` — translates to Netflix-internal Maestro workflow. Production.
- `AirflowOrchestrator` — translates to Airflow DAG via REST API. General OSS swap target.

### The runner

Each orchestrator runs tasks somewhere — a container, a pod, a notebook, a Spark driver. Whatever the orchestrator gives it, the task does one thing: look up the bound interface impl from knot's DI registry and call its method. That entrypoint is knot's runner.

The runner is small on purpose. Reads `handler_ref` and `params` from its arguments, asks `REGISTRY.get_<interface>()`, calls the method, exits. Same DI bindings as the long-running server — same code, different entrypoint.

What the runner does **not** do: manage env vars, fetch secrets, configure networking, propagate credentials. None of that is knot's concern. If a team's bound impl needs database credentials or a Kafka cluster URL, the impl reads them from its deployment environment when it's constructed. Knot's API to the team is the bound DI interfaces; the team's API to its environment is whatever they've set up in their deployment.

### Customization model

Teams customize by binding their own implementations to the DI interfaces. An `ERProtocol` impl could be fuzzy-title-matching, embedding-based, ML-trained, or custom; a materialization impl could write to Neo4j, Iceberg, parquet, vector store, or all of them. The compiler emits tasks that call the bound impl with its declared DataContexts materialized; the binding determines the runtime. Knot's compiler doesn't change when the team's stack changes.

This is the dependency-injection pattern proper: the bound DI interfaces are the seams where customization happens. Adding a new ER algorithm = new `ERProtocol` impl + bind it; no compiler change. (See `staging/seam-contract-pattern.md` and `staging/di-input-contract.md`.)

### The local-dev experience

`compose up` brings postgres-control + the toy scheduler with `/lake` parquet directory. Knot compiles a workflow. `ToySchedulerOrchestrator` translates the DAG into a sequence of `POST /jobs`. Toy scheduler runs each job (DuckDB-backed SQL); parquet shows up in `/lake`. Identical control flow to the production path; only the `Orchestrator` binding changes.

## The audit story across the boundary

Every orchestrator run record is paired with `(knot_compile_id, ontology_revision, strategy_revision, sources_at_compile_time, target_revision)`. Knot's `pipeline_runs` row references the orchestrator run id. Walking back from any resolved fact:

1. Resolved fact → which run produced it (`pipeline_runs` row)
2. Run record → orchestrator run id + compile id
3. Compile id → the `WorkflowSpec` and the spec-node revisions that went into it
4. Spec-node revisions → the actual specs that were active at compile time
5. From there: which sources were enabled, which strategy was active, which corrections were participating, what trust scores looked like

The boundary is clean because knot only sees the compiled spec + completion status. Everything in between is the orchestrator's runtime — opaque to knot, audited by knot at the granularity that matters (compile time + completion).

## Architectural rules that follow from this

1. **Knot core code never executes work.** It dispatches. If you find yourself writing a SQL execution loop in `server/api/`, you are violating this rule. Move the execution into the orchestrator (a registered handler) or into the data-plane impl behind one of the bound DI interfaces.
2. **The compiler is pure.** It reads spec nodes, emits `WorkflowSpec`. No side effects. Testable as `compile(spec_graph) -> WorkflowSpec`.
3. **`Orchestrator` impls are translators.** Their job is to turn `WorkflowSpec` into native workflow format and surface status back. They do not contain business logic.
4. **The bound DI interfaces are the only customization seams.** A team that needs new behavior implements a new impl of one (or extends an existing one) and binds it. They do not fork knot's compiler. They do not add a new interface lightly.
5. **Tasks call back into knot's interfaces, not into knot's HTTP API.** When the orchestrator runs an ER task, it calls the bound `ERProtocol` impl directly with its declared DataContexts materialized, not by hitting `POST /resolve/{class}` on knot's API. The HTTP API is for *triggering* runs and *querying* state; the task runtime is in-process to the runner.

## What this rules out

- An "in-process orchestrator" — there isn't one. Knot dispatches; the toy scheduler runs locally, the real orchestrator runs in prod. Same code path.
- A "knot worker" daemon that picks up jobs from a knot-internal queue. Knot has no internal queue. Maestro's queue is the queue.
- Custom orchestration logic inside `server/api/` handlers (the current `api/merge.py` is the canonical violation — it loops, executes SQL, writes resolved_facts inline). All of that moves into the compiled merge task: knot generates the merge SQL from the spec; the bound `Materializer` (per `staging/query-executor.md`) executes it. No bound merge impl required — merge is knot-internal logic that runs through the QueryExecutor protocols.
- Per-customer forks of knot. Customization happens at the binding layer — same knot, different bindings.

## See also

- `legacy/architecture-boundary-spec-vs-data-plane.md` — earlier draft of the boundary spec. **Note:** that doc lists eight interfaces including `SourceReader`. This document and [`source-layer-contract.md`](source-layer-contract.md) supersede it: `SourceReader` is dropped because sources are team-owned lake declarations, not knot-runtime objects. Seven interfaces, not eight. The legacy doc otherwise still describes the seam pattern accurately.
