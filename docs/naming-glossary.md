# Naming Glossary

Canonical names for concepts in this system. When in doubt, use the canonical form. Aliases listed are deprecated for new code, docs, and discussion.

---

## Core Data Model

| Canonical | Definition | Deprecated aliases |
|---|---|---|
| **class** | A type of entity in the ontology (e.g., Movie, Person, Studio). | type, kind, concept, node type |
| **binding** | One source's claim about one entity. Row in `<class>_bindings`. PK: `(source_name, source_identifier)`. | observation, claim, fact instance, source record, raw row |
| **canonical** | The post-matching, post-truth-resolution version of an entity. Row in `<class>_canonical`. | golden record, resolved entity, master record, fused entity |
| **slot** | A named, typed property on a class (title, year, runtime). | attribute, field, column, property |
| **source** | An origin of bindings (IMDb, TMDb, studio feed, user corrections). | publisher, provider, feed, dataset |
| **source\_identifier** | The source's native ID for a binding (`tt0110912`, `680`). | source ID, native ID, external ID, source key |
| **canonical\_id** | The system's identifier for a canonical entity. Stamped onto bindings during matching. | internal ID, master ID, fused ID, MID |
| **virtual class** | A filtered/derived view of a concrete class (e.g., "DirectedMovie" = Movie where `has_credit(role=director)`). | subtype, view, derived class, projection |

---

## Relationships

| Canonical | Definition | Deprecated aliases |
|---|---|---|
| **FK** (in inline-FK model) | A foreign key column on a class table pointing at another class's canonical_id. | reference, pointer, link, edge column |
| **edge** (in universal-bridge model) | A row in the polymorphic edges table. Has `from_class`, `from_id`, `relation`, `to_class`, `to_id`. | triple, link, fact, association, bridge row |
| **relation** | The named type of an FK or edge (`directed_by`, `produced_by`, `acted_in`). | predicate, edge type, property type, role |
| **reified relation** | A class whose instances represent relationships (Credit = (Movie, Person, role)). Has its own slots. | bridge entity, association class, link entity |

---

## Operations

| Canonical | Definition | Deprecated aliases |
|---|---|---|
| **matching** | The pipeline that decides which bindings refer to the same entity and stamps them with the same canonical_id. | entity resolution, ER, canonicalization, deduplication, identity resolution, record linkage |
| **truth discovery** | The batch process that re-computes trust weights from cross-source agreement patterns. | quality calibration, source scoring, reliability estimation |
| **trust** | A per-`(source, class, slot)` weight used by the resolver to pick winning values. Stored in the `trust` table. | weight, confidence, source quality, accuracy |
| **resolver** | The query layer that picks per-slot winners from bindings using trust weights (argmax). | fusion, merge, picker |
| **mint** | To assign a new canonical_id to a binding (or group of bindings) that doesn't match any existing canonical entity. | create, generate, allocate |
| **stamp** | To assign an existing canonical_id to a binding during matching. | tag, label, attach, link |
| **fan-out** | The post-stamp step that propagates a canonical_id change to all rows that referenced the source_identifier. | propagation, cascade, rewrite |

---

## Storage Model

| Canonical | Definition | Deprecated aliases |
|---|---|---|
| **bindings table** | `<class>_bindings`. One row per `(source, source_identifier)`. | source table, raw table, observations table |
| **canonical table** | `<class>_canonical`. One row per canonical_id. | golden table, master table, fused table |
| **valid\_from / valid\_to / is\_current** | Append-only history columns on every table. | effective dates, validity period, SCD2 columns |
| **history window** | The retention period before append-only rows are purged from Postgres. | retention, TTL |
| **flattened view** | The denormalized projection of resolved entities exported to Iceberg for warehouse consumption. | mart, dim table, analytical view |

---

## Workflow / Orchestration

| Canonical | Definition | Deprecated aliases |
|---|---|---|
| **ingest workflow** | Temporal workflow that writes bindings for a specific (source, class). | loader, importer, source job |
| **matching workflow** | Temporal workflow that runs the matching funnel for a class. | ER pipeline, dedup job, canonical assignment job |
| **funnel** | The sequence of stages within matching (embed → block → rank → decide → write). | pipeline, cascade, chain |
| **stage** | One step in the funnel, typically one activity. | step, phase, layer |
| **batch** | A group of bindings processed together through the funnel. | chunk, group, page |
| **staged proposal** | A funnel's output before the orchestrator commits it (potential matches, potential mints). Held in a staging area. | candidate, draft, pending decision |

---

## Anti-Aliases

When you hear these in discussions, redirect to the canonical form:

- "the bridge table" → if A: "the FK column on `<class>"; if B: "the `edges` table"
- "the triple store" → "the bindings + canonical layer" (we don't use RDF triple stores)
- "the ER service" → "the matching workflow"
- "the golden record" → "the canonical row"
- "the dim table" → "the flattened view in Iceberg"
- "the observation" → "the binding"
- "the fact" → context-dependent: a binding (a source claim), an edge (a relationship), or a canonical value (a resolved fact). Avoid the word — too overloaded.

---

## When to Add to This Glossary

If a new term appears in three or more docs or two or more team conversations, promote it here with a canonical form and the aliases that were in use.
