# Postgres Graph Modeling: Extended Analysis

A companion to [postgres-graph-modeling.md](./postgres-graph-modeling.md). Captures the deeper analysis: SQL/PGQ alignment, scenario-by-scenario comparison, augmentations to each design, partitioning, codegen, and the schema-first pitch.

---

## 1. SQL/PGQ Alignment

**Design A is more aligned with SQL/PGQ, not B.**

SQL/PGQ projects graph semantics onto existing typed relational tables. The whole point is *"your FK columns already define edges, just declare them as such."*

```sql
CREATE PROPERTY GRAPH content
  VERTEX TABLES (movie_canonical, person_canonical, ...)
  EDGE TABLES (
    movie_canonical AS movie_director
      SOURCE KEY (canonical_id) REFERENCES movie_canonical
      DESTINATION KEY (director_id) REFERENCES person_canonical
      LABEL directed_by
  );
```

That's literally Design A — the FK column on the entity table becomes an edge.

Design B (universal bridge) is harder to fit because the `edges` table has polymorphic `from_id`/`to_id` that don't FK to specific vertex tables, and SQL/PGQ's `SOURCE KEY ... REFERENCES <vertex_table>` requires typed FKs. You'd either constrain edges per relation type (defeats universality) or skip SQL/PGQ's declarations and roll your own projection.

The "everything is an edge" mental model is RDF/triple-store-shaped, which makes B *feel* graph-native — but SQL/PGQ is postgres's answer to graphs precisely because it doesn't ask you to restructure your data. **If you're committed to postgres + SQL/PGQ, A is the natural fit.**

---

## 2. Scenario-by-Scenario Comparison

### Add a new column to a class
- **A**: `ALTER TABLE` on bindings + canonical. Two DDLs.
- **B**: Same.
- **Verdict**: tie.

### Add a new domain with new classes and relations
- **A**: New class tables, FK columns on each. DDL per relation.
- **B**: New class tables. Relations are INSERTs into `edges`. No DDL.
- **Verdict**: B saves DDL at the cost of typed FK enforcement.

### Add a new relation between existing classes
- **A**: `ALTER TABLE ... ADD COLUMN` for the FK.
- **B**: Zero DDL. INSERT into `edges` with new `relation` value.
- **Verdict**: B's main win. Matters if relations evolve often.

### Add virtual / derived classes
- **A**: `CREATE VIEW` filtering on a typed FK. Clean SQL.
- **B**: `CREATE VIEW` with EXISTS over `edges`. Uglier, polymorphic.
- **Verdict**: A.

### Rename a slot
- **A**: `ALTER TABLE ... RENAME COLUMN`. Grep downstream.
- **B**: Same for class slots. Relation rename is `UPDATE edges SET relation=...`.
- **Verdict**: tie for slots; B's relation rename is data, not DDL.

### Many-to-many relation
- **A**: Requires a reified class anyway (no many FKs in one column).
- **B**: Insert N edge rows. No reified class needed for pure M:N.
- **Verdict**: B wins for pure M:N without metadata. Tie when the relation carries data (Credit has `role`, `billing_order` — needs a table either way).

### Query: "all relations from this entity"
- **A**: UNION across N typed queries, one per relation.
- **B**: One polymorphic query on `edges`.
- **Verdict**: B more ergonomic; A faster per-relation.

### Multi-hop traversal
- **A**: Each hop is a typed JOIN with FK + index. Planner inlines.
- **B**: Each hop is a JOIN through `edges`. Same hot table N times.
- **Verdict**: A — advantage grows with depth.

### Bulk re-stamping canonical_ids after ER merge
- **A**: UPDATE every FK column on every class referencing the old ID. Schema knowledge required.
- **B**: One `UPDATE edges SET from_id=...`, then same for `to_id`. Polymorphic, two statements.
- **Verdict**: B wins on ER cascade.

### Source disagreement on FK values
- **A**: Per-row in `<class>_bindings` with source-specific FK. Resolver picks winner via trust argmax, same as any slot.
- **B**: Disagreement spread across `edges` rows with `source_name` discriminator. Needs parallel resolution on edges.
- **Verdict**: A — uniform resolution path.

### Temporal queries
- **A**: Filter each class table by valid_from/valid_to. Per-table, narrow.
- **B**: Filter class tables + filter `edges`. Edges gets hot.
- **Verdict**: A slightly better.

### Self-referential relation (Person collaborated_with Person)
- **A**: FK column on `person_*`. M:N still needs reification.
- **B**: Edges with `from_class=Person, to_class=Person`. Natural.
- **Verdict**: B slightly cleaner.

### Cross-relation constraint ("director must be alive at release date")
- **A**: Typed JOIN movie + person via FK.
- **B**: JOIN movie → edges → person. Extra join.
- **Verdict**: A.

### Net score for a shallow-traversal CKG workload

A wins: column add (tie), virtual classes, multi-hop, source disagreement, temporal, cross-relation constraints, overall query perf.
B wins: new relation without DDL, M:N without metadata, ER bulk re-stamping, polymorphic "all relations" queries, self-references.

**A's wins are on the common path. B's wins are on rare operational events.**

---

## 3. Which Is Easier to Reason About

**Design A.**

- Schema is the documentation. `movie_canonical` tells you Movie points at Person via `director_id`. With B you can't tell what relations exist without reading runtime config or sampling rows.
- Queries look like normal SQL JOINs. B's queries route everything through one mega-table with polymorphic discriminators — closer to SPARQL than SQL.
- Type errors surface at write time in A (FK violation, wrong type). In B they surface as logically-wrong-but-syntactically-valid rows in `edges`, caught later by application code.

---

## 4. Augmenting B with a Relation Tracking Table

A common proposal: add a table that defines valid relations and use it for documentation + constraint enforcement.

### What you gain
- Schema is documented (read the metadata table for "Movie can have directed_by → Person").
- Constraint enforcement becomes possible via triggers or app-level checks.
- "What relations exist?" is a single query.

### What you don't gain
- Query performance. The planner still sees a polymorphic mega-join over `edges`; the metadata table tells the application what's valid, not the planner what's typed.

### What you newly lose
- A drift surface. Edges can be inserted that violate the metadata (failed triggers, disabled triggers, bulk loads). Metadata can describe relations no rows in `edges` actually use. Two sources of truth that can disagree.

This is essentially the RDF / OWL pattern: ontology declares valid triples, triple store enforces via SHACL. The verdict from RDF land is that runtime enforcement is fragile compared to native FK constraints.

**Net**: works as "B with better documentation." Doesn't get you "B with A's reasoning ergonomics."

---

## 5. Partitioning B

### LIST partition on `from_class` (or `relation`)
Per-class locality, planner pruning when filtering on the partition key. But adding a new class or relation requires DDL (create the partition) — same operational cost as A's `ALTER TABLE`.

### HASH partition on `relation`
- Queries filtering on `relation` get pruned (planner hashes the literal at plan time).
- New relation types hash into existing partitions automatically. Zero DDL for new edge types.
- Fixed arity at creation (e.g., 16 partitions). Multiple relations may share a partition.
- **What you still don't get**: pruning for queries that filter on `from_id` or `from_class`. "Everything connected to Pulp Fiction" still touches all partitions.
- **What's still polymorphic**: the columns. `from_id` is still text holding canonical_ids from any class. No typed FKs, no per-class indexes.

HASH-on-relation is a real improvement to B and closes the operational gap (auto-partitioning on new relations). **It does not close the reasoning or typing gap.**

---

## 6. Augmenting A with a Materialized Edges View

The symmetric move on A: derive a polymorphic edges view by UNION ALL across every FK column in every class, normalized to `(from_class, from_id, relation, to_class, to_id, source_name, valid_from, ...)`.

- One stored, indexed table that looks exactly like B's `edges`.
- A serves both shape models from one storage layer: typed FKs for fast queries, materialized edges for polymorphic traversal.
- No tracking table needed — `information_schema.columns` (or the spec) tells you what FK columns exist. The DDL itself is the source of truth.

### Refresh strategies
- Full rebuild on a schedule.
- Incremental via triggers on underlying tables (write overhead).
- CDC-driven — the same stream feeding Iceberg can feed this view. **Cleanest answer.**

### The deploy-time question
Postgres can't auto-update the view *definition* from a tracking table. `REFRESH MATERIALIZED VIEW` refreshes data, not SQL. Adding a row to a tracking table doesn't change the view's UNION until you `DROP` + `CREATE` with new SQL.

So you skip the tracking table entirely. **Regenerate the materialized view SQL from the spec (or `information_schema.columns`) at deploy time. The DDL drives codegen.**

**A + materialized edges = strict superset of B's polymorphic ergonomics, with typed-FK performance on the hot path. The only thing you don't get is "add a new relation with zero work at any layer" — but the work is now "add an FK column + the view regens on next deploy" instead of B's "add a partition + maintain metadata."**

---

## 7. Which Is More Trivially Automated for Codegen

**Design A — especially for Spring Boot.**

### Java records and JPA map cleanly to A
A's typed FK columns translate directly to `@ManyToOne @JoinColumn(name="director_id")`. Vanilla JPA. Every Spring engineer recognizes the output.

B's polymorphic `edges` table needs Hibernate's `@Any` annotation or custom mapping code. Obscure path, worse tooling, worse docs. Generated B code looks like something custom that people will want to refactor.

### A's codegen is mechanical
For each class: emit bindings DDL, canonical DDL, Java record, JPA entity. For each FK column: emit `@ManyToOne` on the entity, emit a relation row in the materialized edges UNION ALL. Walk the spec, emit per-class artifacts.

B's codegen has more moving parts: class tables (easy), edges table (one-time), partition definitions (per-class if LIST), metadata sync (separate output). More places to diverge.

### The pitch is easier with A
Strip the modeling layer to just "schema-first codegen" — same category as Protobuf, LinkML, OpenAPI codegen. Patterns the team already understands. The query language was the part that required new mental model adoption. Without it, you're shipping a single source of truth that emits DDL + JPA entities + Java records + Iceberg view definitions.

A makes the codegen output idiomatic enough that the team can read it and immediately understand it — which makes the pitch much easier.

---

## 8. Recommendation

**Use Design A as the storage model. Add a materialized edges view (CDC-refreshed) on top for polymorphic queries when needed. Codegen everything from the spec at deploy time — DDL, JPA entities, Java records, Iceberg views.**

This is the postgres-native answer (aligns with SQL/PGQ), the workload-correct answer (shallow-traversal CKG), the Spring-friendly answer (vanilla JPA mapping), and the easiest pitch to the team (schema-first codegen, no new mental model).
