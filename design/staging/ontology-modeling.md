# Ontology modeling principles

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How to decide what becomes a class, what becomes a property, and how derived relations work.

## Subclassing

Use subclassing when entities have **genuinely different shapes**. Avoid subclassing for **role-flavored distinctions**.

### Structural subclassing — yes, RDFS-style

`Movie`, `Series`, `Episode`, `Game` are distinct classes with distinct shapes:

- Movie has `runtime`; doesn't have seasons/episodes.
- Series has `season_count`, `episode_count`; doesn't have a single runtime.
- Episode has `parent_series`, `season_number`, `episode_number`.
- Game has `platforms`, `publisher`; runtime isn't a comparable concept.

These can be flat (no shared parent) or organized under a `Title` / `CreativeWork` superclass with `Movie rdfs:subClassOf Title`. Either is fine. Real entity-type distinctions, real subclassing.

### Role-flavored "subclassing" — avoided

`Director`, `Actor`, `Producer`, `Composer` are NOT subclasses of `Person`. They live as values of `Credit.role`. Reasoning:

- Same person is Director on one project, Actor on another. Role is *relational*, not identity.
- Director-the-class would carry no properties beyond Person + the directing context.
- The data already comes role-discriminated in Credit rows, not as type assertions.

Schema.org parallel: `Movie` / `TVSeries` / `TVEpisode` are classes; `actor` / `director` are properties on `CreativeWork`, not classes.

## Derived properties

Storage shape (Credit reified) and query shape (`Movie.director` as a relation) are **decoupled**. The bridge is an ontology-level rule, not a translator hack.

A derivation rule, conceptually:

```
Movie.director := Person where exists Credit(person=Person, work=Movie, role='director')
```

Same pattern for `actor`, `writer`, `producer`, `composer` — each is a derived property defined by a Credit-role rule.

### Where the rule lives

In knot's **Pydantic spec model** — as a `derivation` field on the `Slot` model (or wherever the rule attaches structurally). The spec is authored programmatically (SDK / UI), not via YAML. Versioned and content-addressed alongside the rest of the ontology spec.

Pydantic's parse-time validation handles error reporting: typos and shape mismatches surface as `ValidationError` with field path and source-line context — no separate annotation registry, no walk over arbitrary string keys.

In-spec references between metaschema entities are real Pydantic object refs (`Slot.range: TypeDefinition | OntologyClass`, `OntologyClass.slots: list[Slot]`, etc.). Name-keyed flattening happens at the persistence boundary (postgres JSONB write/read), not in the in-memory shape. See `spec-model.md` § "References".

### How the rule executes

What knot actually needs from a derivation rule is **SQL** — forward-chained at materialization, backward-chained by the translator, used for validation. Not OWL or SHACL on the runtime path.

**Knot owns the SQL generator.** A custom codegen module in knot core reads knot's Pydantic spec models (including `derivation` fields on slots) and emits SQL for:

- **Forward-chaining at materialization** — knot generates SQL producing the derived rows from `resolved_facts`. The materializer runs the SQL against the lake (Spark / Trino) and writes the resulting rows to its target (Neo4j edges, Iceberg tables, parquet partitions, vector store, etc.) using its own machinery.
- **Backward-chaining via the translator** — the translator expands consumer ontology queries into SQL JOINs against the underlying tables (Credit + filter + join to Person) at query time.
- **Validation** — SQL queries / constraints that check data conformance to derivation rules.

**Under the hood: AST-based SQL builder.** Knot's SQL generator builds SQL programmatically via an AST rather than string concatenation. **Targets: Spark and Trino** — common lakehouse SQL engines (Spark for batch / Iceberg writes; Trino for interactive / federated queries). Other dialects aren't on the roadmap.

Tool options for the AST layer:

- **sqlglot** — supports Spark and Trino dialects natively; AST + emitters; used by SQLMesh and Splink. Strong default.
- **pypika** — Python query builder; multi-dialect, but Spark / Trino aren't its primary targets.
- **pyspark / koalas** — Spark-native; no Trino path.
- **Roll our own AST + emitters** — full control; real implementation work.

sqlglot is the strongest default. We don't lean on its multi-dialect transpilation feature; we use the AST and the Spark/Trino emitters directly. CI tests both emit paths against representative SQL with semantic-equivalence checks.

What this rules out: multi-backend SQL transpilation as a knot feature, tier lists, golden corpora across N dialects, translation-correctness gates beyond Spark and Trino. Knot is a Spark + Trino SQL platform on the lake side — not a multi-backend SQL platform.

**In-flight corrections (runtime overlay, not SQL gen).** Between pipeline runs, user corrections in postgres-control aren't yet in the lake's `resolved_facts`. The translator / viewing impl needs to mix them at query time:

1. Knot generates lake SQL (Spark / Trino) → fetches the multi-valued canonical view.
2. Knot queries postgres-control for pending corrections affecting the relevant canonical_ids.
3. Knot merges the two contribution sets in Python; trust resolution picks defaults.
4. Returns the merged result to the impl.

The impl never sees the federation. The lake-side SQL stays scoped to Spark / Trino; the corrections overlay happens at the runtime layer, not at the SQL-gen layer.

**Authoring is programmatic — Pydantic everywhere.** Users author the spec via UI / API (no YAML). The spec lives as Pydantic models in-memory, on the API surface, and in storage (JSONB at the persistence boundary). The vocabulary borrows from LinkML (class / slot / range / mixin / `permissible_values` / etc.) because it's good ontology terminology — knot is not LinkML-compatible and there is no LinkML library dependency.

Knot's spec-layer responsibilities:

- **A SQL generator** (per `sql-generation.md`) — emits forward-chain / backward-chain / validation SQL from the Pydantic spec.
- **An SDK generator** (per `auto-generated-sdk.md`) — emits typed Python classes with query-expression behavior from the Pydantic spec.

### Why the rule lives in the spec (not in bound impls)

- **One rule; all consumers stay consistent.** The translator's backward-chained SQL and the materializer's forward-chained SQL come from the same ontology source. They can't drift apart silently.
- **Self-describing ontology.** Agents/LLMs reading the spec see `Movie.director` as a queryable relation with a declared derivation. They don't need to know it's reified underneath.
- **Versioned + audited.** Rule changes are spec edits; they participate in the same compile-hash + audit-walk-back machinery as the rest of the ontology.

## Property-based class inference

Class-membership inference ("X is a Director iff X has a directing Credit") isn't ruled out for things that genuinely warrant a class — e.g., a hypothetical `Filmmaker` class meaning "Person with directing AND writing AND producing credits." Same machinery as derived properties: a derivation-rule field on the Pydantic spec + the SQL generator. Not yet designed.

## Why a custom Pydantic spec model

Engineer-friendly programmatic authoring with ontology semantics (classes, slots, ranges, identifiers, mixins, subclasses, relationships) and Spark / Trino SQL codegen. The right level between pure ORM (database-flavored) and pure logic (OWL/SHACL).

Alternatives considered and why they don't fit:

| Alternative | Why not |
|---|---|
| SQLAlchemy / Django ORM | Database-first, not ontology-first. No `subClassOf`, no semantic layer. |
| Pure Pydantic (no metaschema) | Just data validation. No ontology semantics — would need to bolt on classes / slots / ranges / mixins as a parallel structure anyway. |
| Prisma | Relational schema, not ontology. |
| ProtoBuf / Avro | Serialization formats. Schema, but no ontology semantics. |
| Pure OWL / SHACL / TTL | Engineer-hostile. Steep curve. Heavy for our purposes. |
| JSON Schema | No ontology semantics; structural validation only. |
| Schema.org | A vocabulary, not a language. |
| LinkML | YAML-first authoring; LinkML-spec as runtime dependency adds API and version-coupling overhead. We borrow LinkML's vocabulary (class / slot / range / mixin) as starting reference, but ship our own Pydantic-native metaschema. |

Knot's spec model is engineer-friendly, ontology-aware, and Spark/Trino-native — purpose-built rather than retrofitted.

## Summary

| Distinction | Modeling approach |
|---|---|
| Different entity shapes | Separate classes; subclassing if hierarchical |
| Different roles for same entity | Property of a reified relation (Credit.role) |
| Convenience query as a direct relation | Derived property in ontology (rule over reified relation) |
| Class with derived membership | Derivation rule on Pydantic spec + SQL generator (same machinery as derived properties; not yet designed) |
