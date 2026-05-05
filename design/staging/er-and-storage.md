# ER mechanics and per-source-facts storage

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How the resolve stage operates and what its inputs look like in the lake.

## Per-source-facts storage (knot-internal lake layout)

This section describes how knot organizes its lake. Bound DI implementations do **not** access these tables directly — they use the seam contexts (see `seam-contract-pattern.md`) to fetch records, which knot resolves against the layout below.

**Wide per `(class, source)`.** One physical table per ontology class per contributing source, columns aligned to the class's spec slots plus attribution columns (`source`, `src_key`, `asserted_at`, plus the deterministic `knot_row_id` assigned at ingestion).

```
per_source_facts/Movie/source=imdb_title_basics/...    src_key, title, runtime, year, asserted_at, ...
per_source_facts/Movie/source=tmdb_movies/...           src_key, title, runtime, year, imdb_id, asserted_at, ...
per_source_facts/Credit/source=imdb_title_principals/   src_key, person_src_key, work_src_key, role, character, ...
per_source_facts/LocalizedTitle/source=imdb_title_akas/ src_key, work_src_key, region, language, type, display, ...
```

knot's SQL generator produces this shape naturally — DDL from the class definitions; the pipeline adds attribution columns.

**Logical table per class, partitioned by source.** `per_source_facts/Movie/` is one logical table; partitions are physical optimization, not logical separation. Reading "all Movie facts from all sources" = reading the table. ER doesn't care about partition layout.

## Mutable fields → point-in-time change stream

Mutable properties (ratings, popularity, vote counts) shouldn't overwrite the wide table on every refresh — that loses history.

```
fact_changes/   class, src_key, source, property, value, asserted_at
```

Wide table holds current. Change stream is the ledger. Audit walk-back of "what was the IMDb rating on date X?" reads the change stream.

## ER (resolve stage)

Sits at `resolve` between normalize and merge. Per ontology class, triggered as part of that class's compiled workflow.

The contract follows the protocol + DataContext + pure-data context pattern (see `seam-contract-pattern.md` and `di-input-contract.md`). The bound ER impl implements `ERProtocol`; knot reads its declared `DataContext` attributes at registration, materializes them at runtime via the bound `Materializer` (per `query-executor.md`), and dispatches.

### Protocol

```python
class ERProtocol(abc.ABC):
    @abstractmethod
    def resolve(self, ctx: ERRuntimeContext, **datacontexts) -> Iterable[Edge]:
        ...
```

The impl implements `resolve()`, receives a pure-data context (config + binding info + upstream-job state) plus the materialized DataContexts as named arguments, and returns pairwise equivalence edges.

### Impl shape

```python
from typing import ClassVar
from knot.ontology import Movie, Credit, Identifier
from knot.di import ERProtocol, DataContext, Edge

class MovieResolver(ERProtocol):
    candidates: ClassVar[DataContext[Movie]] = DataContext(
        primary=Movie,
        include=[
            Movie.identifiers,
            Movie.credits.where(Credit.role.in_(["director", "lead_actor"])),
        ],
        filter=(Movie.year > 1900),
    )

    class Config(BaseModel):
        # impl-defined runtime knobs (per di-input-contract.md § "Impl config")
        blocking: list[Slot]
        matching: list[Slot]
        cross_references: list[OntologyClass] = []
        threshold: float = 0.85
        transitivity: Transitivity = Transitivity.WEAKEST_LINK
        mode: ERMode = ERMode.FULL
        conflict_policy: Policy = Policy.SURFACE
        split_policy: Policy = Policy.SURFACE

    def resolve(self, ctx, candidates) -> list[Edge]:
        # `candidates` is the materialized DataContext (a table/view name in the lake);
        # impl reads it via its own engine. `ctx` is pure data — config, binding, upstream state.
        # Internal scoring, blocking, cascade — all the impl's business.
        return edges
```

Knot resolves the DataContext — generates SQL from the structural relationships in the spec (impl doesn't specify FK columns), materializes a view, hands the impl the view name. Reification, partitioning, change streams, mutable-fields ledger, SCD2 bindings, cross-class pinning lens: invisible to impl.

For 1:N relationships, the materialized view is **denormalized cartesian-product rows** — repeated primary-class data, one row per included tuple. The impl aggregates with group-by if it wants list/struct shape (or uses the SDK's `.collect()` / `.group_by_source()` operators in its DataContext to push aggregation into the materialized view).

### What the impl returns

Pairwise equivalence edges:

```
(knot_row_id_a, knot_row_id_b, confidence)
```

Per pair: the two knot row IDs being claimed equivalent, plus a confidence score. That's the entire deliverable. For ambiguous pairs where the impl declines to assert, knot supports a separate "request review" path (mechanically: an output flagged for human review rather than emitted as an edge — exact API tbd).

### Configuration (the impl's Config + the binding's stored config)

There is **no separate "strategy" spec entity** in knot. The impl IS the strategy, plus the runtime-editable config attached to its binding (per `di-input-contract.md` § "Impl config"). Each impl declares its own `Config` Pydantic class shape; knot stores the runtime values and threads them into the `ctx` as pure data.

For ER, typical config shape (example from MovieResolver above):

```python
class Config(BaseModel):
    blocking: list[Slot]                  # real Slot refs
    matching: list[Slot]
    cross_references: list[OntologyClass] = []
    threshold: float = 0.85
    transitivity: Transitivity = Transitivity.WEAKEST_LINK
    mode: ERMode = ERMode.FULL
    conflict_policy: Policy = Policy.SURFACE
    split_policy: Policy = Policy.SURFACE
```

Config is editable at runtime via `POST /impls/<id>/config` — no redeploy. Changing config bumps the workflow compile hash; cross-validation enforces that any slot referenced in config is reachable through one of the impl's declared DataContexts (per `di-input-contract.md`).

Different impls for the same class are fine — register a new impl, bind it. The active binding at compile time is pinned into the run.

### Full vs incremental mode

ER supports both **full** and **incremental** runs. The mode lives in the impl's config (runtime-editable):

- **Full mode** — the bound impl receives all per-source-facts for the class as of the pinned input hashes. Recompute every cluster from scratch.
- **Incremental mode** — the bound impl receives only rows that are new or changed since the last successful ER run for this class (delta against the previous run's source watermark). Existing canonical_ids and bindings are inputs; the impl matches new rows against existing canonical entities, mints new canonical_ids only for genuinely new entities, and may emit edges that trigger merges with existing canonical_ids.

Incremental ER is enabled by knot's bookkeeping: previous run completion times, source watermarks, the SCD2 `entity_bindings` table (which carries valid_from / valid_to per binding so historical state is queryable). Knot has the structural information; the DI can opt in.

**Default is full.** Incremental is opt-in per impl because it requires the DI to handle delta semantics correctly (matching new rows against existing canonical entities, not just within the new batch). DIs that prefer simplicity always pick full.

If a DI's matching algorithm is naturally full-only (e.g., a global graph clustering algorithm), it stays full. If a DI does its own caching internally — taking full input every run but diffing client-side — that's also fine; knot doesn't care which path the impl picks as long as the inputs match the declared mode.

### Knot's post-processing

After the impl returns its edges, knot:

1. **Apply user ER decisions.** Forced edges and forced non-edges from `_user_er_decisions` (see below) are merged into the impl's edge set with confidence=1 and a manual flag.
2. **Apply threshold** (per the impl's config) — drop edges below the configured confidence.
3. **Build connected components** via the chosen transitivity rule (weakest-link, strong-link, average-confidence — per the impl's config).
4. **Reconcile each component against existing canonical_ids — four cases:**
   - **All-new.** No row in the component has a prior canonical_id. Knot mints a new deterministic canonical_id (same mechanism as ingestion-time IDs).
   - **Partial-existing.** Some rows already have a canonical_id; new rows inherit it.
   - **Conflict.** Two or more rows in the component have *different* prior canonical_ids — ER says they should merge. **Default: surface for review.** Per-impl opt-in to auto-merge by picking a survivor (older canonical_id wins; ties broken lexicographically); the absorbed canonical_id gets a redirect record in `canonical_id_lineage`.
   - **Split.** A prior cluster is now two clusters in the new ER run — some rows previously bound to canonical_id X are now in a separate component. **Default: surface for review.** Per-impl opt-in to auto-split: original canonical_id keeps the larger cluster; split-off cluster gets a fresh canonical_id; lineage event recorded.
5. **Persist bindings.** Update `entity_bindings` (SCD2; see below). Append to `canonical_id_lineage` for any merge/split events.
6. **Audit.** Per ER run: edges received (including manual edges from user decisions), threshold + transitivity decisions, resulting clusters, canonical_id assignments, lineage events.

**Both conflict and split policies follow the same posture:** stability by default; impls opt in to auto-applying. The reasoning is symmetric — both are cases where ER changed its mind about the cluster shape, and the question is whether knot trusts ER's revised opinion enough to silently adjust canonical_ids.

**Base/row-level knot IDs are never overwritten.** They are assigned deterministically at ingestion and stable across runs. Canonical entity IDs are minted on top of row IDs; canonical_ids may be retired or split (with audit), but knot row IDs are forever.

### User-provided ER decisions

Users can assert ER decisions that override or supplement the bound impl's scoring:

- **Forced edges** — "merge these two rows" — `(row_a, row_b, asserted_by, asserted_at, reason?)`.
- **Forced non-edges** — "do NOT merge these two rows" — same shape.

These live in postgres-control as `_user_er_decisions` (control-plane data, not lake data; user-authored, low-volume, spec-shaped). The bound ER impl reads them at compile time alongside its own data; they merge into the edge set at confidence=1 with a `manual` flag during knot's post-processing.

For the lifecycle of user assertions (T1 assertion → T2 next-pipeline-run migration), and the parallel query-time overlay for property corrections, see `pipeline-stages.md` § "In-flight corrections overlay."

User decisions enter the audit chain: any merge or split driven by a forced edge/non-edge records `at_run_hash` + the user assertion id, so audit walk-back can answer "this canonical_id was split because user X asserted a non-edge on date Y."

This bypasses the "should we honor splits/merges by default?" question for user-driven changes — they're authoritative by construction.

### Canonical_id lifecycle and data model

The data model that supports merge / split / audit:

**`entity_bindings/<Class>`** — SCD2 table, source of truth.

```
knot_row_id | canonical_id | valid_from | valid_to | at_run_hash | change_type
123         | mov_y7p4     | run1       | run3     | 9876ab...   | mint
123         | mov_x9k2     | run3       | NULL     | abcdef...   | merge_into
```

- Each rebind = UPDATE old row's `valid_to` + INSERT new row.
- Current binding for a knot_row_id: `WHERE knot_row_id=X AND valid_to IS NULL`.
- Historical binding at time T: `WHERE valid_from <= T < COALESCE(valid_to, +∞)`.

**`canonical_id_lineage`** (optional audit convenience) — append-only event log.

```
old_id     | new_id    | change_type | at_run_hash | at_timestamp           | strategy_revision
mov_y7p4   | mov_x9k2  | merge       | abcdef...   | 2026-04-15T10:00:00Z   | movie_v3
mov_x9k2   | mov_z4q1  | split       | 1234cd...   | 2026-05-15T14:22:00Z   | movie_v4
```

Strictly redundant with `entity_bindings` (lineage is derivable), but useful for human-readable audit queries — "who merged X into Y, by which run, with which strategy."

**`resolved_facts/<Class>`** — derived. `per_source_facts/<Class>` JOIN `entity_bindings/<Class> WHERE valid_to IS NULL`. Implementation choice: query-time view OR materialized snapshot per merge run.

How the multi-valued contribution bag is reduced to a single value at query time — the `ResolutionPolicy` enum, per-slot `resolution_policy` field, and protocol-level `disagreement_stance` that governs whether a trust-resolution CTE is attached — is owned by `staging/multi-valued-semantics.md`. The storage layout above (`per_source_facts`, `resolved_facts`, `entity_bindings`) remains the source of truth for what is written to the lake; the reduction semantics are separate.

**Resolution from old canonical_id to current — single SQL, no chain walking.**

The "what is `mov_y7p4` now?" question doesn't require recursive CTE or iterative queries:

```sql
SELECT DISTINCT eb_now.canonical_id
FROM entity_bindings eb_old
JOIN entity_bindings eb_now ON eb_old.knot_row_id = eb_now.knot_row_id
WHERE eb_old.canonical_id = 'mov_y7p4'
  AND eb_now.valid_to IS NULL;
```

Even through multiple merge / split events, the answer falls out — the chain is implicit in `entity_bindings` via the stable `knot_row_id` anchor. Splits naturally return multiple current canonical_ids (one per cluster).

In rare deep-chain cases where you want to walk the lineage explicitly (audit storytelling), recursive CTE or iterative client queries work — but it's optimization, not correctness.

### Cross-references via the `Identifier` class

Cross-references (TMDb's `imdb_id`, IMDb's nconst, Wikidata QIDs, etc.) reify as `Identifier` — a first-class ontology class. An `Identifier` fact says: "this entity carries an identifier of `value` in `system`."

Knot-internal storage:

```
per_source_facts/Identifier/source=tmdb_movies/
  knot_row_id  src_key  entity_class  entity_src_key  system  value         asserted_at
  ...          ...      Movie         238             imdb    tt0068646     ...
  ...          ...      Movie         238             tmdb    238           ...   ← source's own ID is also an Identifier fact
```

The impl accesses Identifier facts via a declared DataContext — e.g., `DataContext(primary=Identifier, filter=Identifier.entity_class == Movie)` as its own attribute, or by including them on a primary-class DataContext (`include=[Movie.identifiers]`). Cross-source `(system, value)` agreement is strong-evidence input to its scoring.

**Why reify** (applies the `reification-strategy.md` rule):

- **Lifecycle.** Sources revise identifier claims (TMDb correcting an `imdb_id`; IMDb merging or splitting entries upstream).
- **Properties.** An identifier assertion has attributes — when asserted, by whom, whether verified.
- **Provenance.** Each Identifier fact carries which source contributed it.
- **ER on identifiers themselves.** Two sources agreeing on `(system, value)` are agreeing on identifier-identity, strengthening the entity-level binding.

Identifiers earn their own class on all four counts. Same machinery as every other reified relation.
