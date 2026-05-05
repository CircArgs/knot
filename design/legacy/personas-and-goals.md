---
title: Knot — user personas and goals
status: note
project: knot
tags: [planning, personas, requirements]
created_at: 2026-04-28T00:00:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# Knot — user personas and goals

Anchor for design debates. Every architectural decision should be
checkable against "does this make at least one persona's job
materially better, without making another's worse?"

## Primary personas

### 1. Ontology Author
Senior data engineer / domain expert who owns "what is a Movie, what
is a Title, what relations exist."

- **Goal:** define + evolve the canonical schema for the data model.
- **Workflow:** writes LinkML for an entity or relation, saves as
  draft, iterates with collaborators (diff view), publishes when
  stable.
- **Pain today:** schema in spreadsheets, wikis, scattered code — no
  versioning, no validation, drift between teams.
- **Knot win:** schema-as-code with proper versioning, automatic SHACL
  validation, downstream lineage shows blast radius of every change,
  drafts let work-in-progress live alongside production.

### 2. Source Onboarder
Data engineer integrating an upstream source (IMDB, internal feed,
vendor data).

- **Goal:** get a new source's data flowing into the canonical graph.
- **Workflow:** registers the source (LinkML doc capturing connection
  + ontology mapping + refresh policy), wires up the Ingest stage,
  watches initial trust calibration.
- **Pain today:** every source is a one-off project; mapping logic in
  custom code; trust scoring is informal ("I trust IMDB more than
  the vendor").
- **Knot win:** uniform source registration, per-source materialization
  automatic, trust score has explicit semantics that every consumer
  can rely on.

### 3. Data Steward / Curator
Domain expert responsible for quality in their slice (music catalog
steward, originals metadata lead).

- **Goal:** catch and fix errors; raise the floor of data quality
  continuously.
- **Workflow:** browses entities, spots wrong values, submits
  corrections through the UI, watches the corrections-as-source feed
  back into the next ER run.
- **Pain today:** corrections die — fix something, next pipeline run
  overwrites it, file a ticket against the upstream pipeline, repeat.
- **Knot win:** corrections persist (first-class source); earn trust
  over time; provenance shows them which source said what; the loop
  closes.

### 4. ER / DQ Engineer
Research-flavored engineer tuning entity resolution or quality rules.

- **Goal:** improve match quality without breaking downstream
  consumers.
- **Workflow:** writes a new ER strategy or DQ check, deploys as a
  versioned `er_strategy` or `dq_check` node, runs side-by-side,
  evaluates against held-out clusters.
- **Pain today:** ER algorithms entangled with pipelines; experimenting
  requires duplicating infrastructure.
- **Knot win:** ER strategies and DQ checks are versioned, pluggable,
  lineage-tracked — "which ER version produced this cluster" is
  exactly answerable.

### 5. Analytics / ML Consumer
Data scientist or ML engineer querying the graph for downstream work.

- **Goal:** clean, schema-conformant data for analysis or models.
- **Workflow:** writes queries in their preferred language (SQL /
  Cypher / SPARQL / GraphQL), gets back verified SQL pointed at the
  lake, runs it.
- **Pain today:** "is this title's metadata authoritative?" hard to
  answer; numbers fragile in standups; pipelines break silently when
  upstream schemas drift.
- **Knot win:** translator API + per-fact provenance + stable revision
  semantics — they can defend their numbers.

### 6. Pipeline Operator
SRE-adjacent; keeps the whole flow running.

- **Goal:** pipelines complete on time; failures triage fast.
- **Workflow:** monitors run dashboard, drills into failed stages,
  coordinates with upstream/downstream when something's stuck.
- **Pain today:** opaque failures; "why didn't this run?" requires
  Slack archaeology.
- **Knot win:** explicit run tracking, per-stage event logs, static
  validation catches errors at submit time before they burn Spark.

### 7. LLM Pipeline Builder
Applied ML engineer extracting structure from unstructured sources.

- **Goal:** bulk-extract knowledge from text into the graph.
- **Workflow:** pulls JSON Schema from knot, prompts an LLM with it,
  registers extracted output as a source (low trust by default).
- **Pain today:** LLM outputs ad-hoc; no enforcement; integration
  artisanal.
- **Knot win:** schema-driven extraction; LLM source = regular source
  = participates in ER + corrections loop with appropriate trust.

## Secondary stakeholders

### Compliance / Auditor
Needs reproducible audit trails. Append-only `history` with strong
forensic fields + versioned ontology means "who knew what when" is
answerable.

### Downstream Consumer Team Engineer
Recs / search / content engineer reading lake outputs into their
stack. Stable per-class layout + GraphFrames-friendly UNION recipe +
Maestro-step trigger model means knot drops into existing data flows
without disruption.

## Cross-cutting goals

Across all personas the same wins recur:

- **Trustworthy data** — high confidence values are clearly marked;
  low confidence values are flagged.
- **Clear provenance** — every fact's origin is traceable.
- **Fast iteration** — schema changes, ER strategy changes, source
  additions are quick and reversible.
- **Deterministic outcomes** — same inputs + same versions always
  produce same outputs (idempotency by design).

Every architecture decision pings against these four.
