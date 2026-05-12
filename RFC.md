# knot — RFC

## Headline

**knot is a reflective ontology compiler.**

"Reflective" in the programming-language sense: the compiler validates each spec edit against the running system that will execute it — the currently registered impls, the active trust policy, the source watermarks, the bound configs. The validator and the interpreter are the same runtime. CI cannot do this work, because CI does not hold that runtime state.

## Problems we built knot to solve

### 1. The pipeline / ontology split in the existing CKG

In the system we operate today, data management and ontology management live on two structurally unconnected axes.

The data pipeline runs lake-side: separate managed pipelines for every stage — source prep, ER, materialization, validation. The ontology, by contrast, is source-controlled turtle (RDF) — read by humans, referenced for context, but operationally inert. Editing the ontology and editing the pipeline are two separate workflows owned by two separate seams of the system, and nothing forces them into agreement.

Knot collapses that split. Data management *is* ingestion through the service layer, and the entire system hinges on the compiler's reading of the spec. Every artifact downstream of an ingest — the DDL the row is inserted into, the GraphQL surface it's queryable through, the constraints it must satisfy, the ER strategy that resolves it, the trust priors that weight its contribution — is emitted by the compiler from one canonical spec. There is no "ontology file the pipeline ignores."

### 2. The existing ontology is descriptive but not executable

The turtle files name classes, their properties, and `subClassOf` relations, but cannot carry the things that would actually make the pipeline correct — source-to-property mappings, constraints, trust priors, derivation rules for defined types. Even where the ontology and the pipeline agree on what entities exist, the ontology contributes nothing to producing or validating the data that fills them. The pipeline ends up encoding its own implicit schema, its own implicit constraints, its own implicit derivations — in code — and the ontology becomes a parallel description with no causal power.

### 3. Adding a new source is a multi-system, multi-team coordination

Onboarding a new source today touches every seam at once: a new schema for the source's raw shape, a new managed pipeline for ingestion + normalization, an ER strategy update if the source overlaps existing entities, downstream materialization updates, and ontology edits if any new concept appears. Each of those lives in a different system with a different owner and a different release cadence; nothing enforces consistency across them, and the cost of adding a source scales with how many of those systems happen to be touched.

### 4. Constraints can't live in any one place because no one place owns enough context

A rule like "Movie.year must be between 1888 and now+5" requires knowledge spanning the ontology (what Movie is, what year is), the storage layout (what column year maps to), the predicate language (how to express the rule), and the enforcement timing (ingest? batch? publish?). Today that context is scattered: the ontology knows entities, the pipeline knows storage, and the rule itself lives in hand-written validation code coupled to a specific storage layout. Constraints stop being *part of the schema* and become part of the implementation, which means they aren't shared, inherited, or queryable.

## Thesis — what these four problems have in common

A knowledge graph is only worth the name when **the ontology is the system of record and everything descends from it**. Entities exist because the ontology says so; their properties exist because the ontology says so; constraints, source mappings, derivation rules, trust priors exist because the ontology says so. Every artifact downstream — table layout, query surface, ingest validators, ER pairs, materializations — should be a mechanical projection of that one spec.

The existing CKG violates this. The ontology and the pipeline are *peers*, not parent and child. Each has its own implicit schema, its own implicit constraints, its own implicit derivations, and the ontology has no causal power over any of them. That's what's actually causing the four problems above — they're symptoms of having multiple competing sources of truth in a system that, by its name, should have exactly one.

Knot's posture is the inverse: the spec is the single source of truth, and the compiler is the mechanism that makes everything else descend from it. If an artifact exists at runtime that wasn't projected from the spec, the architectural seam has been violated.

## What knot is, concretely

Knot is a service whose only declarative state is a single versioned, typed spec — naming the graph's classes (concrete, abstract, defined) with their properties, sources, source-bindings (with per-property mappings and trust priors), and constraints — and whose compiler emits every runtime artifact (DDL, GraphQL surface, ingest validators, constraint SQL, ER bindings, materializations) as a mechanical projection of that spec.

## Why "reflective"

The compiler validates each spec edit against the *running system* that will execute it — not against a static schema, not against CI fixtures. When a curator drafts an edit ("add property `Movie.trailer_url`", "tighten constraint X", "rebind the wiki source to ingest synopsis"), the publish gate consults the currently registered ER impls and their declared input shapes, the active trust policy, the source watermarks (what's already in the lake), the published-spec hash, and the bound configs. CI cannot do this work because CI does not hold any of that state. The validator and the interpreter being the same runtime is what makes "edit the spec while it's running" safe — and what makes it possible for the ontology to be the source of truth rather than a parallel description that has to be reconciled.

## Architecture in one figure's worth of words

Knot is a single FastAPI service backed by postgres. The control plane (spec revisions, draft state, audit) and the data plane (one source-rows table + one SCD2 bindings table per concrete class; one VIEW per defined class) live in the same postgres instance.

The compiler is a pure pipeline: typed-entity-tree in, SQL-Composable out, no I/O. It has three emitters: **postgres DDL** (CREATE TABLE/VIEW/INDEX/CONSTRAINT per class), **GraphQL schema** (Strawberry types projected from class definitions), and **constraint SQL** (sqlglot-validated predicates compiled to violation-shape `SELECT`s over the per-class tables).

The runtime has four pieces:

- **Spec store.** Holds drafts and the published spec as typed Pydantic values, with SCD2 audit on every revision.
- **Ingest path.** REST endpoint per source; rows are validated through compiler-emitted Pydantic schemas, passed through bound ER impls if any, and inserted via the SCD2 bindings table.
- **Query surface.** Strawberry GraphQL over psycopg; query nodes compile to SQL on demand.
- **Extension surface.** A thin in-process dispatcher plus HTTP shims to sibling services (`ai/`, `er/`) for team-owned behavior.

Knot itself runs no jobs. Bound impls (ER, materialization, dq, translator) execute the workflows the compiler emits — that's the "compiler that delegates execution" claim made concrete.

## What knot is NOT

Each comparator does part of what knot does well; none unify all four problems above.

- **RDF + SHACL + turtle files** — exactly what the existing CKG has. Strong ontology vocabulary, real validation language, no runtime; you still need a separate pipeline to produce the data, and validation lives in a separate process from storage. The pipeline/ontology split is *baked in* by this approach.
- **LinkML** — closest at the model level. Generates Pydantic, JSON Schema, SQL DDL from a YAML spec. Stops at code generation: doesn't host a service, doesn't manage runtime state, doesn't validate edits against the running system, doesn't unify ingest + query + constraint behind one spec. Knot's metaschema borrowed structural ideas from LinkML and then diverged where the unified-runtime requirement forced different choices.
- **dbt** — a SQL compiler with a model graph and a test framework. The model graph isn't an ontology: no class/property/relation typing, no defined-class predicates, no trust resolution, no source-binding-as-data. dbt is the comparator for *the lake-side pipeline knot replaces*, not for the ontology layer.
- **DataJunction** — closest spiritual comparator: a semantic-layer service that compiles spec edits to artifacts. But DJ optimizes for query-time semantic models over many backends; knot optimizes for write-time ontology + ingest unification in a single postgres. Different problem.
- **Graph DBs (Neo4j, TigerGraph, etc.)** — strong on adjacency-heavy workloads, weak on per-property trust reconciliation, schema-as-data, and constraint-as-data. We chose postgres because the data is rectangular with reified relations, not because we needed adjacency primitives.

The shape of knot's contribution: an ontology compiler with a postgres-native execution model, validation against runtime state, and source-bindings / trust / constraints as first-class spec content.
