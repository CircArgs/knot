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

The turtle files name classes, slots, and `subClassOf` relations, but cannot carry the things that would actually make the pipeline correct — source-to-slot mappings, constraints, trust priors, derivation rules for defined types. Even where the ontology and the pipeline agree on what entities exist, the ontology contributes nothing to producing or validating the data that fills them. The pipeline ends up encoding its own implicit schema, its own implicit constraints, its own implicit derivations — in code — and the ontology becomes a parallel description with no causal power.

### 3. Adding a new source is a multi-system, multi-team coordination

Onboarding a new source today touches every seam at once: a new schema for the source's raw shape, a new managed pipeline for ingestion + normalization, an ER strategy update if the source overlaps existing entities, downstream materialization updates, and ontology edits if any new concept appears. Each of those lives in a different system with a different owner and a different release cadence; nothing enforces consistency across them, and the cost of adding a source scales with how many of those systems happen to be touched.

### 4. Constraints can't live in any one place because no one place owns enough context

A rule like "Movie.year must be between 1888 and now+5" requires knowledge spanning the ontology (what Movie is, what year is), the storage layout (what column year maps to), the predicate language (how to express the rule), and the enforcement timing (ingest? batch? publish?). Today that context is scattered: the ontology knows entities, the pipeline knows storage, and the rule itself lives in hand-written validation code coupled to a specific storage layout. Constraints stop being *part of the schema* and become part of the implementation, which means they aren't shared, inherited, or queryable.

## Thesis — what these four problems have in common

A knowledge graph is only worth the name when **the ontology is the system of record and everything descends from it**. Entities exist because the ontology says so; slots exist because the ontology says so; constraints, source mappings, derivation rules, trust priors exist because the ontology says so. Every artifact downstream — table layout, query surface, ingest validators, ER pairs, materializations — should be a mechanical projection of that one spec.

The existing CKG violates this. The ontology and the pipeline are *peers*, not parent and child. Each has its own implicit schema, its own implicit constraints, its own implicit derivations, and the ontology has no causal power over any of them. That's what's actually causing the four problems above — they're symptoms of having multiple competing sources of truth in a system that, by its name, should have exactly one.

Knot's posture is the inverse: the spec is the single source of truth, and the compiler is the mechanism that makes everything else descend from it. If an artifact exists at runtime that wasn't projected from the spec, the architectural seam has been violated.
