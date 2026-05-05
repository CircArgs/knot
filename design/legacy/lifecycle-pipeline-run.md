---
title: Knot lifecycle — end-to-end pipeline run
status: note
project: knot
tags: [lifecycle, pipeline, runs, planning]
created_at: 2026-04-28T00:00:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# End-to-end pipeline run lifecycle

This note traces a single pipeline run through knot from trigger to
publish. It assumes the canonical six stages and the architectural
split that knot specifies and tracks while an external `Orchestrator`
plugin (Maestro, Argo, the in-tree default queue) actually executes
work. The goal is to make the boundary between knot-owned state and
orchestrator-owned state concrete enough that the `runs` /
`run_events` schema and the `Orchestrator` protocol can be finalized
against it.

## Triggers

A run is always initiated by an explicit trigger. Knot core does not
contain a scheduler. There is no cron loop, no in-process tick.
Recurrence is somebody else's problem (Maestro, Argo, an external
cron sidecar, a human clicking a button).

**Internal triggers** (v1 — API-only):

- `POST /pipelines/{name}/runs` — kick off one named pipeline. Body
  carries optional inputs: source watermark overrides, dry-run flag,
  webhook URL for completion callback, idempotency key.
- `POST /trigger` (a.k.a. `POST /runs?pipelines=…`) — kick off all
  pipelines, or a tagged subset. Used for "rebuild the whole graph"
  or "run everything tagged `nightly`".
- `POST /materialization/specs/{id}/runs` — bespoke spec submission
  path used by the Ontology Author / ER Engineer when iterating on
  a candidate spec.

Each of these is a thin handler: validate the request, run pre-flight,
write a row to `runs`, dispatch to the orchestrator, return the
`run_id`. The handler does not block on completion.

**External triggers** are the same trigger endpoints, called by
something outside knot. The canonical case is a Maestro DAG that has
a step `POST https://knot/pipelines/movie-graph/runs` and waits on
the returned `run_id` (or a webhook). Knot does not care who the
caller is — the trigger surface is uniform.

**Future triggers** (deliberately out of v1):

- **Watermark-advance trigger.** When a registered source's upstream
  watermark moves, knot could auto-trigger that source's Ingest +
  downstream rebuild. Compelling for the Source Onboarder persona,
  but adds an event-loop component knot does not currently host.
  Defer.
- **Correction-volume trigger.** Auto-trigger the corrections-as-source
  pipeline once N corrections accumulate. Same shape as
  watermark-advance; same deferral.

Both can land later as a single small "trigger evaluator" service
without touching anything else.

## Pre-flight (static validation)

Every trigger call funnels through the static pre-flight validator
*before* the run row is committed and *before* anything is dispatched
to the orchestrator. The validator is the only place in knot that has
a complete view (ontology + lineage + plugins + auth) so it is the
only place these checks can run cheaply.

Checks (millisecond-scale, no data touched):

- **SQL parses** via sqlglot for every emitted query in the pipeline's
  compiled spec.
- **Type compatibility** — LinkML node shapes ↔ SQL projection types
  ↔ declared output node schema all align.
- **DAG completeness** — every upstream node referenced by a stage
  exists in `ontology_nodes`; no dangling refs, no forward refs, no
  cycles.
- **Plugin satisfiability** — the `ERStrategy`, `Orchestrator`,
  translator languages referenced exist on this deployer's plugged
  implementations.
- **Authorization** — the caller (`IdentityProvider.whoami` →
  `AuthorizationService.can`) is permitted on every node and action
  the pipeline touches.
- **Ontology conformance** — declared per-stage outputs match each
  target node's LinkML shape; SHACL constraints are at least
  declarable given the projected types.
- **DQ-check SQL** — Validate-stage SQL checks parse and reference
  real columns.

Outcome:

- **Pass.** Knot writes a `runs` row with `status='pending'`,
  records spec hash, computes the stage DAG, writes one
  `run_events.kind='preflight_passed'` row, and dispatches to
  `Orchestrator.submit(spec)`.
- **Fail.** Knot returns 4xx with structured per-check errors
  (path + check name + remediation hint) so the UI can highlight
  fields. **No `runs` row is written.** The orchestrator is never
  contacted. This is deliberate: orchestrator queue depth and Spark
  cost stay clean of obviously-broken specs.

## Stage 1: Ingest

**Owns the source-side boundary.** One Ingest stage per registered
source (corrections-as-source is a regular source for this purpose).

- **Input:** the source registration in `ontology_nodes` (kind=`source`)
  + current `watermarks` row for this source + the source's connection
  config.
- **Knot's job:** emit a job spec ("pull rows from source X where
  partition > last-seen watermark; land them in lake staging table
  `staging.<source>.<node>`") via the materialization compiler.
  Track dispatch.
- **Orchestrator's job:** run the Spark job that pulls + lands. Knot
  polls `Orchestrator.status(run_id)`.
- **Output:** raw-shape staging tables in the lake. Watermark advance
  is *not* committed yet — only after Publish.
- **`run_events` written:** `stage_started{stage:'ingest', source:X}`,
  `stage_progress` (optional, on poll updates), and either
  `stage_completed` or `stage_failed` with payload (rows landed,
  bytes, partition cursor reached).
- **Failure modes:** source unreachable, auth expired, schema drift
  detected at landing, partition overlap with previous run, Spark
  cluster failure.
- **Stage retry:** the orchestrator may auto-retry transient failures
  per its own policy (knot does not encode retry counts). On final
  failure the run halts at this stage; an operator can re-trigger
  the whole run, or — once it exists — the `POST /runs/{id}/retry?
  from_stage=ingest` endpoint resubmits from this stage forward
  using the same spec hash.

## Stage 2: Normalize

**Source-shape → ontology-shape, per source.** No cross-source mixing
yet.

- **Input:** the staging tables produced by Ingest plus the source's
  ontology mapping and the relevant LinkML schema revisions.
- **Knot's job:** compile the mapping + LinkML schema into Spark SQL
  via sqlglot; include SHACL-derived row-level checks (cardinality,
  required slots, type conformance) inline.
- **Orchestrator's job:** run that SQL job; surface SHACL failures as
  rejected rows with reason codes.
- **Output:** per-source, ontology-shaped record tables (still
  partitioned by source).
- **`run_events` written:** `stage_started{stage:'normalize'}`,
  `shacl_violations{count, sample}` (warning), `stage_completed` or
  `stage_failed`.
- **Failure modes:** mapping references a slot that no longer exists
  on the current ontology revision (should have been caught at
  pre-flight, but a concurrent ontology change can race),
  SHACL hard-fail rate above threshold, type-cast errors that
  sqlglot didn't flag statically.
- **Stage retry:** same shape as Ingest. SHACL violations are *data*
  failures, not pipeline failures — they fail the stage only when
  threshold-gated.

## Stage 3: Resolve

**Entity resolution.** Block + match + cluster as one logical stage;
the substages are `ERStrategy` internals, not pipeline stages.

- **Input:** all normalized per-source records for one ontology node
  (e.g., every source's view of `Movie`).
- **Knot's job:** select the configured `ERStrategy` plugin for this
  node, hand it the input set + sources list + current `trust_score`
  values from postgres, dispatch its emitted job spec.
- **Orchestrator's job:** run the strategy's job (typically a
  blocking + matching + clustering Spark pipeline).
- **Output:** entity clusters — sets of `(record_id, source_id,
  cluster_id)` triples.
- **`run_events` written:** `stage_started{stage:'resolve',
  er_strategy:vX}`, `cluster_stats{n_clusters, n_singletons,
  largest_cluster_size}`, `stage_completed` or `stage_failed`.
- **Failure modes:** strategy timeout on a degenerate cluster, OOM
  on blocking key explosion, strategy version drift mid-run,
  ER strategy contract violation (returns malformed clusters).
- **Stage retry:** an ER engineer (persona 4) can swap strategies
  via spec edit + re-trigger; clustering is deterministic given
  inputs + strategy version, so re-runs reproduce.

## Stage 4: Merge

**Trust-aware reconciliation.** This is materialize-time
reconciliation — the resolved layer is computed here, not on read.

- **Input:** clusters from Resolve + current `trust_score` per source
  in postgres + provenance columns from the per-source records.
- **Knot's job:** emit the merge job spec — a SQL window over each
  cluster picking the highest-trust value per `(entity, property)`
  tuple, materializing both the **per-source layer** (one row per
  `(entity, property/relation, source)`) *and* the **resolved layer**
  (winners only). Provenance columns (`source_id`, `correction_id`,
  `materialized_at`, `trust_at_materialization`) are written on
  every row.
- **Orchestrator's job:** run the merge SQL.
- **Output:** two lake tables per ontology node — per-source and
  resolved — staged for atomic publish.
- **`run_events` written:** `stage_started{stage:'merge'}`,
  `merge_stats{n_resolved_entities, n_corrections_applied,
  n_ties_broken}`, `stage_completed` or `stage_failed`.
- **Failure modes:** trust score table read race (mid-run trust
  update — pinned at stage start), cluster too large for the
  windowing, type coercion conflict on the resolved value.
- **Stage retry:** deterministic given inputs + frozen trust
  snapshot, so reruns reproduce.

## Stage 5: Validate

**Post-resolution data quality.** SQL checks beyond SHACL:
statistical (distribution, null rate), freshness (rows landed in
window N), cross-entity invariants (no Movie has > N CastMemberships
of one role).

- **Input:** the staged resolved layer from Merge.
- **Knot's job:** look up the DQ-check nodes (versioned `dq_check`
  ontology nodes) attached to the target node, emit each as SQL,
  classify each check as `hard` or `soft`.
- **Orchestrator's job:** run all DQ checks; report pass/fail per
  check.
- **Output:** a per-check report (counts, samples, pass/fail) plus
  a stage-level pass/fail.
- **`run_events` written:** `stage_started{stage:'validate'}`,
  `dq_check_result{check_id, kind:'hard|soft', status, sample}` per
  check, `stage_completed` or `stage_failed`.
- **Failure modes:** any hard check fails → stage fails → pipeline
  halts before Publish. Soft checks emit warnings only and do not
  block. DQ-check SQL parse error (should have been caught at
  pre-flight; race-condition only).
- **Stage retry:** Validate is read-only over the staged Merge
  output, so retrying without a Merge re-run is cheap and useful
  when the failure was a flaky cross-entity invariant or a check
  whose threshold needs adjustment.

## Stage 6: Publish

**Atomic transition: nothing visible to consumers until this stage
commits. Everything before this point is staged.**

- **Input:** validated per-source + resolved layers from Merge
  (untouched by Validate).
- **Knot's job:** orchestrate the cutover:
  1. Orchestrator writes the per-source + resolved tables to their
     final lake locations (or commits the staged write — depends on
     lake tech, owned by the Spark job).
  2. Knot, in a single postgres transaction:
     - flips `ontology_nodes.current_version` for affected
       node(s) to the new revision pointer,
     - advances `watermarks` rows for every Ingested source to the
       cursor reached this run,
     - appends a `history` row per affected entity with
       `activity_type='materialized'`, pre/post pointers, and the
       `run_id` as `request_id`,
     - marks `runs.status='succeeded'`, `finished_at=now()`.
- **Orchestrator's job:** the lake write itself (Spark job atomic
  rename / commit).
- **Output:** new lake tables visible at the published version
  pointer; downstream consumers reading `current_version` see the
  new revision on their next read.
- **`run_events` written:** `stage_started{stage:'publish'}`,
  `pointer_flipped{node_id, from_version, to_version}` per node,
  `stage_completed`, `run_completed`.
- **Notification:** if the trigger request supplied a webhook URL,
  knot POSTs `run_completed` to it. Otherwise the caller polls
  `GET /runs/{id}` until terminal.
- **Failure modes:** lake write fails mid-commit (orchestrator
  surfaces, knot does not flip the pointer), postgres transaction
  fails (lake tables are written but the version pointer didn't
  flip — those tables become unreferenced and are GC-able by
  hash; the run is marked failed).
- **Stage retry:** Publish is the only stage whose retry is
  semantically delicate — the lake write may have partially
  committed. Retry policy: knot's transaction is the source of
  truth for "did this run publish." If the postgres txn didn't
  commit, the run is unpublished regardless of lake state, and
  retry is safe (idempotent on hash). If it did commit, the run
  is published, full stop.

## Stage transitions and failure handling

**Transitions are gated on stage success.** Stage N+1 is dispatched
only after stage N reports `stage_completed`. Knot does not run
stages in parallel within a single pipeline run; the DAG is a strict
chain Ingest → Normalize → Resolve → Merge → Validate → Publish.

(Cross-pipeline parallelism is fine and expected: ten different
pipelines triggered together each progress through their own chain
independently. Within a chain, sequencing matters.)

**Partial failures.** A stage either succeeds or fails — there is no
"partially succeeded" state at the stage level. Within-stage row-level
failures (rejected SHACL rows, soft DQ check warnings) are recorded
as `run_events` payloads and counted, but do not change the stage
outcome unless they cross a configured threshold.

**Where knowledge lives:**

| State | Source of truth |
|---|---|
| "Did this stage finish?" | orchestrator (knot polls) |
| "Did this run publish?" | knot postgres (`runs.status` + version pointer) |
| "Why did the stage fail?" | orchestrator (logs, knot stores the summary in `run_events`) |
| "What was the spec?" | knot postgres (`runs.spec_hash` + spec store) |
| "What watermark did this run advance?" | knot postgres (`watermarks`) |
| "What rows did this run write?" | lake (provenance columns reference `run_id`) |

**Retry semantics:**

- The orchestrator owns *task* retry (transient infra blips, Spark
  executor loss). Knot does not configure or count these — that is
  the orchestrator's policy.
- Knot owns *stage* retry (operator-initiated re-dispatch from a
  given stage with the same spec hash). Open: whether this is a new
  `run_id` or a continuation of the same one. Recommended: new
  `run_id` with a `parent_run_id` link, because spec_hash + new
  watermark snapshot may differ.
- Knot owns *run* retry (full re-trigger). This is just calling the
  trigger endpoint again with the same idempotency key.

## Observability during a run

The Pipeline Operator persona is the primary consumer of this view.

In the run dashboard for a single in-flight run an operator sees:

- **Header.** `run_id`, pipeline name, trigger source (caller +
  request_id), spec hash, started_at, current overall status
  (`pending | running | succeeded | failed | cancelled`).
- **Stage timeline.** Six bars (one per stage) with state from
  `run_events`: not-yet-reached / in-flight / succeeded / failed.
  Wall-clock time per stage. Currently-running stage shows the
  orchestrator's most recent `Orchestrator.status` poll.
- **Event log.** Reverse-chronological `run_events` rows: stage
  transitions, SHACL violation counts, cluster stats, DQ check
  results, pointer flip.
- **Drill-downs.**
  - Click a failed stage → orchestrator's run detail (logs link)
    plus the `run_events` payload summary.
  - Click a soft DQ warning → the per-check report row.
  - Click "lineage" → the node graph showing every upstream this
    pipeline depends on (for "is the source of this failure
    upstream of me?").
- **Live polling.** The dashboard polls `GET /runs/{id}` every few
  seconds; knot polls `Orchestrator.status(run_id)` on a similar
  cadence. There is no push channel from the orchestrator to knot
  in v1 — pull is sufficient and keeps the orchestrator contract
  small.

For the all-runs view the same data aggregates: failed runs in the
last 24h, currently-running runs, runs by pipeline, runs by source.

## A full mermaid sequence diagram

```mermaid
sequenceDiagram
    autonumber
    participant Caller as Caller (Maestro / human / API)
    participant API as Knot API
    participant Validator as Static Pre-flight Validator
    participant PG as Knot Postgres (runs, run_events, watermarks)
    participant Orch as Orchestrator (Maestro / default queue)
    participant Lake as Lake (staging + per-source + resolved)

    Caller->>API: POST /pipelines/{name}/runs (webhook?)
    API->>Validator: validate(spec, ontology, plugins, authz)
    alt validation fails
        Validator-->>API: structured errors
        API-->>Caller: 4xx (no run row)
    else validation passes
        Validator-->>API: ok
        API->>PG: INSERT runs(status=pending, spec_hash)
        API->>PG: INSERT run_events(preflight_passed)
        API->>Orch: submit(spec) -> orchestrator_run_id
        API-->>Caller: 202 { run_id }

        Note over Orch,Lake: Stage 1 — Ingest
        Orch->>Lake: pull source -> staging tables
        loop poll
            API->>Orch: status(run_id)
            Orch-->>API: running / done / failed
        end
        API->>PG: run_events(stage_completed: ingest)

        Note over Orch,Lake: Stage 2 — Normalize
        Orch->>Lake: staging -> per-source ontology shape (SHACL)
        API->>PG: run_events(stage_completed: normalize)

        Note over Orch,Lake: Stage 3 — Resolve
        Orch->>Lake: ERStrategy: block + match + cluster
        API->>PG: run_events(stage_completed: resolve, cluster_stats)

        Note over Orch,Lake: Stage 4 — Merge
        Orch->>Lake: trust-aware merge -> per-source + resolved layers
        API->>PG: run_events(stage_completed: merge)

        Note over Orch,Lake: Stage 5 — Validate
        Orch->>Lake: run DQ checks over staged resolved layer
        alt hard DQ fails
            Orch-->>API: stage failed
            API->>PG: run_events(stage_failed: validate); runs.status=failed
            API-->>Caller: webhook run_failed (if registered)
        else all hard pass
            API->>PG: run_events(stage_completed: validate)

            Note over API,Lake: Stage 6 — Publish (atomic)
            Orch->>Lake: commit per-source + resolved tables
            API->>PG: BEGIN
            API->>PG: flip ontology_nodes.current_version
            API->>PG: advance watermarks
            API->>PG: append history rows
            API->>PG: runs.status=succeeded; run_events(run_completed)
            API->>PG: COMMIT
            API-->>Caller: webhook run_completed (or caller polls)
        end
    end
```

## Open questions

1. **Retry endpoint shape.** `POST /runs/{id}/retry?from_stage=…` vs
   "always start a new run." Recommendation above is "new run with
   `parent_run_id` link" but not locked.
2. **Idempotency key semantics.** When the same idempotency key is
   replayed mid-run, does knot return the in-flight `run_id` or
   422? Likely the former.
3. **Webhook delivery guarantees.** At-most-once with retry budget?
   Signed payloads? Nothing about webhook delivery is specified yet.
4. **Watermark-advance trigger.** Concretely where does the watermark
   evaluator live — knot core, a sidecar, the orchestrator? Defer
   until a real source motivates it.
5. **Cancel semantics mid-stage.** `POST /runs/{id}/cancel` calls
   `Orchestrator.cancel(run_id)`, but what happens to staged but
   unpublished lake writes? GC by `run_id` is the obvious answer;
   the policy is not yet written down.
6. **Cross-run dependency.** Can pipeline B's trigger declare "wait
   for pipeline A's latest run to succeed"? Today: no — let Maestro
   do that. May surface as a real ask.
7. **`run_events` retention.** Append-only forever, or rolled off
   after N days into a cold table? No decision.
8. **Soft DQ surfacing.** Soft warnings live in `run_events` — do
   they also need a dedicated `dq_warnings` table for trend queries?
   Probably yes once we have real DQ checks running.
