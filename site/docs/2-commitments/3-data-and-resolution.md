# 3. Data and resolution

Three commitments — 7, 8, and 13 — describe how knot represents canonical data and reconciles disagreement across sources, time, and human input. Each one resolves a specific Layer 1 hard problem. Together they make audit walk-back queryable as data — no sidecar, no parallel meta-graph.

---

## Commitment 7 — Multi-valued canonical facts. Trust resolution is query-time. SDK type follows protocol stance.

At the canonical layer every property is **implicitly multi-valued** — one contribution per source. `Movie.year` for a canonical entity is a set: `[{source, value, asserted_at}, ...]`. The merge stage does **not** write a "winner" column. Default-value selection happens at query time via a trust-resolution CTE knot rewrites pre-execution, using the `resolution_policy` declared on each `Slot` (default: `ARGMAX_TRUST`).

Which SDK type a slot reference receives depends on the protocol's `disagreement_stance`. Under **`RESOLVED`** protocols (Materializer, Translator, ConstraintEvaluator, DerivationEvaluator), slots are `Resolved[T]` — comparison operators work, bare `Movie.year > 1900` compiles. Under **`DISAGREEMENT_AWARE`** protocols (ERProtocol, DqRunner), slots are `MultiValued[T]` — bare comparison is a type error; callers spell their reduction (`from_source(...)`, `all_()`, `winner()`). The canonical statement with per-protocol assignment and the `ResolutionPolicy` enum is in [`design/staging/multi-valued-semantics.md`](../../../design/staging/multi-valued-semantics.md).

### Rationale

Layer 1 ([Hard problem 1](../1-why/the-hard-problems.md#hard-problem-1--multi-source-disagreement-is-information-not-a-bug)) establishes the structural fact: for any property of any canonical entity, the truthful representation is a multi-set of `(source, value, asserted_at)` tuples. Compressing it any earlier than absolutely necessary destroys evidence.

Two follow-on problems:

1. **Where does the multi-set get compressed into a single value, if it ever does?** A consumer asking for `Movie.year` doesn't want a list back. Something has to pick a default. The "where" determines whether trust adjustments require re-running the pipeline.
2. **What if the trust policy changes?** If the merged value was written into a column at pipeline time, every fact in the graph is now stale until the pipeline re-runs. If the multi-set is preserved and the default is chosen at query time, the trust edit propagates with no re-run.

Trust is per `(source, property)`, not per source ([`design/trust-and-merge.md`](../../../design/trust-and-merge.md) §"Trust is per `(source, property)`, not per source"). IMDb is excellent for `runtime`, mediocre for `release_date`. TMDb is strong for `franchise`, weaker for `genres`. A single source-level trust number is the toy model; the working unit is `(source, property)`.

Multi-valued canonical facts with query-time trust resolution is the answer that satisfies both. Trust adjustments don't require re-running merge. Lineage lives in the data; no separate `AuditChain` table. Audit walk-back is queryable as data — `Movie.year.all_()` *is* the lineage.

The protocol-kind distinction adds a second guarantee: ER and DQ cannot silently compare trust-winners where they should compare source bags. `MultiValued[T]` makes the comparison a type error; the impl writer is forced to be explicit about which reduction they want.

### Comparator anchor

| System | Canonical-fact representation | Trust / merge timing |
|---|---|---|
| **Property graphs (Neo4j)** | One value per property per node | Single-valued by construction; merges happen upstream. |
| **RDF triple stores** | Multi-statement; each `(s, p, o)` independent | Native multi-valued; trust is bolted on (named graphs / reification). |
| **MDM tools (Reltio, Tamr)** | Golden records with survivorship rules at write time | Survivorship at merge; trust edits require re-run. |
| **Splink** | Pairwise linkage + cluster output; doesn't model property merge | Property-merge is downstream of Splink. |
| **dbt + warehouse** | Single-value via `COALESCE` ordering or `MAX(CASE WHEN ...)` | Trust is in the SQL; trust edits are SQL edits + re-run. |
| **knot** | Multi-valued at canonical layer; one row per `(source, property, asserted_at)` | Default at query time via trust-resolved CTE. |

The closest neighbor is RDF with named graphs (each statement carries provenance). RDF gets the multi-valued primitive right; what it doesn't carry is per-`(source, property)` trust as a structured first-class concept that resolves at query time. MDM tools (Reltio, Tamr — *inferred* on survivorship-at-merge timing; behavior consistent with general MDM literature) are the anti-pattern: golden records compute the winner at merge, requiring re-run on trust edits.

### Tradeoffs / honest costs

- **Default reads cost a trust-CTE.** Every read of `Movie.year` runs a trust-resolved CTE; knot rewrites it at query time. The performance cost is real and grows with multi-sourced entities. The architecture is materialization-free at the merge layer; if read performance matters more than trust-edit propagation for a specific consumer, that consumer's bound materialization impl can write a winner column for itself (per [Commitment 15 in 4-quality-and-publish.md](4-quality-and-publish.md)).
- **The trust model itself has open mechanics.** Beta-Bernoulli framing is settled per [`design/trust-and-merge.md`](../../../design/trust-and-merge.md), but the observation signal (what counts as a win/loss for an arm), non-stationarity, and the query-time use of the posterior (mean? Thompson sample? pessimistic mean?) are open. Layer 1 flagged these; they remain open here. They don't block the multi-valued framing.
- **Three access modes is more than one.** Default / `from_source(...)` / `all()` is a learning surface. The team has to know to call `.all()` for audit walk-back. The audit promise dissolves the alternative (a separate `AuditChain` table); the cost is a richer SDK.
- **Tie-breaks are policy.** Equal trust on a property happens. Defaulting to recency is probably right; the choice is real and configurable.

??? details "Deep-dive: SDK access modes, protocol stance, and the trust-resolved CTE"

    From [`design/staging/multi-valued-semantics.md`](../../../design/staging/multi-valued-semantics.md):

    Under `RESOLVED` protocols, the default bare access resolves via the slot's `resolution_policy`:

    | Access | Returns | What knot does |
    |---|---|---|
    | `Movie.year` | Resolved via `resolution_policy` (default: `ARGMAX_TRUST`) | Trust-CTE attached |
    | `Movie.year.from_source("imdb")` | That source's contribution | No CTE |
    | `Movie.year.all_()` | All contributions: `[{source, value, asserted_at}, ...]` | No CTE |
    | `Movie.year.contributions()` | Raw `list[Contribution[int]]` | No CTE |

    Under `DISAGREEMENT_AWARE` protocols, `Movie.year` is `MultiValued[T]` and bare `Movie.year > 1900` is a type error:

    | Access | Notes |
    |---|---|
    | `Movie.year.from_source("imdb") > 1900` | Specific source |
    | `Movie.year.all_() > 1900` | Forall |
    | `Movie.year.winner() > 1900` | Explicit opt-in to resolved behavior |

    SQL emit pattern, per [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) §"Trust-resolution placeholder":

    ```sql
    WITH __trust_resolved__Movie AS (
        SELECT pk, slot, value, source, asserted_at
        FROM <resolved_facts>
        -- ^ placeholder body; knot's runtime rewrites this before execution
    )
    SELECT ...
    FROM __trust_resolved__Movie
    WHERE ...
    ```

    Contract:

    - CTE name format: `__trust_resolved__<ClassName>`.
    - Placeholder body is valid SQL runnable as-is (against the unresolved `resolved_facts` view) so unit tests and golden snapshots work without a trust-resolution runtime in scope.
    - `KnotSQL.trust_cte_names: list[str]` lists all trust CTE names; knot's runtime enumerates this and rewrites each CTE body via sqlglot AST manipulation before dispatching to `QueryReader`.
    - `from_source(...)` and `.all()` query `resolved_facts` directly and do NOT emit trust CTEs.

    Mechanics, from [`design/trust-and-merge.md`](../../../design/trust-and-merge.md):

    - Trust is `Beta(α, β)` per `(source, property)` arm.
    - Prior is spec (steward-set, versioned, auditable).
    - Posterior is derived data (updated from observations; own audit chain).
    - At each merge run, the trust state `(α, β)` per arm is snapshotted alongside the compiled workflow. Replaying yesterday's merge fetches yesterday's snapshot.
    - The merge stage writes nothing other than `entity_bindings` (canonical_id linking) + the trust-state snapshot. No "winner" column.

    The merge stage's role (per [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md)):

    > Merge does not "write winners" — it ensures canonical_id linking is current and trust state is consistent. The actual default-value selection happens at query / display time, inside knot.

### Cross-links

- [`design/staging/multi-valued-semantics.md`](../../../design/staging/multi-valued-semantics.md) — canonical statement: protocol stance, `ResolutionPolicy` enum, SQL reductions, lens-correction interaction.
- [`design/trust-and-merge.md`](../../../design/trust-and-merge.md) — trust model and merge semantics.
- [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md) §"Multi-valued canonical facts".
- [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) §"Trust-resolution CTE — per-policy reductions".
- [`1-why/the-hard-problems.md`](../1-why/the-hard-problems.md#hard-problem-1--multi-source-disagreement-is-information-not-a-bug) — Hard problem 1.

---

## Commitment 8 — Cross-class pinning. Relation classes pin parent run hashes at compile.

A class whose slots' ranges reference other classes (a "relation class") pins its parents' specific run hashes into its compiled spec. Reads parent canonical_ids "as of" those runs, not "current." Knot's runtime applies the temporal lens automatically; the impl writes plain ontology expressions.

### Rationale

Layer 1 ([Hard problem 3](../1-why/the-hard-problems.md#hard-problem-3--relation-classes-pin-parent-state-not-current)) establishes the problem: a `Credit` connects a `Movie` to a `Person`. Credit-shaped facts depend on Movie's canonical_ids and Person's canonical_ids. If Movie's ER runs again *after* Credit was last computed and merges two movies, every Credit row that referenced one of those movies now points at a `canonical_id` that was retired.

Two wrong answers, both tempting:

- **Always read "current" parent canonical_ids.** Credit's view of the world drifts whenever Movie's ER changes, even though Credit didn't run. Replay of yesterday's Credit run no longer reproduces yesterday's edges.
- **Eagerly cascade.** When Movie's ER produces a merge, recompute every dependent Credit row immediately. Expensive; couples downstream classes to upstream cadence; turns a small ER edit into a fan-out re-run.

The correct shape: **relation classes pin their parents' run hashes at compile time**, and read parent canonical_ids "as of" those pinned runs. Credit running today, with Movie's pinned run from yesterday, sees yesterday's canonical_ids. Replay is deterministic. The audit walk-back crosses class boundaries with no ambiguity: "what state were Movie's canonical_ids in when Credit ran?" answers via the pinned Movie run hash.

The impl writer never thinks about historical revisions. Knot's runtime serves the right data through the SCD2 `entity_bindings` + `canonical_id_lineage` layer ([`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md)).

### Comparator anchor

| System | Cross-class temporal handling | Granularity |
|---|---|---|
| **dbt** | `ref('upstream_model')` + `{{ var('snapshot_date') }}` for time-traveled tables | Table-level. Manual snapshot cadence. |
| **Iceberg / Delta time travel** | `AS OF` snapshot reads | Snapshot-level. |
| **Bitemporal databases** | `valid_time` + `transaction_time` per row | Row-level. |
| **MDM tools** | Survivorship rules at merge; downstream reads "current" | No cross-class temporal. |
| **knot** | Pinned parent run hashes baked into the relation-class compile hash | Per-canonical_id, transparent to the impl writer. |

dbt's `ref()` macro is the closest neighbor on the cross-table dependency axis but operates at table granularity; cross-class temporal pinning at canonical-ID granularity is something dbt approximates manually with snapshot tables. Iceberg time travel is a primitive knot's executor uses but doesn't replace the architectural commitment — knot pins the run hash at compile, then resolves "as-of" at read.

### Tradeoffs / honest costs

- **Cross-class scheduling is the orchestrator's job, but knot picks the pin.** The split — orchestrator schedules, knot pins — is a real coordination point. A team triggering Credit out-of-band has to know that knot defaults to "latest successful Movie run + latest successful Person run"; explicit override via `?parents=...` is available for replay / A/B comparisons.
- **Re-running a parent doesn't invalidate child runs.** This is the right answer for replay determinism, but it means a team that intends "Credit should reflect the latest Movie state" has to trigger Credit explicitly. A meta-workflow at the orchestrator level handles "rebuild whole KG" cases.
- **The compile hash includes pinned_parent_runs.** A relation-class run with default pinning today vs default pinning tomorrow produces different compile hashes (because the parent hashes differ). This is the right behavior for audit but means default-pinned Credit hashes will proliferate as Movie / Person re-run.
- **The "ER dependency graph" plus topological sort is real machinery.** Cross-class pinning handles structural parent dependencies; the topological sort over the entire ontology graph (per [`design/staging/cross-class-pinning.md`](../../../design/staging/cross-class-pinning.md) §"ER dependency graph and topological order") handles strategic dependencies declared via impl config (e.g., Movie's ER uses Identifier facts as a signal). Cycles are spec errors caught at compile time. Honest engineering work, not free.

??? details "Deep-dive: pinned_parent_runs and the ER dependency graph"

    From [`design/staging/cross-class-pinning.md`](../../../design/staging/cross-class-pinning.md):

    The compiled spec for a relation class carries:

    ```yaml
    class: Credit
    pinned_parent_runs:
      Movie:
        run_hash: 9876ab...
        run_id: <pipeline_runs.id>
        completed_at: 2026-04-30T18:22:11Z
      Person:
        run_hash: 0000zz...
        run_id: <pipeline_runs.id>
        completed_at: 2026-04-30T17:05:42Z
    ```

    Default at compile: most-recent-successful run of each parent class. Override: `POST /runs/Credit?parents={Movie:hash_X,Person:hash_Y}`. Both produce different compile hashes — `pinned_parent_runs` is part of the canonical form (per [`design/compiled-workflow-hashing.md`](../../../design/compiled-workflow-hashing.md)).

    Toposort over the ER dependency graph:

    ```
    Identifier      (no deps — clusters on (system, value) alone)
        ↓
    Movie, Person   (parallelizable; both depend on Identifier strategically)
        ↓
    Credit          (depends on Movie + Person structurally; pins their run hashes)
    ```

    Two edge types:

    - **Structural dependencies (mandatory)** — relation class references parent classes via reference patterns. From `compute_spec_reference_graph`. Cross-class pinning enforces these at compile time.
    - **Strategic dependencies (optional)** — declared via impl config. From the ER impl's `cross_references` config field. Strategic edges extend the graph for ER ordering.

    Cycles are spec errors. Knot rejects "ER strategy dependency cycle" at compile time. No fixed-point iteration; no runtime loop.

    Merge propagation, per [`design/staging/cross-class-pinning.md`](../../../design/staging/cross-class-pinning.md) §"Merge propagation across dependent classes":

    ```
    T1: Identifier run A — produces id_X, id_Y (separate identifiers)
    T2: Movie run M1 — pins Identifier run A; sees id_X and id_Y as different
    T3: Identifier run B — auto-merges id_X + id_Y into id_X
    T4: Movie run M2 — fresh compile pins Identifier run B; sees id_X as merged;
                       SCD2 entity_bindings transparently redirect references
                       to retired id_Y → id_X
    ```

    The DI ER impl never sees merge history. Knot's runtime serves bindings post-merge state at the pinned hash through SCD2 + `canonical_id_lineage`. Composition: DI ER + cross-class pinning + SCD2 + lineage are layers; the DI doesn't need to know about the latter three.

### Cross-links

- [`design/staging/cross-class-pinning.md`](../../../design/staging/cross-class-pinning.md) — canonical statement.
- [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md) §"Canonical_id lifecycle and data model" — SCD2 entity_bindings + canonical_id_lineage.
- [`design/compiled-workflow-hashing.md`](../../../design/compiled-workflow-hashing.md) — `pinned_parent_runs` in the canonical form.
- [`1-why/the-hard-problems.md`](../1-why/the-hard-problems.md#hard-problem-3--relation-classes-pin-parent-state-not-current) — Hard problem 3.

---

## Commitment 13 — In-flight corrections via dedicated source.

User corrections (and additions) live in postgres-control briefly, then migrate into the lake at the next pipeline run via a dedicated `_user_corrections` source. From migration onward they're regular lake data treated as a high-trust source by the trust model. Knot's built-in lake query endpoint overlays postgres on top of lake at query time **only** for consumer-facing reads — closing the T1→T2 gap so consumers see fresh values immediately. Bound `Translator` impls apply the same overlay for materialized non-lake targets (Neo4j, Neptune, vector stores, etc.) that can't be queried via knot's built-in lake SQL path. **Pipeline impls never see this overlay.** DataContext views are pure lake reads at the pinned moment. Reproducible by construction.

### Rationale

Layer 1 ([Hard problem 4](../1-why/the-hard-problems.md#hard-problem-4--corrections-must-be-immediate-for-consumers-and-reproducible-for-impls)) establishes two requirements that pull against each other:

1. **Immediate.** The correction must be visible to the next consumer query, not at the next pipeline run.
2. **Reproducible.** When the pipeline next runs and replays workflows for audit, the correction must flow through the same pipeline machinery as any other source's data — not be a special override that bound impls have to know about.

The two-overlay model resolves the tension:

- A **query-time overlay** for consumer-facing reads. Lake data + pending postgres corrections, merged in the SDK runtime at the moment of read.
- A **migration into the lake** at the next pipeline run via the dedicated `_user_corrections` source. From that point on the correction is regular lake data with full provenance.

The non-obvious property: **bound impls (ER, DqRunner, materialization, etc.) never see the query-time overlay.** Their `DataContext` views are pure lake reads at the pinned moment. If they saw the overlay, replay would be non-deterministic — a workflow run yesterday would see the overlay state of today. Hiding the overlay from impls is what keeps replay deterministic.

The mirror principle for ER decisions ([forced edges and forced non-edges](../1-why/the-hard-problems.md#hard-problem-4--corrections-must-be-immediate-for-consumers-and-reproducible-for-impls)): a query-time overlay can paper over a property value disagreement; it cannot paper over identity. Forced edges affect ER, not value resolution; they're meaningful only at the next ER run.

### Comparator anchor

| System | Correction model | Replay determinism |
|---|---|---|
| **MDM tools (Reltio, Tamr)** | Curate / steward UI; corrections affect the golden record | *Inferred*: typically not bit-replayable; corrections compute into survivorship at write. |
| **Schema registries** | Schema evolution events | Not a correction primitive. |
| **dbt + manual override table** | Override table joined into the merge SQL | Pipeline branches on "is this an override row"; replay includes the override table at its current state. |
| **CDC + materialized view** | Read-through overlay typical | Non-deterministic for downstream replay if the overlay state shifts. |
| **knot** | Two overlays: query-time for consumers, lake migration for pipeline. Bound impls never see the query-time overlay. | Deterministic by construction. |

The closest *anti-pattern* is the dbt override table: it works, but the pipeline has to know about a separate path. Knot's commitment is that corrections are just another source — same trust machinery, same multi-valued canonical view, same audit walk-back. The query-time overlay is purely a gap-closer for the T1→T2 window; once at T2, the correction is regular lake data.

### Tradeoffs / honest costs

- **Two overlay paths exist.** The translator's query-time overlay is consumer-facing; the lake migration at next pipeline run is impl-facing. The team has to remember which path applies where. Document loud; debug less often.
- **Forced ER decisions don't have a query-time overlay.** A user asserting "merge these two" or "do not merge these two" only takes effect at the next ER run. Consumers querying immediately after a forced edge will see the old canonical_id structure until the next ER run picks it up. If immediate effect is needed, the user re-triggers the affected per-class run; the new run reads `_user_er_decisions` from postgres and applies the assertions during clustering.
- **Postgres staging adds a small operational surface.** `_user_corrections` and `_user_er_decisions` are postgres-control tables with their own retention policy. The team owns the migration cadence (run-at-T2) and the staging cleanup (mark `applied_to_lake=true` or delete per retention).
- **Trust-model implication.** `_user_corrections` is treated as a high-trust source (e.g., 0.95 in [`design/trust-and-merge.md`](../../../design/trust-and-merge.md)). A team that wants tiered correction trust (junior steward 0.7, senior steward 0.9) models multiple correction sources rather than one. The trust model handles this naturally; the team has to decide.

??? details "Deep-dive: lifecycle and the two overlays"

    From [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"In-flight corrections overlay":

    Lifecycle:

    - **T1 — assertion.** User submits via knot's API. Data lands in postgres-control:
      - Property corrections → `_user_corrections` table (per-source-facts-shaped row).
      - ER decisions → `_user_er_decisions` table (forced edge or non-edge).
    - **T1 to T2 — in-flight.** Data lives in postgres only. The lake's `resolved_facts` doesn't reflect it yet.
    - **T2 — next pipeline run picks it up.** The `_user_corrections` source's `normalize` step reads postgres → emits `per_source_facts` rows → ER consumes them. ER decisions applied as forced edges / non-edges in the same run.
    - **T2+ — the correction is in the lake.** Overlay no longer needs it.

    Query-time overlay (corrections only):

    ```mermaid
    flowchart LR
        consumer[Consumer query<br/>Movie.runtime] --> sdk[Knot SDK runtime]
        sdk --> lake_sql[Run SQL against lake<br/>resolved_facts]
        sdk --> pg_query[Query postgres<br/>_user_corrections]
        lake_sql --> merge[Merge in Python:<br/>lake + postgres = full set]
        pg_query --> merge
        merge --> mode[Apply SDK access mode<br/>default / from_source / all]
        mode --> ret[Return]
    ```

    Bound impls never see this federation. From the impl's perspective, it just calls `Movie.runtime` and gets the right answer. Hidden inside knot's SDK runtime.

    ER decisions: no query-time overlay. Forced edges / non-edges affect ER, not value resolution. Meaningful only at the next ER run.

    From [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided":

    > In-flight corrections do not overlay DataContext views. Pipeline-stage impls never see this overlay. DataContext views are pure lake reads at the pinned moment — no overlay logic, no special-case branches, fully reproducible.

    Trust treatment, from [`design/trust-and-merge.md`](../../../design/trust-and-merge.md):

    > A human override is **not** a special case in the pipeline. It's a row contributed by a `_user_corrections` source at very high `(source, property)` trust. Same per-source-facts table, same multi-valued canonical view, same query-time trust resolution. The correction surfaces as the default because its trust dominates — not because the pipeline branches on "is this a correction."

### Cross-links

- [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"In-flight corrections overlay" — lifecycle and the two overlays.
- [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided" — bound impls never see the query-time overlay.
- [`design/trust-and-merge.md`](../../../design/trust-and-merge.md) — corrections as a high-trust source.
- [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md) §"User-provided ER decisions".
- [`1-why/the-hard-problems.md`](../1-why/the-hard-problems.md#hard-problem-4--corrections-must-be-immediate-for-consumers-and-reproducible-for-impls) — Hard problem 4.

---

## Open tensions on this page

- **Trust-evolution mechanics are open.** The Beta-Bernoulli framing is settled (commitment 7's deep-dive); the observation signal, non-stationarity, and the query-time use of the posterior are explicitly open per [`design/trust-and-merge.md`](../../../design/trust-and-merge.md). These are flagged in Layer 1 as well; they remain open at the commitment level. They don't block the framing.
- **The bound-impl invisibility of the correction overlay is non-obvious for new authors.** A team writing a new ER impl might assume "user corrections should affect ER pairwise scoring at query time" — but the architecture says no, ER reads pure lake at the pinned moment, corrections affect ER only at T2 via the `_user_corrections` source's normalize step. This needs surfacing in impl-author documentation; flagging as a tension because the architecture is right but the cognitive model takes work.
- **Cross-class pinning + corrections interaction.** A correction asserted today affects T2 (next pipeline run) for the corrected class; relation classes pinning that class will pick it up via the standard pinned-parent-runs mechanism on their next compile. The chain works; worth flagging that "when does my Credit run see this correction" is a function of (a) when Movie next runs after T2, (b) when Credit next compiles after that. No bug; just operational reality.
