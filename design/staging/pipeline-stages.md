# Pipeline stages

**Status:** decided.

End-to-end flow from source rows in the lake to consumer-facing graph or query results.

## Trigger model — knot is not a scheduler

Knot exposes an API; pipeline triggers arrive via API calls from an external scheduler (Maestro, Airflow, Argo) or a manual operator. Knot does not poll, does not own a schedule, and does not decide when to run.

Per trigger, knot's job is: read the spec, compile a `WorkflowSpec` for the requested scope, content-address it, and hand it to the orchestrator. The API caller can request whole-pipeline runs, individual stage runs, or per-class runs (`POST /runs/Credit`). Scope is always caller-supplied; knot does not decide which classes to run or in what order.

The orchestrator drives the toposort and stage skipping (per `incremental-execution.md`); knot emits the plan with per-stage cache flags but does not execute anything. This is commitment 1 ("compile, never execute"). See `core-design.md` § 1 and § 9 (source-layer contract / orchestrator scheduling) and `staging/incremental-execution.md`.

## The flow

```
team-side ingestion        (out of scope per source-layer-contract — sources land in lake however)
        ↓
[normalize]                source-shape → ontology-shape per source
        ↓ [DqNormalizeRunner — if configured]
                           per_source_facts (wide per (class, source))
        ↓
[resolve / ER]             per ontology class — produces canonical entity IDs
        ↓ [DqResolveRunner — if configured]
                           entity_bindings (source, src_key) → canonical_id  +  decision audit
        ↓
[merge]                    per canonical entity — joins per-source-facts to canonical_ids; updates trust state
        ↓ [DqMergeRunner — if configured]
                           resolved_facts (canonical_id-keyed, multi-valued — all source contributions retained)
        ↓
[validate]                 structural validation (SQL from spec) — fail-loud on violations
        ↓
[publish / materialize]    fork:
        ↓ [DqPublishRunner — if configured]
                           ├─→ graph-store consumers — forward-chained derived edges
                           └─→ lake analysts via knot's built-in query endpoint (or Translator impl for non-lake targets)
```

Each DQ stage is optional and configured independently. When present, the appropriate `DqRunner` subprotocol runs after its stage and before the next stage proceeds. See `dq-design.md` for the full family definition and per-stage built-in check assignments.

**Relation classes (Credit, Identifier, ActedIn, etc.):** at compile time — *before* the stages above run — knot's compiler resolves the parent classes' run hashes and pins them into the workflow spec (`pinned_parent_runs`). The relation class's resolve/merge/validate/publish stages then read parent canonical_ids "as of" those pinned runs, never "current." See [`cross-class-pinning.md`](cross-class-pinning.md).

## What each stage does

Each stage's bound DI implementation interacts with knot via the protocol+context pattern (see `seam-contract-pattern.md`) — implementations call into a context to fetch data and emit results, rather than directly accessing knot's lake tables.

- **Normalize.** One task per source. Applies the source's mapping (source spec → ontology class), validates projected rows against the ontology class constraints (SQL queries from knot's structural validator), drops non-conforming. Output: per-source-facts in ontology-class shape. (Knot vs. team responsibility for normalize is still open.)
- **Resolve (ER).** Per ontology class. The bound ER impl returns pairwise equivalence edges with confidence (impl declares its inputs via DataContexts on the impl class; knot materializes them and dispatches per `di-input-contract.md`). Knot then applies threshold + transitivity to form connected components, reconciles against existing canonical_ids, mints deterministic canonical_ids for new entities, and persists bindings + decision audit. Base/row-level knot IDs are never overwritten. **For relation classes**, parent canonical_ids are read at the pinned parent run hashes (see `cross-class-pinning.md`). (See `er-and-storage.md`.)
- **Merge.** Per canonical entity. Joins per-source-facts to canonical_ids (from ER) to produce the multi-valued canonical view (`resolved_facts` — every source's contribution retained, none discarded). Updates trust state from new evidence per the Beta-Bernoulli bandit. **Default-value selection happens at query time inside knot's SDK, not at this stage** — merge does not write a "winner" column. (See `trust-and-merge.md` and `auto-generated-sdk.md`.)
- **Validate.** Knot emits structural-validation SQL from the ontology class constraints (cardinality, types, ranges, patterns) and runs them against resolved data. Domain DQ checks run via the `DqRunner` protocol family (per `dq-design.md`): `DqNormalizeRunner` after normalize, `DqResolveRunner` after resolve, `DqMergeRunner` after merge, `DqPublishRunner` after publish. Each subprotocol pins its lens to the appropriate upstream tables. How failures propagate is not yet designed.
- **Publish / materialize.** Free-form DI impls (per `di-input-contract.md`); see "Materialization" below.

## Materialization

Materialization is a **free-form DI stage**, not a per-class operation. A bound impl declares whatever DataContexts it needs (single-class or multi-class joined / projected / filtered), runs whatever transformation it wants, and publishes to whatever target. Same DI pattern as ER, DqRunner, etc.: declared DataContexts, a `Config`, and a method that does the work.

`DataContext.primary` accepts `OntologyClass | list[OntologyClass] | DerivedSlot | list[DerivedSlot]`. The three common shapes are:

1. **Single class** — `DataContext(primary=Movie)` → one view at fulfill.
2. **Multi-class** — `DataContext(primary=[Movie, Person, Credit])` → three views; impl receives `{Movie: <view>, Person: <view>, Credit: <view>}`.
3. **Whole graph** — `DataContext(primary=spec.classes)` (or a list-comp over it) → N views, one per class; spec additions flow in automatically at next compile.

A list of `DerivedSlot` values fans out to edge views instead (e.g., `{Movie.director: <edge_view>, ...}`). The reference impl shape for a Neo4j publisher — two DataContexts, one for node classes and one for derived-edge slots — is in `staging/multi-class-datacontexts.md`.

Common targets:

| Target | Consumer | Pattern |
|---|---|---|
| Graph store (Neo4j, custom) | Apps, services, search | Forward-chained — impl reads denormalized DataContexts + emits node/edge CSVs (often via S3 + LOAD CSV) or pushes via Bolt MERGE. |
| Vector store | Similarity search | Impl reads relevant slots, computes embeddings, writes vectors. |
| Lake analytics table (Iceberg / parquet) | Analysts, BI, ad-hoc queries | Forward-chained — impl writes a denormalized view via the Materializer protocol. Often unnecessary if the translator handles ad-hoc queries backward-chained. |
| CSV / parquet export | Downstream BI, ML pipelines | Impl reads the relevant DataContexts and dumps to a known location. |

One impl per target by default; one impl can span multiple targets if the team prefers (e.g., dump a CSV AND push to Neo4j from the same publish run). knot doesn't prescribe; the impl decides.

Translator (consumer-facing query expansion at query time, backward-chained) is also a bound impl — separate concern, exposes lake data to consumers without prior materialization to a graph store.

**Operates over canonical entities post-merge.** Materialization impls read from `resolved_facts` (post-merge) via their DataContexts; knot's QueryExecutor (per `query-executor.md`) materializes the views that fulfill the DataContexts.

### Materialization model: snapshot-rebuild (default)

The default pattern for materialization impls is **snapshot-rebuild**: compute the full derived state per publish run, write it to the target, atomically replace the previous publish. The impl owns the actual implementation, but this is the recommended posture and what knot's defaults assume.

- Each publish run computes the full set of derived facts via one-shot SQL queries against `resolved_facts` plus the spec-declared derivation rules.
- The impl writes the result to the target (Neo4j database alias, Iceberg branch, partitioned parquet directory, etc.) and atom-swaps the alias / branch / partition pointer.
- Previous publishes remain available (per the audit + content-addressed spec model) until garbage-collected per retention policy.
- **Not incremental by default.** No DELETE-old-then-INSERT-new merging, no incremental view maintenance.

A specific impl can choose a different strategy (incremental, append-only, CDC-style) if its target requires it — knot doesn't enforce snapshot-rebuild.

The model is conventional — dbt's default `table` materialization is full-refresh; Iceberg branches and Neo4j aliases support atomic swap. It also collapses the forward/backward chain symmetry concern:

- **Backward-chain SQL** (translator at query time): `SELECT what should be true`.
- **Forward-chain SQL** (materializer at publish time): `INSERT INTO target SELECT what should be true` — same query, different wrapping.
- Both compile from the same derivation rule. They agree by construction.

**Snapshot-rebuild is the base mode.** For stages whose inputs are unchanged, knot skips execution entirely via per-stage cache keys: at compile, knot computes a cache key per stage from its full input set (stage kind, class, spec revisions, impl source hash, config revision, source watermarks, pinned parent runs). The orchestrator skips on cache hits and propagates "unchanged" forward through the toposort to every dependent stage. knot emits the skip plan in the dispatched `WorkflowSpec`; executing or skipping is the orchestrator's responsibility. A materialization impl with `primary=[Movie, Person, Credit]` skips only if all N upstream views are cache-stable; a change to any one invalidates its cache key. See `staging/incremental-execution.md`.

**Embeddings (note).** Embedding generation and querying are a parallel materialization DI pair, not part of ER or merge:

- **Embedding generation impl** — same DI pattern as any materialization impl. Declares DataContexts over the entities it embeds, runs the embedding model, writes vectors to the target (vector DB, parquet column, etc.).
- **Embedding query impl** (consumer-facing, parallel to the translator) — surfaces similarity search against the embedding store.

Same shape as graph-store materialization: one DI to produce, one to query. ER doesn't natively support embedding-based matching — if a team wants that, they either pre-compute embeddings lake-side at ingestion (then ER reads them as ordinary slot data via the SDK) or build an ER impl that calls the embedding-query DI for similarities at ER time.

## In-flight corrections overlay

Users author corrections (per-property values) and ER decisions (forced edges / non-edges) that need to take effect immediately — not at the next pipeline run. Both have a defined lifecycle and overlay mechanism for the gap between assertion and pipeline run.

### Lifecycle

- **T1 — assertion.** User submits via knot's API. Data lands in postgres-control immediately:
  - Property corrections → `_user_corrections` table (per-source-facts-shaped row).
  - ER decisions → `_user_er_decisions` table (forced edge or non-edge; see `er-and-storage.md` § "User-provided ER decisions").
- **T1 to T2 — in-flight.** Data lives in postgres only. The lake's `resolved_facts` doesn't reflect it yet.
- **T2 — next pipeline run picks it up.** The `_user_corrections` source's `normalize` step reads postgres → emits `per_source_facts` rows → ER consumes them, regenerating the multi-valued canonical view. ER decisions are applied as forced edges / non-edges in the same run. Postgres staging rows are marked `applied_to_lake=true` (or deleted per retention policy).
- **T2+ — the correction is in the lake.** Overlay no longer needs it.

### Query-time overlay (corrections only)

For property-level corrections, knot's SDK runtime overlays postgres at query time so consumers see fresh corrections immediately. The flow:

1. SDK call: `Movie.runtime` for some canonical_id.
2. Knot's SQL gen runs the SQL against the lake's `resolved_facts` → multi-valued contributions.
3. Knot queries postgres-control for any pending corrections affecting the involved canonical_ids.
4. Knot merges in Python: lake contributions + postgres corrections = full contribution set.
5. Apply the SDK access mode:
   - **Default** → trust picks the highest-trust contribution (correction wins by virtue of high trust).
   - **`.from_source("_user_corrections")`** → returns the pending correction directly.
   - **`.all()`** → returns all contributions, including the pending correction with full attribution (`source=_user_corrections`, `asserted_at=T1`, etc.).
6. Return to caller.

Postgres atomic + consistent → corrections applied during a query are picked up on the next call; within a single call, lake data is materialized first, postgres at the last step before returning. No coordination needed.

The bound impl never sees this federation. It's hidden inside knot's SDK runtime; from the impl's perspective, it just calls `Movie.runtime` and gets the right answer.

### ER decisions: no query-time overlay

Forced edges / non-edges in `_user_er_decisions` don't have a query-time overlay — they affect ER, not value resolution. They're meaningful only at the next ER run. Until then, the canonical_id structure stays as it was.

If an immediate effect is needed, the user re-triggers the affected per-class run; the new run reads `_user_er_decisions` from postgres and applies the assertions during clustering. Audit chain captures the trigger.

### What knot implements

- Postgres-control tables:
  - `_user_corrections` — per-source-facts-shaped pending rows.
  - `_user_er_decisions` — forced edges / non-edges.
- A query-time merge step in the SDK runtime that runs alongside lake SQL and folds postgres results into the contribution set.
- Migration logic at next pipeline run: read postgres → produce `per_source_facts/<Class>/source=_user_corrections` rows → mark staging rows applied.

## Key invariant

**No derived facts exist before the materialization fork.** Per-source-facts hold what sources said. Resolved-facts hold the multi-valued canonical view — every source's contribution joined to canonical_ids; nothing discarded. Anything *inferred* — type-based subclassing, derived properties like `Movie.director`, named relations like `directedBy` from Credit role — emerges only at the materialization layer. The pipeline produces canonical facts (with full lineage as data); downstream surfaces decide what to derive and how.
