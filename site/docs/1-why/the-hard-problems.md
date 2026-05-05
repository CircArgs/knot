# The hard problems

What makes this domain not trivial. Each of the problems below has a known solution in isolation. The hardness is that they compound — and any architecture that solves one of them well at the cost of another is wrong for the team's situation.

---

## One-paragraph summary

Knowledge-graph construction from heterogeneous sources is hard because **(a)** sources disagree about properties of the same canonical entity, and the disagreement is itself information; **(b)** entity resolution decides what counts as "the same entity," is sometimes wrong, and is revised over time without losing history; **(c)** trust is per-`(source, property)` and edits to trust must change query results without re-running the pipeline; **(d)** corrections from humans must take effect immediately for consumers but flow through the lake to remain reproducible; **(e)** every fact in the graph must trace back deterministically to the spec revisions, run hashes, ER decisions, trust state, and corrections that produced it; and **(f)** the ontology is itself live data — runtime-edited, programmatically authored — so the spec is also part of what audit walks back through. The first five problems are individually solved by various existing tools. The combination, with `(f)` layered on top, is what no existing tool covers.

---

## At a glance

| # | Hard problem | Why it's hard |
|---|---|---|
| [1](#hard-problem-1-multi-source-disagreement-is-information-not-a-bug) | Multi-source disagreement is information, not a bug | The naive merge picks a winner and loses the disagreement. The truthful representation is a multi-set of `(source, value, asserted_at)`; compressing it early destroys evidence. A trust edit must change query results without re-running the pipeline. |
| [2](#hard-problem-2-entity-resolution-is-wrong-sometimes-and-the-wrongness-has-lifecycle) | ER is wrong sometimes, and the wrongness has lifecycle | False merges and false splits are routine. Old canonical_ids must remain queryable; merge / split events need provenance with strategy-revision attribution; downstream replay must work at the prior ER state. |
| [3](#hard-problem-3-relation-classes-pin-parent-state-not-current) | Relation classes pin parent state, not "current" | A Credit must read Movie's canonical_ids "as of" a pinned run, not "current," or replay drifts whenever any parent class re-runs. Audit walk-back must cross class boundaries with no ambiguity. |
| [4](#hard-problem-4-corrections-must-be-immediate-for-consumers-and-reproducible-for-impls) | Corrections immediate for consumers, reproducible for impls | A correction must be visible at the next consumer query, flow through the lake at the next pipeline run, and never poison replay determinism for bound impls. The two requirements pull against each other. |
| [5](#hard-problem-5-audit-walk-back-is-the-load-bearing-user-benefit) | Audit walk-back is the load-bearing user benefit | Every contributing artifact (spec rev × impl rev × config × source watermark × ER decision × trust state × correction overlay) must be pinned at run time and walked from a single content-addressed identity. Trigger-based identity drifts. |
| [6](#hard-problem-6-the-ontology-is-itself-live-data) | The ontology is itself live data | Spec edits are runtime, not code-deploys. The spec graph is content-addressed alongside everything else. The publish gate must validate Pydantic parse, reference resolution, DataContext cross-checks, and impact preview atomically — at runtime, not in CI. |

The compounding (the diagonal) is at the bottom of this page in [Why these compound](#why-these-compound).

---

## Hard problem 1 — multi-source disagreement is information, not a bug

The naive shape:

```sql
SELECT canonical_id, ANY_VALUE(year) AS year
FROM source_data
WHERE entity_type = 'Movie'
GROUP BY canonical_id;
```

This loses the disagreement. Two months later, when a consumer asks "why does this movie's year flip between 1999 and 2000 depending on which dashboard I look at?", there is no record that the disagreement existed.

The slightly less naive shape:

```sql
SELECT canonical_id, MAX(year) AS year, MIN(year) AS year_alt
FROM source_data
GROUP BY canonical_id;
```

Still wrong. The disagreement is now compressed into two columns and the *attribution* (which source said what, when) is gone. Three sources disagreeing produce the same shape as two, and a fourth source agreeing with one of the others is silently absorbed.

The actual structural fact: **for any property of any canonical entity, the truthful representation is a multi-set of `(source, value, asserted_at)` tuples.** Compressing it any earlier than absolutely necessary destroys evidence.

Once that's accepted, two follow-on hard problems open:

1. **Where does the multi-set get compressed into a single value, if it ever does?** A consumer asking for `Movie.year` does not want a list back. Something has to pick a default. The "where" determines whether trust adjustments require re-running the pipeline.
2. **What if the trust policy changes?** Last month criterion-source was the highest-trust contributor for `runtime`. This month, after a vendor regression, it isn't. If the merged value was written into a column at pipeline time, every fact in the graph is now stale until the pipeline re-runs. If the multi-set is preserved and the default is chosen at query time, the trust edit propagates with no re-run.

This is why **multi-valued canonical facts with query-time trust resolution** is non-obvious but necessary. The naive design (write the winner) trades a small amount of read-time computation for the ability to keep the graph correct as trust evolves. That trade is wrong for this domain.

!!! note "Read for depth"
    [`design/trust-and-merge.md`](../../../design/trust-and-merge.md) covers the per-`(source, property)` trust model, the corrections-as-just-another-source flattening, and the Beta-Bernoulli bandit for trust evolution.

---

## Hard problem 2 — entity resolution is wrong sometimes, and the wrongness has lifecycle

ER says: "this IMDb row, this TMDb row, and this Wikidata row are all the same Movie." It assigns them a single `canonical_id`. Downstream consumers depend on that ID.

ER is wrong in two flavors:

- **False merge.** Two distinct Movies got assigned the same canonical_id. A future ER run, or a human reviewer, splits the cluster.
- **False split.** Two source rows that were the same Movie were assigned different canonical_ids. A future ER run merges them.

Both are routine. Both demand:

1. Old canonical_ids cannot just disappear — anything that referenced `mov_y7p4` last week needs to be able to ask "what is this entity now?" and get a deterministic answer.
2. Audit must record which run made the merge or split, with which strategy, on what evidence.
3. Downstream materializations must be re-derivable at the prior ER state for replay / debugging.

This means **canonical_ids have lineage**, not just identity. A merge event records `(old_id, new_id, at_run_hash, strategy_revision)`. A split event records the inverse. Resolving "what does `mov_y7p4` map to *now*?" must work without recursive CTE walks for typical cases — the data model has to make that question single-SQL-statement cheap.

The structural anchor that makes this tractable is **stable row-level IDs that ER never overwrites**. ER mints canonical_ids on top of row IDs; canonical_ids may be retired or split (with audit), but the underlying row IDs are forever. The lineage chain is implicit in the row → canonical mapping over time, not a hand-maintained linked list. See [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md).

The non-obvious part: **human-asserted ER decisions** (forced edges, forced non-edges) participate in this same lineage. A human saying "these two are not the same" must produce the same mechanical merge / split events as an algorithmic ER decision, with the human assertion attributed.

---

## Hard problem 3 — relation classes pin parent state, not "current"

A `Credit` connects a `Movie` to a `Person` via a role. Credit-shaped facts depend on Movie's canonical_ids and Person's canonical_ids. If Movie's ER runs again *after* Credit was last computed and merges two movies, every Credit row that referenced one of those movies now points at a `canonical_id` that was retired.

Two wrong answers, both tempting:

- **Always read "current" parent canonical_ids.** Credit's view of the world drifts whenever Movie's ER changes, even though Credit didn't run. Replay of yesterday's Credit run no longer reproduces yesterday's edges.
- **Eagerly cascade.** When Movie's ER produces a merge, recompute every dependent Credit row immediately. Expensive; couples downstream classes to upstream cadence; turns a small ER edit into a fan-out re-run.

The correct shape: **relation classes pin their parents' run hashes at compile time**, and read parent canonical_ids "as of" those pinned runs. Credit running today, with Movie's pinned run from yesterday, sees yesterday's canonical_ids. Tomorrow, a new Credit compile pins today's Movie run. Replay is deterministic.

This produces a third hard problem inside it: **the audit walk-back must cross class boundaries with no ambiguity.** "Why does this Credit row connect canonical_id `mov_x9k2` to canonical_id `per_a1b2`?" must answer with the pinned Movie run hash and the pinned Person run hash, not "current state of the lake." See [`design/core-design.md`](../../../design/core-design.md) commitment 8.

---

## Hard problem 4 — corrections must be immediate for consumers and reproducible for impls

A senior steward says: "Movie 'The Matrix' has year 1999, not 1998." Two requirements that pull against each other:

1. **Immediate.** The correction must be visible to the next consumer query, not at the next pipeline run. Pipelines run on cadences; the correction must not wait.
2. **Reproducible.** When the pipeline next runs and replays workflows for audit, the correction must flow through the same pipeline machinery as any other source's data — not be a special override that bound impls have to know about.

These pull apart unless the system has two distinct overlays for the *same* corrections data:

- A **query-time overlay** for consumer-facing reads. Lake data + pending postgres corrections, merged in the SDK runtime at the moment of read.
- A **migration into the lake** at the next pipeline run via a dedicated `_user_corrections` source. From that point on the correction is regular lake data with full provenance.

The non-obvious property: **bound impls (ER, DqRunner, materialization, etc.) never see the query-time overlay.** Their `DataContext` views are pure lake reads at the pinned moment. If they saw the overlay, replay would be non-deterministic — a workflow run yesterday would see the overlay state of *today*. Hiding the overlay from impls is what keeps replay deterministic. See [`design/core-design.md`](../../../design/core-design.md) commitment 13 and [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) § "In-flight corrections overlay."

The mirror problem applies to **forced ER decisions**: a user asserting "merge these two" or "do not merge these two" must take effect at the next ER run, not at query time, because ER decisions structurally change which `canonical_id` a row binds to. A query-time overlay can paper over a property value disagreement; it cannot paper over identity.

---

## Hard problem 5 — audit walk-back is the load-bearing user benefit

Restate the canary:

> **Why does the graph say `Movie.year = 1999` for canonical_id `mov_x9k2`?**

To answer mechanically — meaning: a system query, no human investigation, returning a structured response — every contributing artifact has to be **pinned and content-addressed** at the moment the value was produced. That includes:

| Artifact | Role in audit |
|---|---|
| Spec revisions of every involved class, slot, type, constraint, derivation, and source mapping | What the ontology said this value should be shaped like, at the moment of the run. |
| Bound impl revisions (ER, DqRunner, Materializer, QueryReader) participating in the run | Which Python source actually ran. |
| Impl `Config` revisions | Which thresholds, blocking strategies, and trust overrides were active. |
| Source watermarks at the moment of read | Which input rows were visible to the run. |
| ER cluster decisions and any user-asserted forced edges / non-edges | Which rows were bound to this canonical_id, and on whose authority. |
| Trust state `(α, β)` per `(source, property)` arm at the moment of the merge | Which contribution would have been the default if asked at that moment. |
| Pending corrections in postgres at query time | Whether the read overlaid an in-flight correction on top of lake data. |

If any of those is not pinned at run time — if any one of them is "look it up in the current state of the system" — replay drifts.

The hard part is not collecting these. The hard part is **making them all flow from the same primitive**: the content-addressed compile hash. A workflow trigger compiles to a `WorkflowSpec`, hashed via canonical JSON, and the hash IS the run identity. Spec revisions, impl revisions, configs, and pinned parent run hashes are baked into the hash. Reruns reference the hash. Audit walk-back is mechanical because the hash *is* the ledger.

This is non-obvious because most pipeline systems treat the run trigger (timestamp, schedule slot, manual button press) as the run identity. With a trigger-based identity, the system has to maintain a sidecar of "what was the spec when this trigger fired?" — and the sidecar drifts. Content-addressed identity dissolves the sidecar. See [`design/compiled-workflow-hashing.md`](../../../design/compiled-workflow-hashing.md).

---

## Hard problem 6 — the ontology is itself live data

Most ontology systems treat the ontology as code: edited in YAML, committed to a repo, deployed to a service that re-reads it on restart. That model is wrong for this domain because:

1. **Modeling is exploratory.** A team wiring up a new source discovers ontology gaps in real time. "We need a `LocalizedTitle` class. We need to split `runtime` into `runtime_theatrical` and `runtime_extended_cut`." The friction of a code-deploy cycle for each insight is friction on the actual modeling work.
2. **Spec authoring is programmatic, not textual.** YAML hand-editing for a non-trivial ontology produces typos, misaligned references, and silent semantic errors. A typed, programmatic authoring surface (UI / API over Pydantic spec) catches reference errors at parse time, validates cross-class consistency at publish time, and shows impact preview before the change goes live.
3. **The spec is part of audit.** If the ontology is "code," then audit walk-back has to leave the system to figure out which version of the code was active. If the spec is *data* with revisions and content-addressed hashes, audit stays inside the system.

The hard problem this opens: **edits to the spec must validate atomically.** Adding a slot that breaks a cross-class derivation must be caught before publish. Removing a class that an ER impl's `DataContext` depends on must fail at publish, not at the next workflow compile. The publish gate is non-trivial — it has to do Pydantic parse, reference resolution, DataContext cross-checks against every registered impl, and impact preview, and it has to do all of that in the runtime, not in CI.

Combined with hard problem 5, the implication is that **the spec graph and the impl graph live in the same identity model** — both are content-addressed, both are pinned into compile hashes, both are walked by impact analysis when something changes.

---

## Why these compound

Each problem in isolation has a known answer:

- **Problem 1** alone — multi-valued resolution — is a feature of property graphs and triple-stores.
- **Problem 2** alone — ER lineage — Splink, Zingg, and several commercial MDM tools handle.
- **Problem 3** alone — pinning parent state — is dbt's `ref()` macro at table granularity, and a fragile pattern in most other tools.
- **Problem 4** alone — read-through overlays — is what most CDC + materialized-view systems give you.
- **Problem 5** alone — audit walk-back — is what data lineage tools (Atlas, OpenLineage, Marquez) approximate.
- **Problem 6** alone — runtime-editable ontology — is what schema registries and data catalogs partially provide.

The compounding is what hurts.

| If you solve... | ...you're forced to give up |
|---|---|
| Multi-valued + ER lineage | Most ER tools assume single-valued canonical attributes after merge. |
| Multi-valued + audit walk-back | The "why this value" answer has to traverse the trust state at the moment, which most graph stores don't capture. |
| Audit walk-back + runtime-editable ontology | The audit chain has to include spec revisions as data; most catalogs treat schema changes as undocumented operational events. |
| ER lineage + pinned parent state | Cross-class temporal pinning is something dbt approximates at table granularity but not at canonical-ID granularity. |
| Corrections + audit walk-back | Corrections have to be a regular source, not a "manual override" branch in the pipeline; otherwise audit has to know about two parallel paths. |
| Corrections + runtime-editable ontology | Correction shapes have to evolve with the spec, with versioning, without code redeploys. |

The hard problem is **the diagonal** — making all six work together such that every fact in the graph traces back deterministically and every edit (to spec, trust, ER strategy, correction) propagates correctly without breaking replay.

---

## Open / unresolved

- **Observation signal for trust evolution.** What counts as a "win" or "loss" for a `(source, property)` bandit arm — correction rate, consensus-agreement, hold-out validation against curated truth — is acknowledged as open in [`design/trust-and-merge.md`](../../../design/trust-and-merge.md). It does not block the framing of "trust evolves," but the choice has real downstream consequences for what the system rewards.
- **Non-stationarity in trust.** Vendor reliability drifts. Vanilla Beta assumes stationary rewards. Whether to use windowed updates, exponential decay on prior counts, or explicit drift detection is open.
- **Default-resolution function at query time.** Posterior mean? Pessimistic mean? Thompson sample? The framing is settled (query-time default from a posterior); the actual function is open.

These three are flagged here because they sit inside hard-problem-1's framing and a careful reader might press on them.
