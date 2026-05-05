# Why existing systems don't suffice

An honest, factual look at the systems that solve adjacent problems. Each one covers a slice of the combination knot needs. None covers the combination. The point of this page is not to argue that existing systems are bad — most are excellent at what they do — but to be clear about what each one does and does not address, so the reader can verify that the combination is the hard part.

---

## One-paragraph summary

The systems most often suggested in this domain — dbt, DataJunction, LinkML, SHACL, OWL, Splink, Apache Atlas, Neo4j — split cleanly along the axes of the [hard problems](the-hard-problems.md). dbt and DataJunction handle SQL transformation pipelines with lineage at table or column granularity but do not model entities, ER, or per-`(source, property)` trust. LinkML and SHACL handle ontology authoring and structural constraint validation but do not orchestrate pipelines, perform ER, or run query-time trust resolution. OWL DL handles formal reasoning but is the wrong primitive for a pipeline that wants deterministic replay, not theorem-proving. Splink is excellent at pairwise probabilistic record linkage but solves only the ER slice. Atlas (and adjacent lineage tools) collect lineage as metadata but do not own the pipeline that produces the data, so their lineage drifts from what actually happened. Neo4j stores and queries graphs but is downstream of the entire problem — knot publishes to graph stores like Neo4j; it does not replace them. The combination — multi-source ER with per-source disagreement preserved, per-`(source, property)` trust resolved at query time, runtime-editable ontology, deterministic audit walk-back through pinned content-addressed runs — is what no single one of these systems covers.

---

## Comparison table

| System | What it does | Slice of knot's problem covered | Slice it doesn't cover |
|---|---|---|---|
| **dbt** | SQL transformation pipelines compiled from Jinja-templated models. Documents lineage at the model (table) level. | The compile-not-execute discipline; templated SQL; ref-based lineage. | No ontology / typed metaschema. No ER. No multi-valued canonical facts. No per-`(source, property)` trust. Lineage at table-not-fact granularity. Incremental requires manual `is_incremental()` macros because models are opaque SQL. |
| **DataJunction** | Semantic layer; metric and dimension definitions over a logical model; serves queries via push-down to backends. | A typed semantic model; lineage; query-time materialization choices. | Not an entity-resolution system. Does not model multi-source disagreement as a first-class concept. Not designed for runtime-editable ontology with content-addressed audit walk-back through pinned spec revisions. |
| **LinkML** | YAML-based ontology / data-model authoring with code generation (Python, JSON Schema, SHACL, etc.). | Excellent ontology vocabulary (class / slot / range / mixin / `permissible_values` / `is_a` / `multivalued`). Compiles to multiple targets. | Authoring is YAML-first, not programmatic / runtime-editable. Not a pipeline. Not an ER system. Not a trust model. Knot borrows the vocabulary precisely because it is good ontology terminology — but takes none of the library, runtime, or YAML round-trip. |
| **SHACL** | Shape constraint language for RDF graphs. Validates that a graph conforms to declared shapes. | Constraint validation as a separate concern from data production. | Not an authoring system. Not a pipeline. Not an ER system. RDF-shaped; assumes triple-store data residency. Wrong primitive for tabular lake data. |
| **OWL DL** | Description logic with formal reasoners (HermiT, Pellet, etc.). Supports automated inference of class / property relationships. | Formal semantics for class hierarchy and property characteristics. | Decidability constraints make modeling slow. Reasoner runtime is not deterministic in the operational sense the audit promise needs. Not a pipeline. Not a trust model. Wrong tool for "compile a workflow that produces this graph deterministically." |
| **Splink** | Probabilistic record linkage at scale (Spark / DuckDB / Athena). Fellegi-Sunter-style scoring; blocking; clustering. | The ER slice — pairwise linkage with confidence, threshold + transitivity to clusters. | Only the ER slice. Does not own the ontology, the canonical-id lifecycle, multi-valued resolved facts, trust evolution, or audit walk-back. A team using Splink would still need everything around it. (A bound knot ER impl could legitimately *be* a Splink wrapper.) |
| **Apache Atlas** | Metadata catalog and lineage tracker. Receives lineage events from systems that opt in. | Lineage as metadata; type system for entities and relationships. | Atlas does not run the pipeline; it collects what the pipeline reports. Lineage fidelity depends on the producing system telling the truth. No content-addressed run identity. No query-time trust resolution. Not designed to be a control plane; it is a sidecar. |
| **Neo4j** (and other graph stores) | Stores and queries property graphs; Cypher / Bolt; supports CSV bulk load. | Graph storage and traversal for downstream consumers. | Downstream of the entire problem. Knot publishes *to* Neo4j (and Iceberg, vector stores, parquet) via bound `Materializer` impls. Neo4j is a target, not a competitor. |

---

## Detailed positioning

### dbt — closest neighbor on the compile-not-execute axis

dbt and knot share one structural commitment: **the system reads a declarative spec, emits something a runtime can execute, and dispatches.** dbt compiles Jinja-templated SQL into runnable SQL and hands it to a warehouse; knot compiles a typed Pydantic spec into a `WorkflowSpec` and hands it to an external orchestrator. Both are compilers, not engines.

Where they diverge:

- **Granularity.** dbt's models are tables (or views, or incremental tables, or ephemeral CTEs). dbt's lineage is `ref('upstream_model')` at the model level. Knot's units are typed ontology classes and slots, with lineage that survives down to individual fact contributions per source.
- **Structural understanding.** dbt sees SQL as opaque text. It cannot tell you *which derived fact* depends on which source row through which transformation, only which model depends on which model. Knot's expression tree is a typed Pydantic structure; it knows the dependency at fact granularity.
- **Incremental processing.** dbt's `is_incremental()` requires the model author to write the delta logic manually because dbt has no structural understanding of what flows where. Knot's structural model means change propagation is computable from the spec, not hand-rolled per model. This page is not the place to design that — but the *capability* difference is structural.
- **Multi-source disagreement.** dbt does not have a concept of "the same canonical entity from multiple sources." A team using dbt for this domain would write SQL that makes one source-priority decision per property and write a comment explaining why. Knot's data layer keeps the multi-set; trust resolution happens at query time.
- **Audit walk-back.** dbt's run history records which models ran when. It does not record which spec revision was active per run as a content-addressed identity. The team using dbt for audit ends up with a sidecar.

The position is: **dbt is the right shape for SQL transformation pipelines, and the wrong shape for multi-source knowledge-graph construction.** A team trying to use dbt for the latter will rebuild ER, trust, multi-valued resolution, and audit-as-data on top of it — and at that point they are writing a different system.

### DataJunction — semantic model without the ER and trust layers

DataJunction provides a typed semantic layer: dimensions, metrics, dimensional joins compiled and executed via push-down. It overlaps with knot's spec-as-data ambition in that it treats the model as a structured artifact, not a folder of SQL files.

Where it diverges:

- **Not an ER system.** DataJunction assumes canonical entities exist; knot's ER stage is what produces them.
- **No multi-valued canonical facts.** DataJunction's metrics are single-valued by construction.
- **No per-`(source, property)` trust.** Source-level priority is the closest analog and is not modeled as a first-class spec concept.
- **No content-addressed run identity that pins spec, impls, and configs together.** The audit chain a knot run produces — spec rev × impl rev × config rev × source watermark × trust state × ER decision × correction overlay — is finer-grained than DataJunction's lineage primitive.

The position is: **DataJunction handles the semantic-model slice cleanly; it is not addressing ER, multi-source disagreement, or audit-walk-back-through-pinned-runs.**

### LinkML — vocabulary, not runtime

LinkML's vocabulary (class, slot, range, mixin, `permissible_values`, `is_a`, `multivalued`) is the right ontology terminology, and knot borrows it. Knot is **not** LinkML-compatible:

- No LinkML library dependency.
- No YAML import / export.
- No SHACL escape hatch.
- No OWL DL reasoner.

Why borrow vocabulary but not the runtime? Because the runtime LinkML produces is YAML-first, file-based, and oriented toward generating artifacts (Python classes, JSON Schema, SHACL constraints). Knot's authoring is *programmatic over a Pydantic metaschema, runtime-editable, with a publish gate that validates against bound impls* — a runtime LinkML does not provide. Adopting LinkML's library would inherit YAML round-trips and decouple the metaschema from the rest of knot's identity model.

The position is: **LinkML is excellent ontology terminology; the right move is to borrow the words and own the runtime.** Open-ecosystem interop (LinkML YAML output) is a non-goal unless the team specifically chooses to publish to that ecosystem, and even then it is a bound impl decision.

### SHACL — wrong primitive for tabular lake data

SHACL validates that an RDF graph conforms to declared shapes. It is a shape language, not a pipeline.

The mismatch:

- **Data residency.** SHACL assumes RDF triples. Knot's data layer is tabular lake (Spark / Trino over Iceberg / parquet). Lifting tabular data into RDF for SHACL validation, then dropping back, is overhead with no payoff for this team.
- **Validation timing.** SHACL is a check, not part of the production pipeline. Knot's structural validation is SQL emitted from the same spec that produced the data — same source of truth, same SQL generator, same run.
- **Constraint expressiveness.** Cross-class derivations and constraints in knot use the same expression-tree machinery as derivations and DataContext bodies. SHACL's primitives are different — useful for graph-shaped data, not the right fit for fact-level multi-valued contributions.

The position is: **SHACL is a check for RDF graphs; knot's structural validation is part of the pipeline that produces tabular fact data.** No SHACL escape hatch. See [`design/core-design.md`](../../../design/core-design.md) commitment 17.

### OWL DL — wrong primitive for deterministic replay

OWL DL is a formal description logic with reasoners that compute entailments — what classes a thing belongs to, what properties follow from declared characteristics, what inconsistencies the asserted graph implies. It is mathematically rich.

The mismatch:

- **Reasoner runtime is not part of the audit promise.** "Why does the graph say X" answered by "the OWL reasoner inferred it from these axioms" is not the deterministic walk-back the team needs. A reasoner is a black box from the operational audit perspective.
- **Decidability constraints slow modeling.** OWL DL's expressiveness has hard limits to keep reasoning decidable. Modeling work fights those limits.
- **Open-world assumption.** OWL is open-world by default; "we don't know that X is not a Y" is a different default from what a pipeline-driven graph wants.

The position is: **OWL DL is a reasoner; knot is a pipeline. They are not the same shape of system.** Knot has no DL reasoner. Knot's "inference" is forward-chained derivation at materialization time and backward-chained query expansion via the translator, both compiled from the same spec.

### Splink — the right shape for the ER slice only

Splink does probabilistic record linkage. Fellegi-Sunter scoring; blocking strategies; transitivity to clusters; runs at scale on Spark / DuckDB / Athena.

This is exactly the shape of the ER stage — and a bound knot ER impl can legitimately wrap Splink. The team would write a `MovieResolver(ERProtocol)` whose `resolve()` method called Splink and translated its output into the pairwise edges knot expects.

What Splink does not provide:

- The ontology that defines which classes have ER bound to them.
- The canonical-id lifecycle (mint, merge, split, audit).
- The multi-valued resolved-fact storage that survives ER changes.
- Per-`(source, property)` trust evolution.
- The cross-class pinning that lets relation classes read parent canonical_ids "as of" pinned parent runs.
- The audit walk-back primitives.

The position is: **Splink is a great candidate for an ER impl; it is not a candidate for the system around the ER impl.**

### Apache Atlas (and OpenLineage / Marquez) — sidecar, not control plane

Atlas catalogs metadata and tracks lineage. It is told about lineage events by the systems that produce data; it does not own those systems.

Two structural problems for knot's audit promise:

- **Fidelity depends on producers being honest.** A pipeline that doesn't report a step, or reports a step incorrectly, makes Atlas's lineage wrong — and Atlas has no way to know.
- **Run identity is the producer's, not the catalog's.** Atlas does not assign content-addressed identity to runs. The producer's run ID (often a timestamp or job ID) is what gets recorded. Spec drift between runs is not part of the lineage primitive.

Knot's audit promise is keepable because **knot owns the pipeline that produced the data.** The compile hash is the run's identity, and every contributing artifact is pinned into the hash by construction. There is no sidecar to drift.

The position is: **lineage tools approximate audit by capturing what producers report; knot keeps audit by being the producer and using content-addressed run identity.** A team can still publish lineage to Atlas via a bound materialization impl if they want a catalog presence — but the audit walk-back lives inside knot.

### Neo4j — target, not competitor

Neo4j (and adjacent property-graph stores) is downstream of knot. A bound `Materializer` impl reads `resolved_facts` via DataContexts and publishes nodes / edges to Neo4j (typically via S3 + LOAD CSV, sometimes via Bolt MERGE). Apps and services query the published Neo4j.

Knot does not replace Neo4j. Knot also does not require Neo4j — the same `Materializer` shape publishes to Iceberg, vector stores, parquet exports, or a mix. See [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) § "Materialization."

The position: **graph stores are publication targets, queried by consumers. The audit promise lives in knot, not in the graph store.**

---

## A worked example: the SQL the team would otherwise write by hand

Without knot, a team building a multi-source movie graph in dbt or DataJunction (or hand-rolled SQL) ends up writing something like:

```sql
-- merge: pick a winner per property, somehow
WITH movie_contributions AS (
    SELECT canonical_id, source, year, asserted_at FROM imdb_movies_normalized
    UNION ALL
    SELECT canonical_id, source, year, asserted_at FROM tmdb_movies_normalized
    UNION ALL
    SELECT canonical_id, source, year, asserted_at FROM wikidata_films_normalized
)
SELECT
    canonical_id,
    -- which year? whichever source we trust most for `year`
    -- but trust is hardcoded here:
    COALESCE(
        MAX(CASE WHEN source = 'imdb_movies' THEN year END),
        MAX(CASE WHEN source = 'tmdb_movies' THEN year END),
        MAX(CASE WHEN source = 'wikidata_extract' THEN year END)
    ) AS year
FROM movie_contributions
GROUP BY canonical_id;
```

Look at what the team has now lost:

1. The `COALESCE` order **is** the trust policy. Editing trust means editing SQL and re-running the pipeline.
2. The `MAX(CASE WHEN source = ... THEN year END)` collapses the multi-set per source — if IMDb has two contradicting `year` values for the same canonical_id, one is silently dropped before the COALESCE.
3. There is no record of which contribution won. Audit walk-back is "look in the SQL file at the time, hope it didn't change since."
4. There is no way for a downstream consumer asking `Movie.year.all()` to see all contributions; the table only has one column.
5. Fixing IMDb's trust for `year` requires re-running this CTE for every affected canonical_id, then re-publishing.
6. A correction "no, this is 1999" has to be inserted as a high-priority row in some side table that this CTE knows to consult — branching the pipeline on "is this a correction" rather than treating the correction as just another source.

Each of those losses is a hard problem from [`the-hard-problems.md`](the-hard-problems.md). The team that writes this CTE is not solving the wrong problem — they are solving the right problem with the wrong primitive.

Knot's claim is not that dbt is bad. Knot's claim is that **the right primitive for this domain is multi-valued canonical facts with query-time trust resolution, content-addressed run identity for audit walk-back, runtime-editable spec authoring, and a single-team trust posture that dissolves the defensive infrastructure that would otherwise be required.** No off-the-shelf system carries that primitive. The team would build it on top of whatever they pick. Knot is what that "build it on top" looks like when designed from the primitives instead of grown from a SQL pipeline.

---

## Open / unresolved

- **Splink-as-ER-impl integration depth.** Whether the team would wrap Splink directly in a `MovieResolver(ERProtocol)`, or build a thinner ER impl over a different linkage library (Zingg, py_entitymatching, or a custom approach), is an impl-level choice the team makes per binding. Layer 1 does not need to settle this; the position is only that Splink is a legitimate candidate for the ER slice.
- **Atlas / OpenLineage publication.** Whether the team chooses to publish a slice of knot's audit chain into a catalog like Atlas for cross-team visibility is a bound impl decision, not a knot deliverable. Layer 1 flags this as a possibility, not a commitment.

These are flagged as open because they are real choices the team will make later — not because the comparison itself is unsettled.
