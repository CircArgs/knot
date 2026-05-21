# knot_temporal — reference adapter design

**Status:** design doc. The adapter is not yet built. This document
specifies the shape so a future `knot_temporal/` sibling package can
ship without surprises.

## Posture

Same as `knot_graphql/`: a **reference adapter** sitting on top of
the substrate. `knot` ships the SQL compiler; `knot_temporal` ships
the workflow + activity shapes a team would otherwise hand-write
once per source.

- `knot_temporal` imports `knot`; knot does NOT import
  `knot_temporal` (one-way dep, same shape as the GraphQL adapter)
- Pure-Python + `temporalio` SDK; no DB driver dep
- Workflows are **deterministic** (orchestrate only, no I/O); all
  knot SQL emitters get called from **activities**
- Activities take a host-injected `sql_executor` callable so the
  package itself doesn't depend on psycopg / SQLAlchemy / asyncpg

The user-facing surface:

```python
from knot_temporal import register_workflows
from your_spec import spec

# Walks spec.source_bindings + classes, registers one ingest
# workflow per binding + one embedding worker per VECTOR slot +
# one ER worker per multi-source class + the validation sweep.
register_workflows(client, spec, sql_executor=my_executor)
```

The team writes their source-specific `fetch_batch` activities (the
external API integration) and registers them alongside.

## Workflow catalog

Six workflow types. Each is a template parameterized by spec /
source / class — `register_workflows` instantiates them per binding.

### 1. `IngestSourceWorkflow`

**One per `SourceBinding`.** Scheduled (cron) or signal-triggered.

```
fetch_batch(source, class, cursor)
  → validate_and_bucket(source, class, rows)
  → upsert_rows(source, class, clean)
  → route_to_review(violations)        # only if any
  → emit_summary
```

- `fetch_batch` is source-specific and host-owned (not shipped by
  knot_temporal); the source API knows pagination cursor, rate
  limits, auth
- `validate_and_bucket` calls `binding.validate_rows_sql()`, splits
  clean from violation rows by `row_index`
- `upsert_rows` calls `binding.write_sql()` inside
  `pg.transaction()`; idempotent via ON CONFLICT
- `route_to_review` is host-owned — a sink for the dirty subset
  (DLQ topic, review-queue table, an oncall ticket, etc.)
- Re-running is safe: `write_sql` upsert preserves `canonical_id` +
  `er_metadata` on every re-ingest

### 2. `EmbeddingBackfillWorkflow`

**One per `(class, source, vector_slot)` triple.** Continuous
worker; polls for unembedded rows.

```
fetch_unembedded(class, source, slot, batch_size)
  → compute_embeddings(rows, model_name)
  → write_slot(class, source, slot, vectors)
  → loop until empty | continuous mode
```

- `fetch_unembedded` calls
  `cls.from_source(src).where(col.is_null()).lock("for_update_skip_locked").limit(N)`
  — the `for_update_skip_locked` is what makes N concurrent
  replicas safe (each worker claims a disjoint slice atomically)
- `compute_embeddings` runs the model; an activity, not a workflow,
  because GPU calls are non-deterministic
- `write_slot` calls `binding.update_slot_sql(slot_name)` — the
  positional UPDATE; safe to re-run because it matches on
  source_identifier

### 3. `ERWorkflow`

**One per multi-source class.** Continuous worker.

```
fetch_unresolved(class, source)
  → find_candidates(class, candidates, target_source)   # cross-source k-NN
  → apply_policy(candidates, policy_config)              # host-owned
  → mint_canonicals(class, source, decisions)
  → emit_summary
```

- `fetch_unresolved` uses `cls.from_source(s).where(canonical_id.is_null())`
- `find_candidates` uses `col.distance_to(target_vec)` against
  `from_source(other_src)` to get HNSW push-down
- `apply_policy` is host-owned — threshold, collision detection,
  human-in-loop for ambiguous cases. knot doesn't decide who wins.
- `mint_canonicals` uses `binding.assign_canonicals_sql()` (batched
  jsonb form) — many decisions in one round-trip per source
- canonical_id minting is **deterministic** (sha1 of identity) so
  partial-failure retries converge instead of desync

### 4. `ValidationSweepWorkflow`

**One global sweep.** Cron'd (hourly / daily / per environment).

```
emit_validations(spec, scope=None)
  → run_each(rule, sql)
  → severity_gate(violations)
  → page_oncall(error_severity) | log(warning_severity)
```

- `spec.emit_validation()` includes built-in constraints
  (FK-orphan, required-null) AND user constraints in one call
- Severity-gated alerting: ERROR violations page, WARNING
  violations log + report. The host owns the policy via the
  `severity_gate` activity.
- Can scope via `scope_to_source_identifiers` for delta-only sweeps
  after a fresh ingest batch (the post-write enforcement path)

### 5. `RecanonicalizeWorkflow`

**Triggered.** When a human-in-the-loop ER review reverses a
prior decision or when corrections-source updates fire.

```
recanonicalize(class, source, old_id → new_canonical_id)
  → cascade_check(class)         # verify the recanonicalize cascade
  → emit_audit_entry
```

- Calls `binding.recanonicalize_sql()` — single statement that
  captures old_id, stamps new, cascades to every referencing
  binding's FK columns
- The cascade is source-agnostic (canonical-ids are global), so
  this fixes referential integrity automatically

### 6. `RetractWorkflow`

**Triggered.** When a source publishes a deletion or a correction
is withdrawn.

```
retract(class, source, source_identifier)
  → cascade_check(class)
  → emit_audit_entry
```

- Calls `binding.retract_sql()` — DELETE on the bindings row
- The resolver view filters `canonical_id IS NOT NULL`, so a
  retract that's the last source for a canonical_id naturally
  removes that row from reads

## Activity layer

Activities are where I/O happens. Each is a thin wrapper around a
knot emitter + `sql_executor`:

```python
@activity.defn
def validate_and_bucket(source: str, class_name: str,
                         rows: list[dict]) -> BucketedRows:
    binding = spec.sources[source].binding_for(spec.classes[class_name])
    violations_sql = binding.validate_rows_sql()
    with sql_executor.cursor() as cur:
        cur.execute(violations_sql, {"rows": json.dumps(rows)})
        violations = cur.fetchall()
    bad = {v[0] for v in violations}
    clean = [r for i, r in enumerate(rows) if i not in bad]
    return BucketedRows(clean=clean, violations=violations)
```

The activity is the boundary where:
- Non-determinism is allowed (clock reads, randomness, network)
- Retries are configured (`RetryPolicy`)
- Timeouts are configured (`start_to_close_timeout`, `heartbeat_timeout`)
- The result type is serializable (Pydantic models or dataclasses)

## Replay safety

**Workflow code must be deterministic.** Two corollaries:

1. **Never call knot emitters in workflow code.** Compiling a SQL
   string is deterministic, but the moment you do it inside a
   workflow you've leaked the spec module reference into the
   workflow history — a redeploy with a different spec replays
   into a poisoned state. Always emit SQL inside activities.
2. **canonical_id minting via hashlib is deterministic** but should
   still happen in an activity — it's an output of ER, not
   orchestration.

Idempotency contract — what makes Temporal retries safe:

| primitive | retry-safe because |
|---|---|
| `write_sql` | ON CONFLICT (source_name, source_identifier) DO UPDATE; canonical_id + er_metadata preserved |
| `validate_rows_sql` | SELECT only; pure |
| `update_slot_sql` | positional UPDATE matched on source_identifier; idempotent |
| `assign_canonicals_sql` | stamp filters `canonical_id IS NULL`; second call no-ops; fan-out gated on EXISTS-FROM-stamp so it can't force-rewrite |
| `recanonicalize_sql` | captures old_id before stamp; cascade re-runs no-op when old_id no longer matches |
| `retract_sql` | DELETE; re-runs no-op (row already gone) |
| `emit_validation` | SELECT only; pure |

## Scheduling

`Source.poll_interval` (new optional field) drives the cron
schedule:

```python
imdb = spec.add_source("imdb", poll_interval=timedelta(hours=6))
tmdb = spec.add_source("tmdb", poll_interval=timedelta(days=1))
```

`register_workflows` walks bindings + sources and uses
`client.create_schedule()` to register one `IngestSourceWorkflow`
cron per source × class. Backfill workflows run continuously;
validation sweep cron is configurable (default 1 hour).

## Failure modes the activity layer handles

- **Source API rate-limit / timeout** → activity retry with
  exponential backoff (`RetryPolicy`)
- **Postgres temporary error** → activity retry
- **Schema mismatch** (source returns a new field the spec doesn't
  know about) → captured in `raw_payload`; not a failure
- **Validation violations** → not a failure; route to review queue
- **ER ambiguity** (multiple high-confidence matches, none over
  threshold) → not a failure; route to human review
- **Constraint ERROR with severity-block policy** → workflow
  failure (page oncall)
- **Workflow worker crash mid-batch** → Temporal replays; idempotent
  primitives mean the second run no-ops the already-done work

## Out of scope for v0 of `knot_temporal/`

- Dashboards / UI / monitoring integration
- Auth (which source's API token to use)
- Pagination cursor encoding (source-specific)
- Source-side schema discovery (source publishes a JSON spec, we
  diff against our spec)
- Cross-Temporal-cluster federation
- Strawman: a `knot_temporal cli` for ops actions (force a
  recanonicalize, dry-run a validation sweep)
- Custom workflow versioning helpers (Temporal's
  `workflow.patched` is enough)

## Open questions for design review

1. Should `register_workflows` take an explicit `taskQueue` per
   workflow type, or one global task queue? Production deploys
   probably want separate queues (ingest vs ER vs validation)
   for capacity planning.
2. Should `EmbeddingBackfillWorkflow` be one workflow per `(class,
   source, slot)` or one workflow that loops over all triples?
   Per-triple gives finer-grained worker scaling; one-workflow
   gives simpler state.
3. How does `apply_policy` activity get configured? Pluggable
   callback? Yaml config? Code-driven? Probably a callback
   registered alongside `register_workflows`.
4. `Source.poll_interval` belongs in spec or in deploy config?
   Spec is convenient but mixes "how the data is shaped" with
   "how often we pull it" — probably should be deploy config.

## What this doc is NOT

- A replacement for Temporal SDK docs; assume the reader knows
  Temporal Python SDK fundamentals
- An auto-gen specification — `register_workflows` is hand-coded
  to enumerate the spec; not generated from a schema like the
  GraphQL adapter
- Auth / observability story — that's host-shape work outside
  the adapter package

When `knot_temporal/` ships, the codebase under `knot_temporal/`
will follow this doc's structure. Discrepancies are bugs in the
doc or in the adapter, not features.
