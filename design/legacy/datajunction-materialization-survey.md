---
title: DataJunction materialization survey
status: note
tags: [research, datajunction, reference, materialization]
project: knot
created_at: 2026-04-27T22:45:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# DataJunction materialization survey

Reference clone at `~/code/knot/refs/datajunction/`. Survey informs
Knot's materialization architecture.

All paths below are absolute relative to that clone unless stated. Line
numbers are from the current HEAD of the clone (already cloned, not
re-fetched).

## Overview

DJ separates "logical node definition" from "physical bytes" and
delegates almost all execution to an external **Query Service**
(`datajunction-query`, a.k.a. DJQS) over HTTP. The DJ server itself
never runs SQL. It builds a SQL string, packages it into a
`MaterializationInput` Pydantic model, POSTs it to DJQS, and DJQS
schedules workflows in some external scheduler (the OSS reference
implementation runs queries directly via duckdb / snowflake-connector /
bigquery / sqlalchemy; the Netflix-internal scheduler/orchestrator is
*not* in this repo and is referred to abstractly as "the query service
manages all reruns of this job").

Concretely the materialization subsystem has three concentric rings:

1. **Server side (datajunction-server)**: model, validate, build SQL,
   compute partition predicates, persist `Materialization` row, then
   call DJQS. Lives under `datajunction_server/materialization/`,
   `datajunction_server/internal/materializations.py`,
   `datajunction_server/api/materializations.py`,
   `datajunction_server/construction/`, and
   `datajunction_server/sql/parsing/`.
2. **Query service (datajunction-query / DJQS)**: receives SQL +
   workflow spec, executes / schedules / runs against a backend engine
   (Spark, Trino, Druid, Postgres, Snowflake, BigQuery, DuckDB).
3. **Reflection (datajunction-reflection)**: a Celery worker that
   periodically re-introspects source tables and POSTs availability
   state back to DJ core
   (`datajunction-reflection/datajunction_reflection/worker/tasks.py:32-83`).

There is **no in-process executor**. The server is a SQL-spec-and-dispatch
plane; physical execution is fully out-of-process.

## Pipeline stages

Stages from "user defines a node" to "rows exist in backend":

1. **Node revision exists.** A node is created via `/nodes/...`
   endpoints (out of scope here). The transform/dimension/cube node
   carries a SQL string and partition column metadata.
2. **User upserts a materialization config.**
   `POST /nodes/{node_name}/materialization/` →
   `upsert_materialization()` at
   `datajunction-server/datajunction_server/api/materializations.py:90-277`.
   Body is one of `UpsertMaterialization` /
   `UpsertCubeMaterialization` discriminated by `job` field
   (`models/materialization.py:493-533`,
   `models/cube_materialization.py:198-236`). Validation enforces e.g.
   `INCREMENTAL_TIME` requires a temporal partition column
   (`api/materializations.py:126-132`).
3. **Build materialization SQL (server).**
   `create_new_materialization()` at
   `internal/materializations.py:237-303` dispatches by node type:
   - Transform/dimension → `build_non_cube_materialization_config()`
     (`internal/materializations.py:200-234`) which constructs an
     `ast.Query` via the v2 builder
     (`construction/build_v2.py:254-462`, `QueryBuilder.build()`).
   - Cube + `DRUID_CUBE` job → `build_cube_materialization()`
     (`internal/cube_materializations.py:155-293`) which produces a
     `DruidCubeConfig` with measures-materializations and a combiner
     query.
   - Cube + legacy Druid job → `build_cube_materialization_config()`
     (`internal/materializations.py:103-197`).
4. **Persist Materialization row.** A `Materialization` ORM record is
   saved on the current `NodeRevision`, holding `name`, `schedule`
   (cron string, default `@daily`), `strategy`, `job` (class name of the
   `MaterializationJob` subclass), and a JSON `config` blob
   (`database/materialization.py:34-101`).
5. **Schedule the job (server-side dispatch).**
   `schedule_materialization_jobs()` at
   `internal/materializations.py:306-336` looks up the
   `MaterializationJob` subclass by `cls.__name__` and calls
   `clazz().schedule(materialization, query_service_client)`. The
   subclass:
   - Re-parses the stored SQL into AST, splices in temporal +
     categorical partition predicates derived from
     `DJ_LOGICAL_TIMESTAMP()` (see
     `materialization/jobs/materialization_job.py:75-187` for
     `SparkSqlMaterializationJob.schedule()`, and
     `materialization/jobs/cube_materialization.py:166-250` for
     `build_materialization_query()`).
   - Builds a `GenericMaterializationInput` /
     `DruidMaterializationInput` / `DruidCubeMaterializationInput`
     payload.
   - Calls `query_service_client.materialize(...)` /
     `materialize_cube(...)` /
     `materialize_cube_v2(...)` (`service_clients.py:306-410`).
6. **Query service receives spec + SQL.** DJQS `materialize` endpoint
   ingests the input and is "expected to manage all reruns of this
   job" (docstring at `service_clients.py:314-318`). In the OSS DJQS
   the actual execution lives in
   `datajunction-query/djqs/engine.py:73-156` (`run_query` dispatching
   to duckdb / snowflake / bigquery / sqlalchemy). The OSS DJQS does
   *not* implement scheduled materialization itself — that's where
   Netflix-internal infrastructure plugs in.
7. **Bytes land.** Either Spark/Druid/Trino writes the output table, or
   a view is created via `query_service_client.create_view()`
   (`internal/views.py:154-201`, `service_clients.py:195+`).
8. **Availability writeback.** Whoever ran the job (or the reflection
   worker) POSTs to `POST /data/{node_name}/availability/`
   (`api/data.py:66-185`), which writes an `AvailabilityState` row
   keyed to the node revision
   (`database/availabilitystate.py:16-89`). This is the marker that
   "rows exist."
9. **Reads use the materialized table.**
   `get_table_for_node()` at
   `construction/build_v2.py:2260-2310` swaps the logical SQL for
   `SELECT ... FROM {availability.schema_}.{availability.table}` when a
   downstream query references the node *and* the availability state
   is usable.

Stage boundaries are HTTP boundaries between (server → DJQS) and
(DJQS or scheduler → server `/data/.../availability/`). The handoff
from "build SQL" to "schedule" is the single Python call
`MaterializationJob.schedule()`; everything before that is in-process
SQLAlchemy + AST work, everything after is HTTP.

## SQL generation

There are **two libraries** in play, used at different points:

- **ANTLR4 + a hand-rolled AST** for parsing, manipulating, and
  re-rendering canonical (Spark) SQL. This is the primary mechanism.
  - Grammar / parser: `sql/parsing/backends/grammar/` (generated
    SqlBaseLexer / SqlBaseParser, very Spark-flavored).
  - Backend: `sql/parsing/backends/antlr4.py` (1314 lines, including
    `parse()`, `build_parser()`, `UpperCaseCharStream`, error
    listeners).
  - AST: `sql/parsing/ast.py` (3570 lines). Hand-written dataclasses
    for `Query`, `Select`, `Column`, `Table`, `BinaryOp`, `Function`,
    `Cast`, `Join`, `From`, `Relation`, etc. Includes a
    dialect-rendering context var:
    `_render_dialect: ContextVar`, `render_for_dialect()`,
    `to_sql(query, dialect)` (`ast.py:90-138`). Default rendering is
    "canonical / Spark."
  - This AST is what every materialization job manipulates. Examples:
    `SparkSqlMaterializationJob.schedule()` parses the stored query,
    walks `query_ast.select.projection` for the temporal partition
    column, builds `ast.BinaryOp(...)` or `ast.Between(...)` and
    rewrites `final_query.select.where`
    (`materialization/jobs/materialization_job.py:88-162`).
- **sqlglot for cross-dialect transpilation** — applied as a *final*
  pass on a string that was already rendered from the AST.
  - Plugin layer: `transpilation.py:16-130`. `SQLTranspilationPlugin`
    is the base; `SQLGlotTranspilationPlugin` (`transpilation.py:66-98`)
    is registered for every supported dialect via the
    `@dialect_plugin(...)` decorator stack. Configured via
    `settings.transpilation_plugins` (`config.py:170` defaults to
    `["default", "sqlglot"]`).
  - Entry point: `transpile_sql(sql, dialect)` at
    `transpilation.py:101-130`. Always reads as Spark, writes to the
    target dialect.
  - Real usage: `internal/views.py:145` transpiles a Spark view body
    to Trino before submitting CREATE OR REPLACE VIEW. `api/cubes.py`
    documents transpiling via sqlglot for Preset/Trino views.
- **Note: there is no per-backend hand-written SQL generator.** Druid
  ingestion is handled via a JSON spec
  (`DruidMeasuresCubeConfig.build_druid_spec()` at
  `models/materialization.py:325-397`), not via Druid SQL. The Druid
  spec includes `dataSchema`, `parser.parseSpec` (parquet),
  `metricsSpec` keyed off `DRUID_AGG_MAPPING`
  (`models/materialization.py:30-50`), `granularitySpec`,
  `tuningConfig.partitionsSpec` ("hashed", `targetPartitionSize`
  5_000_000).

In short: **DJ stores Spark-canonical SQL, manipulates it as a
hand-rolled AST, and lets sqlglot translate the final string to a
target dialect when rendering for non-Spark backends.**

## Backend abstraction

Two related abstractions to disentangle:

1. **Dialect abstraction** (rendering side):
   - `Dialect` enum at `models/dialect.py:16-31`: `SPARK`, `TRINO`,
     `DRUID`, `POSTGRES`, `CLICKHOUSE`, `DUCKDB`, `REDSHIFT`,
     `SNOWFLAKE`, `SQLITE`, `BIGQUERY`. Plus dynamic registration via
     `_missing_()` and `DialectRegistry`
     (`models/dialect.py:33-94`).
   - Plugin model: `SQLTranspilationPlugin` base
     (`transpilation.py:16-53`) with `transpile_sql(query,
     input_dialect, output_dialect) -> str`. SQLGlot is the only
     non-trivial implementation today (`transpilation.py:66-98`).
   - Render-time dialect: `Function.__str__` and other AST nodes
     consult `_render_dialect` ContextVar (`ast.py:97-138`) so that the
     same AST renders different dialect-specific function names
     without re-walking.

2. **Query Service Client abstraction** (execution side):
   - ABC: `BaseQueryServiceClient` at `query_clients/base.py:31-383`.
     Every method (`get_columns_for_table`, `submit_query`,
     `materialize`, `materialize_cube`, `materialize_preagg`,
     `run_backfill`, `deactivate_materialization`,
     `get_materialization_info`, `create_view`,
     `refresh_cube_materialization`, etc.) defaults to
     `NotImplementedError` — subclasses cherry-pick what they support.
   - Implementations:
     - `query_clients/http.py`: HTTP delegator (the production-y one,
       targets DJQS).
     - `query_clients/snowflake.py`: direct snowflake-connector-python
       — only column introspection + `submit_query`. No materialization.
     - `query_clients/bigquery.py`: same shape for BigQuery.
   - The "fat" canonical client used by the server is
     `QueryServiceClient` in `service_clients.py` (802 lines). It is
     not a subclass of `BaseQueryServiceClient` — it's a separate
     concrete HTTP client used directly via DI
     (`utils.get_query_service_client`). The ABC is more recent and
     covers a "bring-your-own" client surface.

   The "Trino vs Spark vs Druid" difference, on the *execution* side,
   is opaque to the server: the server posts a SQL string + workflow
   spec to DJQS and DJQS picks the engine (e.g. via
   `EngineType.DUCKDB | SNOWFLAKE | BIGQUERY` switch in
   `datajunction-query/djqs/engine.py:101-139`). On the *rendering*
   side dialect choice happens via the AST `render_for_dialect()` /
   sqlglot transpile pass.

3. **Engines as data**: there's also a database-level `Engine` /
   `Catalog` concept (`database/engine.py`,
   `database/catalog.py`) tying nodes to one of these execution
   backends, but it's metadata, not behavior.

## Materialization strategies

Defined as `MaterializationStrategy` enum at
`models/materialization.py:56-87`:

| Strategy | Meaning | Availability shape |
|---|---|---|
| `FULL` | Replace target dataset in full each run | Single table |
| `SNAPSHOT` | New snapshot table per run, DJ tracks which is current | Multiple tables, one per snapshot |
| `SNAPSHOT_PARTITION` | Append a snapshot-stamped partition each run | Single table with snapshot column |
| `INCREMENTAL_TIME` | Process last N temporal partitions (`lookback_window`) | Single table |
| `VIEW` | Don't materialize — create a database view | Single view |

The user picks via the `strategy` field on the
`UpsertMaterialization` body (`models/materialization.py:512`).
`INCREMENTAL_TIME` is gated on a temporal partition column
(`api/materializations.py:126-132`). For cubes,
`UpsertCubeMaterialization` defaults to `INCREMENTAL_TIME` and only
allows `FULL` or `INCREMENTAL_TIME`
(`models/cube_materialization.py:206-210`).

The available **job types** (separate axis from strategy) are in
`MaterializationJobTypeEnum` at
`models/materialization.py:438-490`:

- `SPARK_SQL` — `SparkSqlMaterializationJob`, allowed for
  `TRANSFORM`, `DIMENSION`, `CUBE`.
- `DRUID_MEASURES_CUBE` — pre-agg cube to Druid (cube only).
- `DRUID_METRICS_CUBE` — post-agg cube to Druid (cube only).
- `DRUID_CUBE` — newer unified cube job, "will replace the other cube
  materialization types."

Job class is selected via `cls.__name__` lookup at
`internal/materializations.py:324-326` and
`api/materializations.py:490-492`, which uses
`MaterializationJob.__subclasses__()` — i.e. the registry is *implicit
Python class hierarchy*, not a registered plugin map. This is brittle
(only works after the module has been imported) and worth flagging.

There's also a parallel **pre-aggregation** materialization concept
(`api/preaggregations.py`, `models/preaggregation.py`) for cube
pre-aggs with explicit grain-group hashing
(`compute_grain_group_hash`, `compute_preagg_hash`) and a separate
`/preaggs/materialize` workflow path
(`service_clients.py:412-452`). It supports two flows
(documented in the file header): "DJ-managed" (DJ calls DJQS) and
"User-managed" (user materializes externally, then POSTs availability
back).

Streaming is **not** a first-class strategy. There is no Flink / Kafka
integration in this repo.

## Trigger model

Three orthogonal triggers exist:

1. **Cron schedule (the default).** Each `Materialization` row carries
   a `schedule` string (cron, defaulting to `@daily` — see
   `internal/materializations.py:300`). The scheduling itself happens
   in DJQS / external scheduler — DJ just hands the cron string over.
   On every server-side upsert, DJ re-calls
   `query_service_client.materialize(...)` to register/update the
   workflow (`api/materializations.py:185-205`, line 261-267 for
   the new-config branch). DJ does **not** itself run a cron; the
   cron lives downstream.
2. **Explicit user action.**
   - Backfill: `POST /nodes/{node}/materializations/{name}/backfill`
     at `api/materializations.py:428-532` →
     `MaterializationJob.run_backfill()` →
     `query_service_client.run_backfill()`
     (`service_clients.py:768-802`). User supplies
     `List[PartitionBackfill]`.
   - Refresh / restore: re-upsert with the same config triggers
     `schedule_materialization_jobs()` again to "Refresh
     materialization workflows"
     (`api/materializations.py:181-191`); a deactivated config can be
     reactivated by upserting it again.
   - Cube refresh-materialization endpoint
     (`service_clients.py:664-730`) recreates workflows without
     bumping the cube version.
3. **Reflection refresh (source-only).** A Celery beat in
   `datajunction-reflection` periodically lists source nodes and posts
   refresh calls
   (`datajunction-reflection/datajunction_reflection/worker/tasks.py:32-83`).
   The TODO at line 81-82 explicitly says it does *not* yet post
   actual availability state — so today this triggers schema reflection
   only, not materialization.

**Staleness decision.** There is no first-class "is this stale?" check
in the server. Instead:

- Each `AvailabilityState` carries `valid_through_ts`,
  `min_temporal_partition`, `max_temporal_partition`, `partitions[]`,
  and `updated_at` (`database/availabilitystate.py:31-66`).
- `AvailabilityState.is_available(criteria)` (line 80-89) is a stub
  that returns `True` with a `TODO` comment — staleness logic is
  deferred. The intent is that `BuildCriteria` (`models/node.py`)
  would carry a target time and `is_available` would check
  `valid_through_ts >= target`. Currently, if availability exists,
  it's used.
- "When to re-run" is therefore entirely the scheduler's job (cron),
  with explicit backfills as the escape hatch.

## Idempotency / retries

Server-side mostly relies on naming + the JSON config blob:

- **Materialization name is deterministic.** Built from job name +
  strategy + temporal partition + categorical partitions
  (`internal/materializations.py:290-295`):
  ```
  {job}__{strategy}__{temporal_partition}__{categorical_partitions...}
  ```
  Re-upserting with the same shape resolves to the same `Materialization`
  row (uniqueness enforced by
  `UniqueConstraint("name", "node_revision_id")` at
  `database/materialization.py:40-46`).
- **Config-equality short-circuit.** On upsert, if an existing
  materialization with the same name has identical config, the server
  skips persistence and only re-schedules
  (`api/materializations.py:144-205`). If the existing one was
  deactivated, it's restored. This makes the upsert idempotent.
- **No server-side checkpointing of in-flight runs.** The server has
  no notion of "this run is mid-write." Once
  `query_service_client.materialize()` returns, the server is done.
  Crash recovery is the scheduler's problem (e.g. Workflow IDs in the
  `MaterializationInfo.workflow_names` field at
  `models/materialization.py:128).
- **Deterministic output table names for cubes.**
  `MeasuresMaterialization.output_table_name` is a sha256 hash of
  `(node, version, timestamp_column, sorted grain, sorted dims, sorted
  measures)` (`models/cube_materialization.py:101-118`). Same logical
  spec → same table → idempotent overwrite. Same pattern for
  `CombineMaterialization.output_table_name` (line 274-291).
- **Pre-agg hashing** uses
  `compute_grain_group_hash` / `compute_expression_hash` /
  `compute_preagg_hash` (`api/preaggregations.py:38-43`,
  `database/preaggregation.py`) — same idea, scoped to pre-aggs.
- **Backfills append.** `run_materialization_backfill()` always
  creates a new `Backfill` row with the spec and URLs
  (`api/materializations.py:505-513`). No de-dup. Each backfill is its
  own audit record.
- **`DJ_LOGICAL_TIMESTAMP()` is the idempotency seed.** All temporal
  predicates derive from it
  (`database/partition.py:73-108`,
  `materialization/jobs/materialization_job.py:104-126`,
  `internal/cube_materializations.py:25-51`). Re-running the same
  workflow at the same logical timestamp produces the same SQL → same
  partition gets overwritten. This is the intended idempotency
  contract.

So idempotency is structural (deterministic names + the same
predicates) plus delegation: DJ trusts the scheduler/Spark/Druid
ingestion to be idempotent within a logical-timestamp window.

## Failure surface

DJ surfaces failures via four channels:

1. **HTTP responses on upsert.** Validation errors raise
   `DJInvalidInputException` → 400 (e.g.
   `api/materializations.py:111-114, 126-132`). Build errors raise
   `DJInvalidInputException` from
   `internal/materializations.py:187-197`.
2. **Logged-and-swallowed scheduling failures.**
   `QueryServiceClient.materialize()` and friends log via
   `_logger.exception(...)` and return an *empty*
   `MaterializationInfo(urls=[], output_tables=[])` on non-2xx
   (`service_clients.py:324-337`,
   `service_clients.py:355-369`,
   `service_clients.py:646-662`). The server-side upsert path then
   reports success to the user even though the downstream registration
   failed — the only signal is the empty `urls` array. (See `# pragma:
   no cover` markers for these branches; this is intentional.) The
   newer pre-agg path is stricter — it raises (`service_clients.py:402,
   442, 482, 522`).
3. **`History` events.** Every materialization create / update /
   delete / restore writes a `History` row
   (`api/materializations.py:160-171, 237-254, 405-416`). Backfills
   too (line 515-530). This is what the UI's "Activity" tab reads.
4. **`Backfill` rows + workflow URLs.**
   `MaterializationInfo.urls` and `MaterializationInfo.workflow_names`
   are persisted on the `Backfill` row
   (`database/backfill.py`,
   `api/materializations.py:505-513`) so the UI can deep-link to the
   external workflow runner. The actual run status (RUNNING / FAILED /
   SUCCEEDED) lives in the external scheduler — DJ has no
   `run_status` column on `Materialization` or `Backfill`.

The UI hits `GET /nodes/{node}/materializations/`
(`api/materializations.py:280-322`) which returns
`MaterializationConfigInfoUnified` (config + URLs + backfills). This
is the primary failure-visibility surface for end users; see
`datajunction-ui/src/app/services/DJService.js:1226, 1934, 2001` and
`NodePage/NodeHistory.jsx:164`.

For pre-aggs there's a richer status model:
`WorkflowStatus` (`models/preaggregation.py:20`),
`WorkflowResponse.workflow_url` (line 331-334), and labeled
`workflow_urls` (line 160). This is closer to the right shape for
operational visibility but is pre-agg-only.

**Bottom line:** failure visibility on the *legacy* materialization path
is weak (logs + empty MaterializationInfo + activity history). The
*newer* pre-agg path raises and carries WorkflowStatus. Knot should
adopt the pre-agg pattern wholesale, not the legacy one.

## What's directly applicable to Knot

- **Build-vs-execute split is correct.** Knot should keep all SQL
  generation server-side and delegate execution to a query service
  shim. `BaseQueryServiceClient` (`query_clients/base.py`) is a clean
  ABC to copy.
- **`MaterializationStrategy` enum is a good starting taxonomy** —
  `FULL`, `SNAPSHOT`, `SNAPSHOT_PARTITION`, `INCREMENTAL_TIME`, `VIEW`.
  Their docstrings (`models/materialization.py:56-87`) usefully name
  the resulting *availability shape*, which is more useful than naming
  the strategy abstractly.
- **Deterministic materialization name + config-equality
  short-circuit on upsert** (`internal/materializations.py:290-295`,
  `api/materializations.py:144-205`) is a clean idempotency pattern.
- **`DJ_LOGICAL_TIMESTAMP()` substitution** as the canonical "this
  run's processing time" handle (`database/partition.py:80-108`).
  Lets the same SQL re-run idempotently for any window. Steal this.
- **Deterministic content-hashed output table names**
  (`models/cube_materialization.py:101-118` and the analogous
  `CombineMaterialization`). Lets you regenerate without coordination,
  and aligns with content-addressed thinking.
- **Hand-rolled AST + dialect ContextVar** is overkill if we're
  greenfield, BUT the pattern of "render canonical, transpile last
  via sqlglot" (`transpilation.py:101-130`,
  `internal/views.py:145`) is the right separation of concerns.
- **Availability state as a separate first-class entity**
  (`database/availabilitystate.py`) with `valid_through_ts`,
  `min/max_temporal_partition`, `partitions[]`, `total_size_bytes`,
  `total_row_count`, `ttl_days`. This is the right schema for "what
  exists where, how fresh, how big."
- **Backfills as append-only history**
  (`api/materializations.py:505-513`,
  `database/backfill.py`). Every backfill leaves a record with its
  partition spec and workflow URLs.

## What we'd do differently

- **Materialization job dispatch via `__subclasses__()` is fragile**
  (`internal/materializations.py:324-326`,
  `api/materializations.py:490-492`). Use an explicit registry / entry
  points / decorator. A subclass that isn't imported won't be
  discovered.
- **Failure swallowing in `QueryServiceClient.materialize()`**
  (`service_clients.py:324-337` and friends) — empty
  `MaterializationInfo` returned silently. Knot should adopt the
  pre-agg pattern (`service_clients.py:402, 442`) which raises on
  non-2xx, and carry `WorkflowStatus` on the response.
- **`AvailabilityState.is_available()` is a stub** that returns True
  always (`database/availabilitystate.py:80-89`). Knot should
  implement actual staleness comparison
  (`valid_through_ts >= target_ts`, partition coverage, etc.) from day
  one — staleness is the whole point of incremental.
- **No streaming materialization.** If Knot needs streaming
  (Flink/Kafka), this is greenfield. The job-class registry is the
  natural extension point but the
  `GenericMaterializationInput`/`DruidMaterializationInput` schema
  assumes batch (`schedule` is a cron string, no event-source
  abstraction).
- **`MaterializationStrategy` mixes "what gets produced" with "how often."**
  `FULL` and `INCREMENTAL_TIME` describe write semantics;
  `SNAPSHOT_PARTITION` describes table layout. Knot should split
  these into `(write_mode, table_layout, refresh_cadence)`.
- **No `run_status` field on `Materialization` or `Backfill`.** The
  external scheduler's URL is the only operational surface. Knot
  should either subscribe to scheduler webhooks and update a status
  column, or stand up its own polling and project a status into the
  DJ-equivalent DB.
- **Two parallel materialization paths** (legacy
  `materialize/materialize_cube` vs newer
  `materialize_cube_v2/materialize_preagg`) coexist with overlapping
  semantics. The pre-agg model
  (`api/preaggregations.py`,
  `models/preaggregation.py`) is clearly the more thought-through
  successor (`grain_group_hash`, `WorkflowStatus`, dual-flow doc at
  `api/preaggregations.py:1-15`). Knot should pick one model.
- **DJ owns SQL building but not workflow shape.** The query service
  defines what a "workflow" is, including its naming, retry policy,
  and recovery. That made sense for Netflix-internal — likely doesn't
  for Knot if we want a single self-contained system. Decide early.
- **`@daily` default schedule** (`internal/materializations.py:300`)
  is a footgun. Make schedule explicit; don't default it.

## Open questions / gaps in the survey

- **What does the Netflix-internal scheduler actually look like?** The
  OSS DJQS doesn't implement scheduled materialization. The contract
  is "POST /materialization/, we'll handle reruns." Without seeing
  Netflix's scheduler we can't answer "how does retry work in
  practice", "what's the workflow ID space," or "how does the
  callback-to-availability-state happen." The reflection worker hints
  at the shape (`reflection/worker/tasks.py`) but its TODO at line
  81-82 confirms the loop isn't closed in OSS.
- **`MaterializationJob.__subclasses__()` discovery — when does it
  break?** Need to verify whether the import side-effects in
  `materialization/jobs/__init__.py:12-20` are sufficient at runtime
  or whether there are scenarios where a job class isn't registered.
- **`materialization/jobs/job_types.py` is empty** (0 bytes). Looks
  like a planned-but-unfinished module. The codename matches the
  `MaterializationJobTypeEnum` from `models/materialization.py` so
  this might be in-progress refactor toward an explicit registry.
- **`build_v3` vs `build_v2` overlap.**
  `construction/build_v3/builder.py` (558 lines) and
  `construction/build_v3/materialization.py` (192 lines) appear to be
  a newer SQL-build path used by `api/data.py` and `api/views.py`,
  while `construction/build_v2.py` (2310 lines) is still used by
  `internal/materializations.py:217` for non-cube nodes. Worth a
  follow-up to decide which is "the future."
- **Streaming.** Confirmed *not* present in OSS. Whether Netflix
  internal has a `KafkaSourceMaterializationJob`-shaped subclass is
  unknown from this clone.
- **Trino as a write target.** Trino appears in `Dialect`,
  `DialectRegistry`, view DDL submission, and sqlglot transpilation,
  but no `TrinoMaterializationJob` exists. View creation
  (`internal/views.py`) is the only Trino-write path. Confirm whether
  Netflix actually materializes *into* Trino or only reads from it.
- **Partition predicate generation** is duplicated across
  `materialization/jobs/materialization_job.py:99-162`,
  `materialization/jobs/cube_materialization.py:187-237`, and
  `internal/cube_materializations.py:25-51`. Each constructs a
  `temporal_op` slightly differently. Knot should unify.
- **The OSS DJQS executes synchronously via duckdb / snowflake /
  bigquery / sqlalchemy** (`datajunction-query/djqs/engine.py:73-156`)
  with no scheduler. So the "schedule" path tested in OSS is probably
  fictional — the unit tests likely mock `QueryServiceClient`. Worth
  spot-checking the test suite to see what gets exercised end-to-end.
