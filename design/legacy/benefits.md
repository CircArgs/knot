---
title: Knot — design benefits ledger
status: note
project: knot
tags: [architecture, benefits, living-doc]
created_at: 2026-04-28T00:00:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# Knot — design benefits ledger

Living list of *what we get* from the architecture as it stands.
Update as new benefits surface; each entry should answer "why does this
matter?" not just "what is it?"

## The throughline

The seven numbered benefits below are downstream consequences of one
architectural property: **every change vector — schema, source, ER
algorithm, DQ rule, materialization target, human correction — is
versioned as a uniform LinkML node, structurally diffed at write
time, and joined to the data it produces via per-fact provenance.**
Ontology, derivation logic, and provenance live in one machine.

This is what you cannot get by composing existing systems: schema
registries (no execution context), dbt (no entity resolution), data
catalogs (lineage scraped, not generated), graph DBs (no derivation),
workflow orchestrators (no semantic layer), DataJunction (semantic
layer for metrics/cubes — different product remit; knot adopts DJ's
control-plane *patterns* but the ontology + ER + corrections +
multi-target output surface is outside DJ's scope). The value is the
*joining*, not any single capability.

---

## 1. Verified SQL for analytics consumers across query languages

The translator API accepts GraphQL / Cypher / SPARQL and returns SQL
that is **schema-aware** — knot uses the ontology + materialization
mapping to emit SQL that hits the lake correctly. Consumers pick the
language they like; knot guarantees the SQL is correct against the
current ontology + materialization layout.

**Why it matters:** consumers don't need to know the lake's table
naming, partitioning, or per-source vs resolved layout. They write
queries against the ontology; knot bridges to the physical reality.
This is the analytics-side payoff for treating LinkML as the source
of truth.

*Noted: 2026-04-28 (user)*

## 2. Tight version tracking + lineage across all change vectors

Knot owns the version + lineage story for every kind of change:

- **Sources** — every fact carries its origin source. Per-property,
  not just per-record.
- **User overrides (corrections)** — each correction is stored as a
  first-class artifact (user, timestamp, target, payload, overridden
  source) AND packaged as a high-trust source that ER consumes.
- **ER decisions over time** — ER pipelines are stored in knot as
  versioned ontology nodes (kind='pipeline'). When the ER strategy
  evolves, the change shows up in lineage. Re-runs against historical
  pipeline revisions are reproducible because knot dispatches to the
  orchestrator with the spec pinned to a specific revision.

This works because **the orchestrator is purely an executor**. Every
"what happened and why" question routes through knot — knot defined
the spec, knot dispatched it, knot tracks the run. The orchestrator
just runs Spark.

**Why it matters:** the audit story is end-to-end. "Why does this
movie's director say X?" walks back through: resolved value → which
source won → trust score at the time → which ER pipeline revision
was used → which corrections were participating sources at that run.
No information lives outside knot's tracking.

*Noted: 2026-04-28 (user)*

## 3. Data quality and validation as a first-class control-plane concern

DQ and validation aren't bolt-ons; they're three layers of the
control plane:

- **Schema-level DQ** — LinkML compiles to SHACL. Type, cardinality,
  required-ness, enum membership all checked for free at Normalize.
- **Spec-level DQ** — the static pre-flight validator catches every
  malformed spec before any orchestrator dispatch. SQL parses, types
  align, references resolve, no cycles, plugins exist, auth satisfies,
  ontology conforms. Milliseconds per spec.
- **Pipeline-level DQ** — the Validate stage runs SQL checks beyond
  SHACL (statistical, freshness, cross-entity invariants). Hard checks
  fail the run; soft checks warn.

**Why it matters:** every layer of DQ runs in the layer that owns the
context for it. SHACL where the schema lives. Static validation where
the spec lives. SQL checks where the data lives. The combination
catches bad data at the latest possible point where the cost of
recovery is still cheap.

*Noted: 2026-04-28 (user)*

## 4. JSON Schema export for LLM-powered structured extraction

LinkML compiles natively to JSON Schema (`gen-json-schema` is part of
the standard linkml tooling). Knot exposes a `GET /node/{id}/json-schema`
endpoint that returns the JSON Schema for any ontology class at the
current revision (or a pinned revision).

This unlocks LLM-driven extraction from unstructured sources: a
caller hands the JSON Schema to an LLM as the structured-output
constraint (OpenAI `response_format`, Anthropic `tools`, etc.), feeds
it unstructured text (a press release, a fan-wiki article, a podcast
transcript), and gets back ontology-conformant JSON ready to flow
into the Ingest stage as a regular source.

**Why it matters:** the same control plane that serves SQL to
analytics consumers and verified specs to the orchestrator also serves
the schema needed to onboard *any* unstructured input via LLM
extraction. The ontology becomes the universal contract — SQL on one
side, structured-output on the other. Adding a new "scrape and
extract" source becomes a thin wrapper: the LLM does the structuring,
the JSON Schema constrains it, knot's normal Ingest pipeline accepts
the structured rows. No bespoke per-source extractor code needed.

This also means user corrections, LLM-derived sources, and
deterministic upstream feeds all participate in ER through the same
machinery — they just have different trust scores.

*Noted: 2026-04-28 (user)*

## 5. Bidirectional orchestrator integration — knot fits into existing job graphs

Knot uses orchestrators (dispatches Spark via the `Orchestrator`
protocol), and orchestrators use knot (trigger endpoints let Maestro
call knot end-to-end as a step in a larger DAG).

A Maestro task — or any external scheduler's task — POSTs to
`/pipelines/{name}/runs` or `/trigger` and gets back a `run_id`. It
polls `GET /runs/{run_id}` (or receives webhook callbacks) until done,
then proceeds to its downstream steps. Maestro never needs to know
about knot's six internal pipeline stages; it sees one job that takes
some time and emits a status.

**Why it matters:** knot doesn't replace existing orchestration — it
slots into it. Teams already running Maestro DAGs that consume the KG
data don't need a new control surface; they call knot the same way
they call any other internal service. The investment in existing
orchestration tooling is preserved while knot owns the
domain-specific control plane (ontology, validation, lineage,
corrections).

*Noted: 2026-04-28 (user)*

## 6. Uniform publish/draft model across every node kind

Data shapes, sources, pipelines, ER strategies, DQ checks, trust
policies — they all use the *same* LinkML revision machinery, the
*same* DRAFT/PUBLISHED states, the *same* static-validator gating,
the *same* audit trail, the *same* lineage propagation. There is no
special-casing per kind.

**Why it matters:** the cognitive model for users (and reviewers,
and operators) doesn't fork as the platform grows. Onboarding a new
ontology author, a source owner, an ER engineer, or a DQ author is
the same workflow every time: write a LinkML doc, save as draft,
iterate, publish when the static validator passes. Internally, knot
has one machine for "version a thing" rather than seven half-bespoke
ones. Every new node kind we add (e.g., `view` later) inherits
versioning, drafts, audit, lineage, the API surface, and the UI
flow without writing any kind-specific code.

This is the LinkML uniformity argument paying its biggest dividend.

*Noted: 2026-04-28 (synthesized from lifecycle design)*

## 7. Materialization is pluggable too — knot ties ingestion, ontology, validation, AND output

Knot's dispatch pattern extends to the *consumer side*. Materialization
is generalized — knot outputs the graph in whatever shape a particular
consumer needs, with the consumer layer itself pluggable via the same
DI mechanism as orchestrators, ER strategies, and identity. Each
consumer is a plugin with its own output schema, ID-mapping rules, and
query routing semantics:

- **Lake tables** for analytics queries — the default consumer.
- **Graph stores** (Neo4j, Neptune, Cassandra, JanusGraph) — each
  needs different node/edge shapes and different ID conventions; each
  is a `MaterializationTarget` plugin that the materialization compiler
  emits SQL/job specs for.
- **Vector stores** for similarity search — same pattern, different
  output shape.
- **Live-query routing** — some queries route to the lake, some to a
  graph DB, some to a vector store. Knot picks based on query shape +
  consumer registration.

Knot core stays unchanged when a new consumer is added: it's one
plugin against a known protocol, not a parallel system.

The **"knot" name is doubly apt**: it ties the data together (the
graph), and it ties the *processes* together (ingestion, ontology
authoring, validation, ER, materialization, query routing). Today
those processes are disparate systems — a separate ingest pipeline,
an ontology in spreadsheets, a Spark job to the lake, a separate
Neo4j-loader script, ad-hoc query routers. They drift, they go out
of sync, DQ is uneven, provenance is lost between them.

**Why it matters:** any organization that has tried to keep an
ontology + a knowledge graph + an analytics layer + a query API in
sync knows the cost of letting these be separate systems. Knot's
single source of truth + dispatch pattern eliminates the drift by
construction. Every layer reads from one place, validates against
one schema, materializes from one resolved set of facts. Adding a
Neo4j swap-in, a vector materialization for similarity search, or a
federated query router becomes one plugin — not a new pipeline.

*Noted: 2026-04-28 (user)*

---

(Add new benefits below. Each entry: heading, 1–2 paragraphs of "why
it matters," and provenance.)
