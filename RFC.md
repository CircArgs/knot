# RFC: Hitch Ontology Compiler - KNOT

**Author(s):** Nick Ouellet  
**Date:** 2026-05-12  
**Status:** Draft  
**Reviewers:** *[Add Reviewers]*  
**Approvers:** *[Add Approvers]*  

## Abstract

This RFC proposes the design and implementation of **knot**, a unified service that replaces the existing bifurcated approach to data pipelines and ontology management. Currently, the system suffers from a pipeline/ontology split where ontologies are descriptive but operationally inert, leading to implicit schemas in code and high coordination costs. Knot solves this by treating the ontology as the single executable source of truth. From a single declarative spec, knot mechanically projects all downstream artifacts—including Postgres DDL, GraphQL surfaces, ingest validators, and constraint SQL—ensuring complete alignment between the running system and its schema.

**knot is a reflective ontology compiler as a service.**

## Background: Deficiencies of the Current System

In the existing CKG, data management and ontology management live on two structurally unconnected axes. The ontology and the data pipelines act as *peers* rather than parent and child, resulting in competing sources of truth. This leads to four critical failure modes:

*   **The Pipeline / Ontology Split:** The data pipeline runs lake-side via managed jobs, while the ontology is source-controlled turtle (RDF). The ontology is read by humans but is operationally inert. Nothing forces the pipeline and the ontology into agreement.
*   **Descriptive, Not Executable Ontologies:** The turtle files name classes and properties but cannot carry source-to-property mappings, constraints, or trust priors. The pipeline inevitably encodes its own implicit schema in code, leaving the ontology with no causal power.
*   **High Coordination Costs for New Sources:** Onboarding a new source today requires touching multiple independent systems (ingestion, ER, materialization, ontology edits), each with different owners and release cadences. 
*   **Scattered, Un-Queryable Constraints:** A constraint requires knowledge of the ontology, storage layout, and enforcement timing. Because no single place owns all of this context, validation rules live in hand-written code coupled to specific storage layouts, making them impossible to share, inherit, or query.

## Goals and Non-Goals

### Goals
*   **Ontology as Single Source of Truth:** Treat the ontology as the single executable source of truth for the entire pipeline.
*   **Mechanical Projection:** Guarantee that downstream artifacts (DDL, GraphQL, constraints, ingest validators) are mechanically projected from a single spec.
*   **Unified Runtime & Automatic Validation:** Unify ingest, validation, and querying behind one service. Because all data is ingested via the API and validated against compiler-emitted Pydantic models and constraint SQL, the ontology actively governs ingestion. This automatically blocks invalid data at the door—a massive upgrade over the existing setup where validation is scattered across hand-written pipelines.
*   **Reflective Validation:** Enable safe, reflective editing of the spec validated against the live running system state.

### Non-Goals
*   **Heavy Adjacency Traversals:** Replacing graph databases (e.g., Neo4j) for workloads optimized purely for complex multi-hop graph traversals.
*   **Decoupled Pipelines:** Supporting or maintaining lake-side data pipelines that operate independently of the ontology.
*   **Generalized Semantic Layer:** Building a generalized semantic layer intended for arbitrary external query backends (unlike tools like DataJunction).

## What knot is, concretely

Knot is a service whose only declarative state is a single versioned, typed spec — naming the graph's classes (concrete, abstract, defined) with their properties, sources, source-bindings (with per-property mappings and trust priors), and constraints — and whose compiler emits every runtime artifact (DDL, GraphQL surface, ingest validators, constraint SQL, and materialization views) as a mechanical projection of that spec.

## Why "reflective"

The compiler validates each spec edit against the *running system* that will execute it — not against a static schema, not against CI fixtures. When a curator drafts an edit ("add property `Movie.trailer_url`", "tighten constraint X", "rebind the wiki source to ingest synopsis"), the publish gate consults the active trust policy, the source watermarks (what's already in the lake), the published-spec hash, and the bound configs. CI cannot do this work because CI does not hold any of that state. The validator and the interpreter being the same runtime is what makes "edit the spec while it's running" safe — and what makes it possible for the ontology to be the source of truth rather than a parallel description that has to be reconciled.

## Proposed Architecture

Knot is a single FastAPI service backed by PostgreSQL. Both the control plane (managing the spec) and the data plane (the actual knowledge graph data) live in the same Postgres instance, ensuring transactional consistency between schema edits and data.

### 1. The Compiler Pipeline
The compiler is a pure, side-effect-free pipeline: it takes a typed entity tree as input and outputs SQL composables. It features three primary emitters:
*   **Postgres DDL Emitter:** Generates `CREATE TABLE`, `CREATE VIEW`, `CREATE INDEX`, and `CREATE CONSTRAINT` statements per class.
*   **GraphQL Schema Emitter:** Projects Strawberry GraphQL types directly from the class definitions, ensuring the query surface always matches the storage layout.
*   **Constraint SQL Emitter:** Uses `sqlglot` to validate constraint predicates and compiles them into violation-shape `SELECT` queries that run over the per-class tables.

### 2. The Runtime Components
The runtime environment is composed of four distinct layers:

*   **Spec Store (Control Plane):** Manages draft and published specs as typed Pydantic values. Every revision is tracked using Slowly Changing Dimensions Type 2 (SCD2) for full auditability.
*   **Ingest Path:** Exposes a dedicated REST endpoint per configured source. Because knot compiles the ontology directly into typed models and SQL constraints, incoming rows are strictly and automatically validated against the single source of truth *before* being written to the SCD2 bindings table. This entirely eliminates the need for parallel, hand-written validation pipelines. (Entity Resolution and other domain logic are delegated to external extensions).
*   **Query Surface:** Exposes a Strawberry GraphQL API over `psycopg`. Incoming GraphQL query nodes are compiled to optimized SQL on demand.
*   **Extension Surface:** A thin, in-process dispatcher that uses HTTP shims to delegate team-owned behavior to sibling services (e.g., `ai/`, `er/`). 

### 3. Execution Model & Storage Layout
Knot itself does not run background jobs or pipelines. Instead, external extension services (like ER and data quality) subscribe to the storage layer or are invoked via HTTP shims to execute domain-specific workflows. 

At the storage layer, the data plane consists of:
*   **Concrete Classes:** Mapped to one "source-rows" table + one SCD2 bindings table.
*   **Defined Classes:** Mapped to a SQL `VIEW` that dynamically evaluates its predicate against the concrete tables.

## Examples — building up a spec

Below we build a small spec from nothing and watch the running system grow at each step. The domain is intentionally tiny — a movie database — so the conceptual moves stand out. Screenshots from a live instance accompany each step (omitted here).

### 1. A single class

Declare `Movie` with a handful of properties: `title`, `year`, `runtime`. The compiler emits a `movie` table in the data plane (one column per property, plus knot's system columns for provenance and history) and a `movie` field on the GraphQL surface that returns rows. *[screenshot: spec graph showing one class node + the emitted table shape]*

### 2. A source binding

Declare an `imdb` source and bind it to `Movie`, mapping IMDB's field names (`primaryTitle`, `startYear`) to our properties. Posting IMDB rows to the ingest endpoint now validates them through the binding's mapping and lands them in the `movie` table; GraphQL reads return them. *[screenshot: binding card + a few ingested rows]*

### 3. Inheritance

Add an abstract `MediaItem` class that `Movie` (and later `TVSeries`, `Episode`) declare via `is_a`. `MediaItem` itself has no table — only concrete descendants do — but its properties, its mixins, and its constraints flow to every descendant automatically. *[screenshot: spec graph with the is_a edge]*

### 4. A mixin

Declare an `Auditable` mixin with `created_at` / `updated_at` and apply it to `MediaItem`. Every concrete descendant gains those two columns; no separate `auditable` table appears — mixins are property-bundles that get pulled in, not parallel tables. *[screenshot: spec graph showing mixin edge; descendant table now wider]*

### 5. A reified relation

Declare `Credit` as its own class with two relation-typed properties (`movie` → `Movie`, `person` → `Person`) plus `role` and `billing_order`. `Credit` gets its own table and its own ingestion path; the GraphQL surface lets you traverse `Movie → credits → Person` and back, with the relation's own properties available along the edge. *[screenshot: junction node with two FK edges]*

### 6. A defined class

Declare `Director` as "a `Person` where there exists a `Credit` with `role='director'`". The compiler emits a SQL VIEW (not a table) over `Person` filtered by that predicate, and the GraphQL surface gains a `director` field that returns only matching Persons — refreshed automatically as new `Credit`s arrive. *[screenshot: spec graph with defined-class chip; query result]*

### 7. A constraint with inheritance

Attach a constraint to `MediaItem`: `year` between `1888` and `now + 5 years`, severity error. The predicate is a SQL string (validated at publish time); ingest enforces it batch-wise (rolling back violators), and the publish gate revalidates against existing data before any spec edit lands. Because `Movie` is_a `MediaItem`, it inherits this constraint automatically — no per-class duplication. *[screenshot: constraint chip on MediaItem + ingest-time rejection]*

### 8. The query surface

A consumer never sees postgres or hand-written SQL: the published spec produces a GraphQL surface with one root field per concrete or defined class (`movie`, `person`, `credit`, `director`, ...), each filterable by property, joinable by relation, projectable. The same spec that produces the storage layout produces the read surface; they cannot drift. *[screenshot: GraphQL Playground returning a join query]*

### 9. Ingesting Data

Because knot compiles the ontology directly into typed validators, ingestion is a simple REST call. A client posts a batch of rows attributed to a specific source binding (e.g., `imdb`). Knot automatically maps the fields, validates the batch against the `Movie` schema, enforces constraints, and lands the data.

```json
POST /graph/ingest/imdb?class_name=Movie
{
  "rows": [
    {
      "primaryTitle": "The Godfather",
      "startYear": 1972,
      "runtimeMinutes": 175
    }
  ]
}
```

### 10. Manual Corrections

If automated ingestion gets a property wrong, a curator (or an LLM extension) can submit a typed correction. The correction atomically writes an audit row, mutates the data plane, and registers negative bandit feedback against the source that provided the bad data, ensuring the system learns over time.

```json
POST /graph/corrections
{
  "type": "property",
  "class_name": "Movie",
  "canonical_id": "imdb:tt0068646",
  "slot": "year",
  "value": 1973
}
```

## What knot is NOT

Each comparator does part of what knot does well; none unify all four problems above.

- **RDF + SHACL + turtle files** — exactly what the existing CKG has. Strong ontology vocabulary, real validation language, no runtime; you still need a separate pipeline to produce the data, and validation lives in a separate process from storage. The pipeline/ontology split is *baked in* by this approach.
- **LinkML** — closest at the model level. Generates Pydantic, JSON Schema, SQL DDL from a YAML spec. Stops at code generation: doesn't host a service, doesn't manage runtime state, doesn't validate edits against the running system, doesn't unify ingest + query + constraint behind one spec. Knot's metaschema borrowed structural ideas from LinkML and then diverged where the unified-runtime requirement forced different choices.
- **dbt** — a SQL compiler with a model graph and a test framework. The model graph isn't an ontology: no class/property/relation typing, no defined-class predicates, no trust resolution, no source-binding-as-data. dbt is the comparator for *the lake-side pipeline knot replaces*, not for the ontology layer.
- **DataJunction** — closest spiritual comparator: a semantic-layer service that compiles spec edits to artifacts. But DJ optimizes for query-time semantic models over many backends; knot optimizes for write-time ontology + ingest unification in a single postgres. Different problem.
- **Graph DBs (Neo4j, TigerGraph, etc.)** — strong on adjacency-heavy workloads, weak on per-property trust reconciliation, schema-as-data, and constraint-as-data. We chose postgres because the data is rectangular with reified relations, not because we needed adjacency primitives.

The shape of knot's contribution: an ontology compiler with a postgres-native execution model, validation against runtime state, and source-bindings / trust / constraints as first-class spec content.

## Appendix: Proposed API Routes

The service exposes a unified REST/GraphQL surface that cleanly separates the Control Plane (spec management) from the Data Plane (graph operations). 

### Control Plane (`/spec/*`)
*   **`GET /spec/published`**: Retrieve the currently active, compiled specification.
*   **`POST /spec/graphql`**: Query the structure of the published spec itself (the metamodel).
*   **`POST /spec/drafts`**: Create an isolated draft for ontology edits.
*   **`POST /spec/drafts/{id}/classes` (and `/sources`, `/source_bindings`, `/constraints`)**: Mutate the draft ontology.
*   **`POST /spec/drafts/{id}/_validate_constraint`**: Test a raw constraint SQL predicate via `sqlglot` against the draft's schema.
*   **`POST /spec/drafts/{id}/preview`**: Dry-run publish gates to check if the draft is valid and diff the proposed schema changes.
*   **`POST /spec/drafts/{id}/publish`**: Transactionally compile the draft, emit Postgres DDL and Pydantic validators, and promote it to the active spec.

### Data Plane (`/graph/*`)
*   **`POST /graph/query`**: Execute a Strawberry GraphQL query against the published knowledge graph data.
*   **`POST /graph/ingest/{source_name}`**: Push a batch of rows. Rows are strictly validated against compiler-emitted schemas derived from the published spec before hitting the database.
*   **`GET /graph/classes/{class_name}/{canonical_id}`**: Retrieve all raw, per-source contributions for an entity.
*   **`GET /graph/classes/{class_name}/{canonical_id}/resolved`**: Retrieve the single, trust-resolved record for an entity.
*   **`POST /graph/corrections`**: Manually submit a typed correction (e.g., merge, split, property override) with immediate bandit feedback.
*   **`POST /graph/constraints/check`**: Run all published constraints against the current data plane, returning offending rows.

### Extensions & Ops (`/dq/*`, `/lake/*`)
*   **`POST /dq/scan`**: Snapshot per-property data quality stats from the current graph.
*   **`GET /lake/materialize`**: Generate target-dialect SELECT queries for exporting resolved graph state to a data lake.
