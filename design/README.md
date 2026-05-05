# Knot — design

This is the entry point for knot's design. Read this first.

> **Trust posture:**
> - Documents at the top level of `design/` are **authoritative**. Cite them. The team has signed off.
> - Everything under `design/legacy/` is historical and **unverified** — exploration captured during the prototype phase, not reconciled against the current codebase. Use it for context and motivation, not as specification. Cross-check anything before acting on it.
> - The summaries on this page are drafts and should be re-read against the code and the authoritative docs before they're cited.

## Authoritative docs

Read in this order:

1. [`knot-as-compiler.md`](knot-as-compiler.md) — **the architectural pattern.** Knot compiles; Maestro runs; teams customize via DI bindings (per `staging/seam-contract-pattern.md` and `staging/di-input-contract.md`). The "why use knot AND Maestro" answer lives here.
2. [`source-layer-contract.md`](source-layer-contract.md) — **where knot starts.** Sources are team-owned lake declarations; knot's compiled workflows begin at normalize. Specifies what the team must guarantee about the source layer.
3. [`compiled-workflow-hashing.md`](compiled-workflow-hashing.md) — **how runs are identified.** Every trigger is a fresh compile; the compiled `WorkflowSpec` is content-addressed by sha256; reruns reference the hash. Default trigger unit is one ontology class.

(More to be lifted out of `legacy/` as topics are reconciled.)

## What knot is

A control plane for a knowledge graph. The team owns one ontology (a
Pydantic spec authored programmatically via SDK / UI), multiple data
sources contributing facts about ontology classes, an ER strategy
that binds source-natural-keys to canonical entities, a trust-weighted
merge over multi-valued canonical facts, and a materialization step
that publishes resolved entities to a downstream lake or graph store.
Knot owns the *spec, audit, and provenance* of that pipeline; it
delegates the *execution* (Spark, an external orchestrator) to
whatever the team plugs in.

The architectural bet is that *every change vector* — schema, source,
ER algorithm, DQ rule, materialization target, human correction —
lives as the same kind of object: a versioned Pydantic spec node with
structural diff at write time, joined to the data it produced via
per-fact provenance. Ontology, derivation logic, and audit live in
one machine. That's what makes the audit walk-back ("why does this
movie say director = X?") deterministic in seconds rather than hours.

## Where the layers are

There is exactly one architectural seam that matters: **knot core
touches only spec/metadata; every read or write into the data plane
goes through an injected interface.**

The bound interfaces (see `staging/seam-contract-pattern.md` for the
unified DI pattern; the named protocols below are not a fixed set —
they're representative of the shape):

- `QueryReader` / `Materializer` / `Introspector` / `ViewManager` — orthogonal protocols for executing SQL against the lake (per `staging/query-executor.md`). Replaces the earlier framing of `LakeReader` / `LakeWriter` as separate concepts.
- `ERProtocol` — bound entity-resolution impl, per ontology class (per `staging/er-and-storage.md` and `staging/di-input-contract.md`).
- `DqRunner` — bound custom data-quality impl (per `staging/dq-design.md`); knot also ships built-in DQ checks that run via `QueryReader`.
- Materialization impls — free-form bound impls publishing canonical entities to targets (Neo4j, Iceberg, vector store, parquet, etc.) per `staging/pipeline-stages.md`. Not framed as one protocol-per-class; an impl declares whatever DataContexts it needs.
- `Translator` — bound impl for consumer-facing query expansion.
- `Orchestrator` — orthogonal; dispatches workflow specs and returns run handles. Maestro / Airflow / Argo / toy.

Earlier framings of "seven seam interfaces" or "eight interfaces"
(in [`legacy/architecture-boundary-spec-vs-data-plane.md`](legacy/architecture-boundary-spec-vs-data-plane.md))
are loose architectural primitives. The current model: every team-bound
stage follows the protocol + DataContext + pure-data context pattern
documented in `staging/di-input-contract.md` with knot-hosted source
code; the list of named protocols is a naming convention, not the
architectural primitive.

## Sub-docs by topic

### Foundational

- [`legacy/knot-architecture-v1.md`](legacy/knot-architecture-v1.md) — the original architecture write-up (long; the substrate behind most decisions)
- [`legacy/benefits.md`](legacy/benefits.md) — value-prop ledger; what knot's architecture buys the team
- [`legacy/personas-and-goals.md`](legacy/personas-and-goals.md) — Data Steward / Source Onboarder / Analytics Consumer / ER Engineer personas
- [`legacy/agent-accessible-ontology.md`](legacy/agent-accessible-ontology.md) — the ontology is data agents consume too, not just humans
- [`legacy/architecture-boundary-spec-vs-data-plane.md`](legacy/architecture-boundary-spec-vs-data-plane.md) — the eight interfaces, the swap test, the lint guard

### Lifecycles (what happens when…)

- [`legacy/lifecycle-data-node.md`](legacy/lifecycle-data-node.md) — authoring a data class: draft → review → publish
- [`legacy/lifecycle-source.md`](legacy/lifecycle-source.md) — registering a source: schema → connector → ingest → trust → merge participation
- [`legacy/lifecycle-er-strategy.md`](legacy/lifecycle-er-strategy.md) — shipping a new ER strategy: scope → spec → blocking + matching → adoption
- [`legacy/lifecycle-pipeline-run.md`](legacy/lifecycle-pipeline-run.md) — the 6-stage pipeline (ingest, normalize, resolve, merge, validate, publish) and what runs at each stage

### Subsystems

- [`legacy/sources-subsystem.md`](legacy/sources-subsystem.md) — sources surface, trust state, conflict tracking
- [`legacy/translator-impl-decision.md`](legacy/translator-impl-decision.md) — the multi-language query translator's design
- [`legacy/judgments-evaluative-feedback.md`](legacy/judgments-evaluative-feedback.md) — capturing human evaluations of ER / DQ outputs

### Walkthroughs

- [`legacy/walkthrough-paths.md`](legacy/walkthrough-paths.md) — five end-to-end paths (correction → propagate, source onboard → publish, trust adjust, query → translate, new class → blast radius). **Note:** original was UI-driven; rescope to API/SDK before using.

### External-system surveys

These compare knot's choices against existing systems:

- [`legacy/datajunction-di-survey.md`](legacy/datajunction-di-survey.md) — DataJunction's dependency-injection patterns
- [`legacy/datajunction-lineage-survey.md`](legacy/datajunction-lineage-survey.md) — DJ's lineage model
- [`legacy/datajunction-materialization-survey.md`](legacy/datajunction-materialization-survey.md) — DJ's materialization layer
- [`legacy/datajunction-schema-versioning-survey.md`](legacy/datajunction-schema-versioning-survey.md) — DJ's schema versioning
- [`legacy/graphframes-comparison.md`](legacy/graphframes-comparison.md) — GraphFrames' (vertex, edge) shape vs. knot's reified relations

## What lives in code, not in design docs

If you want to understand a current behavior, **read the code first.**

- `server/api/` — the HTTP surface (a thin layer; should depend only on injected interfaces)
- `server/core/` — control-plane domain logic (orchestrators, identity, query translators)
- `server/data_plane/` — interface seam (currently scaffolded; impls TBD)
- `server/repositories/` — repos for core tables only (data-plane reads/writes are not legitimate here)
- `server/materialization/targets/` — lake / neo4j writers
- `integration/scheduler/` — the fake-Maestro: a separate FastAPI service knot dispatches jobs to via HTTP, runs SparkSQL-flavored SQL on parquet via DuckDB
- `tests/integration/test_dispatch_e2e.py` — exercises the dispatch boundary end-to-end

## How this dir is organized

```
design/
  README.md       ← you are here; the only doc that should ever be cited as current
  legacy/         ← snapshots of the prototype-phase design corpus, all marked unverified
```

When a design topic is reconciled with the codebase and considered
stable, lift it out of `legacy/` into `design/<topic>.md` and drop
the legacy banner. Until then, every doc in `legacy/` is something to
read with the file open in `server/` next to it.

## What to do when something here disagrees with the code

The code is the truth. If a legacy doc says "knot writes to
per_source_facts via LakeWriter" but `server/api/corrections.py`
writes directly with raw SQL, the doc is the aspiration and the
code is the fact. File the discrepancy as a TODO in the legacy doc;
fix it in code at your discretion.
