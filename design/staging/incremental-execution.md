# Incremental execution — graph-level skip via per-stage cache keys

**Status:** decided. Supersedes the prior "future direction" hedge in `pipeline-stages.md` with a committed mechanism. Integrates with commitments 1, 3, 8.

---

## The model

knot tackles incremental at the **ontology-graph topological-sort level**, not at the row level. When a pipeline runs:

1. Each stage in the compiled `WorkflowSpec` carries a deterministic input set.
2. knot computes a content-addressed **cache key** per stage from those inputs.
3. If the cache key matches a previous successful execution, the stage is **skip-able** — its output reuses the prior run's artifact.
4. The orchestrator skips on cache hits and runs on cache misses; cache misses propagate "rerun" forward through the toposort to dependents.

knot emits the skip plan in the dispatched `WorkflowSpec` per commitment 1 ("compile, never execute"). The orchestrator does the actual skipping; knot computes which stages can be skipped.

No row-level delta API. No `ctx.delta_since(...)`. The granularity is per-stage, per-class.

## The cache key

For each stage, the cache key is a hash of:

```
(stage_kind,
 class_or_target,
 input_spec_revisions,           # all spec entities the stage's compile network reads
 impl_source_hash,                # bytes of the bound impl's Python source
 impl_config_revision,            # postgres-control config snapshot
 source_watermarks_at_read,       # for normalize stages: the source watermark
 pinned_parent_runs)              # for relation-class stages: cross-class-pinning hashes
```

Same canonicalization machinery as `compiled_workflows.hash` (RFC 8785 JCS, sha256). Same audit-walk-back chain.

A stage hits cache iff a prior `pipeline_runs` row records a successful execution with the identical cache key.

## How "unchanged" propagates

For a typical KG with sources `imdb_movies`, `tmdb_movies`, `wikidata`, classes `Movie`, `Person`, `Credit`:

- `normalize:imdb_movies` — input includes the source watermark. If watermark hasn't moved since last run → cache hit, skip; output reuses last run's per-source-facts parquet.
- `normalize:tmdb_movies` — same logic.
- `resolve:Movie` — input includes per-source-facts watermarks for Movie's contributing sources, ER impl source hash, ER config rev. If all unchanged → cache hit, skip. The "watermark" of `resolve:Movie`'s output is the latest input watermark seen.
- `merge:Movie` — input includes `resolve:Movie`'s output watermark. If `resolve:Movie` skipped (output unchanged) → `merge:Movie` cache hits too.
- `resolve:Credit` — input includes `pinned_parent_runs.{Movie, Person}`. If both Movie and Person skipped (so their pinned-parent run hashes haven't changed since last Credit run) → Credit's pinned-parent hash matches → cache hit, skip.
- `materialize:neo4j_publisher` — input includes the watermarks of all classes it reads. If everything upstream skipped → publisher skips.

A change anywhere in the source watermarks, spec revisions, impl source, or impl config invalidates the cache key for that stage and forwards through the toposort to every dependent stage.

## What knot owns vs. what the orchestrator owns

- **knot:** computes the cache key per stage at compile time; queries `pipeline_runs` for matching successful keys; emits per-stage `cache_hit: <prior_run_artifact_path>` or `cache_miss: <run-this>` flags in the dispatched `WorkflowSpec`.
- **Orchestrator:** sees the per-stage flags; for cache hits, skips execution and exposes the prior artifact as the stage's output (e.g., a parquet path or table snapshot id); for cache misses, runs the stage normally.

knot stays compile-only (commitment 1). The orchestrator already handles task-level skipping in Maestro / Airflow / Argo — the skip flag in the WorkflowSpec is just metadata it acts on.

## Composition with cross-class pinning

Cross-class pinning (commitment 8) and per-stage cache keys are complementary:

- Cross-class pinning tells the relation-class compile **which parent run hashes** to pin.
- Per-stage cache keys tell the orchestrator **whether the relation-class stages need to run** given those pinned hashes.

If `resolve:Credit` is pinned to (Movie hash A, Person hash B) and a prior `pipeline_runs` row has the same pinned hashes + same Credit ER config → cache hit, skip. Even if other classes changed.

## Composition with materialization (multi-class DataContexts)

A Materialization impl with `primary=[Movie, Person, Credit]` reads N views. Its cache key includes the watermarks of all N source views. A change to any one invalidates the materialization's cache key.

For graph stores this is correct behavior: even if only Person changed, the published graph snapshot must rebuild edges that touch Person. Impls wanting to do row-level incremental optimization on top can do so internally — they're free to read `ctx.last_successful_run(...)` (which knot exposes as a typed run handle) and decide not to rewrite unchanged Movie nodes. That optimization is impl-side; knot's contract is the per-stage skip.

## What's NOT in scope

- **Row-level delta API.** No `ctx.delta_since(run)` returning per-canonical-id changes. Impls that want this build it themselves on top of the SCD2 `entity_bindings` + `canonical_id_lineage` knot already records.
- **Auto-incremental materialization.** Materialization impls that opt into incremental are responsible for the row-level merge logic (MERGE / UPSERT / SCD2 in their target). knot's contract is "your inputs are unchanged, you may skip" — not "knot will diff your inputs row-by-row."
- **Cache eviction.** `pipeline_runs` and `compiled_workflows` are retained forever (per the just-decided cross-class-pinning retention policy). Cache lookups never miss because of eviction; only because the input set genuinely changed.

## What's still implementation detail

- The hash-input field order — same canonicalization as `compiled-workflow-hashing.md`.
- Whether `cache_hit` carries the artifact path inline in the `WorkflowSpec` or just the prior run's id (orchestrator dereferences) — emission-shape choice.
- How orchestrators expose "this stage was skipped, here's the prior artifact" to downstream stages — orchestrator API choice.
- Whether the publish gate validates against the *would-have-run* output or accepts the cached output as-is — both are correct given content-addressing.

## Cross-references

- `core-design.md` § 1 (compile, never execute) — knot emits the plan; orchestrator runs/skips.
- `core-design.md` § 3 (content-addressed compile hashes) — cache keys reuse the same canonicalization.
- `core-design.md` § 8 (cross-class pinning) — composes with cache keys; pinned-parent hashes are part of the input set.
- `staging/cross-class-pinning.md` — cache-key composition example with relation classes.
- `staging/compiled-workflow-hashing.md` — canonicalization machinery; cache keys reuse it.
- `staging/pipeline-stages.md` — per-stage cache key model is referenced from the snapshot-rebuild section.
- `staging/multi-class-datacontexts.md` — multi-class views compose with cache keys via per-class watermarks.
