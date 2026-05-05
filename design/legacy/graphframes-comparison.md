---
title: GraphFrames vs knot reified relations
status: note
project: knot
tags: [research, graphframes, spark, materialization]
created_at: 2026-04-28T00:00:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# GraphFrames vs knot reified relations

Research-only comparison of Apache Spark's GraphFrames library against
knot's reified-relations design. Goal: surface any implication for
knot's lake-output shape that would change the architecture-v1 doc.

Source evidence is taken directly from the cloned upstream at
`/home/nick/code/knot/refs/graphframes/` (commit shallow-cloned
2026-04-27). All file/line citations below refer to that tree.

## GraphFrames data model

**Two flat DataFrames, hardcoded column names, single shape.**

The core type is `GraphFrame(vertices: DataFrame, edges: DataFrame)`
defined in
`refs/graphframes/core/src/main/scala/org/graphframes/GraphFrame.scala`.
Constants near line 1135 fix the contract:

- `GraphFrame.ID = "id"` — required column on `vertices`.
- `GraphFrame.SRC = "src"` — required column on `edges`.
- `GraphFrame.DST = "dst"` — required column on `edges`.
- `GraphFrame.EDGE = "edge"` (used in `triplets`).
- `GraphFrame.WEIGHT = "weight"` (used by weighted algorithms).

The `apply(vertices, edges)` factory at line 1182 enforces these
columns with `require(...)` and otherwise treats *all other columns as
attributes*:

> "Vertex DataFrame. This must include a column 'id' containing
> unique vertex IDs. All other columns are treated as vertex
> attributes." (line 1174)
> "Edge DataFrame. This must include columns 'src' and 'dst'... All
> other columns are treated as edge attributes." (line 1177)

There is no per-row "kind" / "label" mechanism baked into the type.
Both DataFrames are flat Spark `DataFrame`s with one Spark schema each.

**Heterogeneous types: not supported at the core layer.** A vertex
DataFrame is *one* Spark DataFrame, so it has *one* schema. You can
add nullable columns to fake it, but every vertex row carries every
column. GitHub issue #111 confirms this is the documented behaviour —
a `Person(id, name, age)` row and a `Company(id, name)` row cannot
share a vertex DataFrame because the schema must apply to all rows.

**Edge cardinality: strictly binary.** `src` and `dst` are required
and there is no third-participant column. Motif finding
(`GraphFrame.find`, line 601) is built on a DSL whose basic unit is
`(a)-[e]->(b)` — a binary edge. Multi-edge motifs like
`(a)-[e1]->(b); (b)-[e2]->(c)` are *joins of binary edges*, not
ternary edges. There is no hyperedge construct in the source or docs;
search of the cloned repo and `04-user-guide/` returned zero hits for
"hyper", "ternary", "n-ary", or "3+ participants".

**Edge properties: arbitrary additional columns.** Not a map column,
not typed; just plain Spark columns added to `edges`. Same model as
vertices. Algorithms that need a specific column (PageRank's
`weight`, etc.) read it by reserved name (see
`docs/src/04-user-guide/14-special-columns.md`).

### PropertyGraphFrame: the heterogeneous-graph layer

GraphFrames *does* ship a thin abstraction over the flat shape:
`org.graphframes.propertygraph.PropertyGraphFrame`
(`core/src/main/scala/org/graphframes/propertygraph/PropertyGraphFrame.scala`).
It introduces:

- `VertexPropertyGroup(name, data, idCol, applyMaskOnId)` — one per
  entity kind, each with its own DataFrame schema.
- `EdgePropertyGroup(name, data, srcGroup, dstGroup, isDirected,
  srcCol, dstCol, weightCol)` — one per relation kind, typed by the
  vertex groups it connects.
- `PropertyGraphFrame(vertexGroups, edgeGroups)` — holds the
  collection.

But to actually run any GraphFrames algorithm you must call
`toGraphFrame(vertexGroupNames, edgeGroupNames, edgeFilters,
vertexFilters)` (line 70), which:

1. Filters each selected vertex group, then `reduce(_ union _)`s them
   into one DataFrame (line 80–82).
2. Filters each selected edge group, then `reduce(_ union _)`s them
   into one DataFrame (line 84–86).
3. Returns a plain `GraphFrame(vertices, edges)`.

The class doc literally says "*Internal ID generation and collision
prevention by hashing vertex/edge IDs with their group names*" and
"*Merging of different vertex types into a unified vertex
DataFrame*". The unified DataFrame carries a `property_group` column
(`PROPERTY_GROUP_COL_NAME`, line 198) as a discriminator and an
`external_id` column for reverse-mapping back via `joinVertices`.

So PropertyGraphFrame is essentially "the union view that knot would
need to build" — already specified, in-tree, with conventions for ID
hashing and a discriminator column. It is *not* a separate engine —
it's syntactic sugar that compiles to the canonical
(vertex DataFrame, edge DataFrame) shape that all algorithms consume.

## Mapping from knot's per-class layout to GraphFrames

Knot materializes one per-source + one resolved table per ontology
node (`knot-architecture-v1.md` §"Lake schema pattern"). Entity
classes and reified relation classes are both first-class ontology
nodes, each with its own LinkML schema.

To feed knot's resolved layer into GraphFrames a Spark consumer must:

1. **Vertices.** UNION the resolved tables of every entity class:
   ```sql
   SELECT id, 'Person'  AS __kind, name, birthdate, NULL AS title, ...
   FROM knot.person_resolved
   UNION ALL
   SELECT id, 'Movie'   AS __kind, NULL AS name, NULL AS birthdate, title, ...
   FROM knot.movie_resolved
   UNION ALL ...
   ```
   Required: `id`. Recommended: a `__kind` discriminator (matches
   GraphFrames' `property_group` convention). All other columns
   become nullable per-kind attributes.

2. **Edges.** For each binary reified relation class, project to
   `(src, dst, ...props)` then UNION:
   ```sql
   SELECT cm.actor_id   AS src,
          cm.movie_id   AS dst,
          'CastMembership' AS __kind,
          cm.character_name,
          cm.billing_order
   FROM knot.castmembership_resolved cm
   UNION ALL
   SELECT d.director_id AS src,
          d.movie_id    AS dst,
          'DirectedBy'  AS __kind,
          NULL AS character_name,
          NULL AS billing_order
   FROM knot.directedby_resolved d
   UNION ALL ...
   ```

3. ID-collision prevention. Knot's per-class IDs are unique within
   class but not necessarily across classes. The PropertyGraphFrame
   pattern is to hash `(group_name, id)` to a global ID. Knot
   consumers either need to do the same in their UNION, or knot can
   pre-assign globally-unique IDs at materialize time.

The projected schemas have to be flat: every column appearing in any
contributing per-class table needs to exist in the union, with
`NULL` for non-applicable rows. Spark `unionByName(allowMissingColumns
= true)` covers this without manual padding.

## Gaps and frictions

**Where reified relations map cleanly:**

- *Binary reified relations.* A `CastMembership(actor_id, movie_id,
  character_name, billing_order)` table maps directly to a row
  `(src=actor_id, dst=movie_id, character_name, billing_order)` —
  reified relation properties become edge columns 1:1. No information
  loss.
- *Per-class tables → per-group property frames.* knot's
  per-class-table layout maps directly to PropertyGraphFrame's
  `VertexPropertyGroup` / `EdgePropertyGroup` per-group DataFrame
  layout. They are isomorphic in spirit: one DataFrame per ontology
  class, schema typed by the class.

**Where they don't:**

- *N-ary relations (3+ participants).* GraphFrames cannot represent
  these natively. See dedicated section below.
- *Provenance columns.* GraphFrames algorithms ignore extra columns
  but they do flow through `triplets` and motif results. A
  `_source_id` / `_trust_at_materialization` column on every edge is
  fine for read-side queries; algorithms like PageRank and connected
  components simply don't consume it. See "Provenance survival".
- *Schema heterogeneity at the algorithm boundary.* A typed
  pattern-matching query like "find Person—WORKED_AT—Company" requires
  filtering the unified DataFrame on `__kind`. GraphFrames doesn't
  type-check the motif against per-kind schemas — it joins on
  `src`/`dst` and the user filters by attribute. This is fine but
  slightly weaker than what a typed graph engine (Neo4j, JanusGraph)
  would offer.

## N-ary relations

**Knot reifies all relations.** For binary `CastMembership(actor,
movie)`, the reified class has slots `actor_ref`, `movie_ref` plus
properties — naturally maps to a binary edge.

For ternary `WonAwardFor(person, movie, award)`, knot's reified
class has three participant slots: `person_ref`, `movie_ref`,
`award_ref`. **GraphFrames has no native representation for this.**
Two consumer-side options, neither costless:

1. **Reify as a synthetic vertex.** Treat the relation instance
   itself as a vertex of kind `WonAwardFor`, then emit three binary
   edges from that synthetic vertex to each participant:
   ```
   vertex (id=wf_123, __kind='WonAwardFor', year=2024)
   edge   (src=wf_123, dst=person_X,  __kind='wf_person')
   edge   (src=wf_123, dst=movie_Y,   __kind='wf_movie')
   edge   (src=wf_123, dst=award_Z,   __kind='wf_award')
   ```
   This is consistent with knot's reification choice — the relation
   is *already* a first-class ontology node; the GraphFrames-layer
   simply mirrors that. Pros: lossless, queryable via motif
   `(p)<-[]-(wf)-[]->(m); (wf)-[]->(a)`. Cons: graph-algorithm
   semantics shift (e.g. PageRank flow now passes through the
   relation node).

2. **Project to pairwise binary edges.** Emit the three edges
   `(person, movie)`, `(person, award)`, `(movie, award)` annotated
   with the same `__rel_id`. Pros: keeps graph algorithms acting on
   "real" entities. Cons: lossy (cannot reconstruct the n-ary fact
   from a single edge), bloats edge count, requires consumer to know
   to use `__rel_id` for grouping.

**Recommendation for knot's design.** Don't try to hide the n-ary
problem at the lake layer. Knot's reified class is the source of
truth; the GraphFrames-view materialization should mirror it via
option (1). The relation-as-vertex pattern is exactly what
PropertyGraphFrame's `applyMaskOnId` was designed for — it expects to
hash IDs across vertex groups including pseudo-vertex groups.

This is a clean *consequence* of reified relations, not a friction.
The friction would be the other way around: if knot stored binary
relations as edge columns, n-ary relations would be impossible to
represent without an out-of-band convention.

## Provenance survival

Knot's per-source layer carries `materialized_at`, `source_id`,
`correction_id`, `trust_at_materialization` per fact. The resolved
layer is a window-function pick from per-source.

**GraphFrames has zero native provenance support.** The library
treats extra columns as opaque attributes. They:

- *Survive joins and motif results.* `triplets` (line 309) and `find`
  (line 601) nest the full vertex/edge schema as a struct, so
  `motifs.select("e.character_name", "e._source_id")` works.
- *Are ignored by algorithms.* PageRank, connected components, BFS,
  label propagation, etc. read only `src`/`dst` (and optionally
  `weight`). They do not propagate provenance into their outputs.
- *Are dropped by some operations.* `dropIsolatedVertices`,
  `filterVertices`, `filterEdges` preserve schemas, but the
  algorithm-output DataFrames (e.g., `pageRank.run().vertices`)
  re-attach the original vertex columns via join — confirmed in
  `GraphFrame.scala` and the lib classes.

**Practical consequence for knot.** If the consumer feeds the
*resolved* layer into GraphFrames, they get a clean graph and lose
provenance for graph-algorithmic results (PageRank scores aren't
attributed to which source supplied each edge). If they feed the
*per-source* layer, they get duplicate edges per source — wrong
semantics for most graph algorithms.

The right pattern is: graph algorithms run on resolved; provenance
re-joined post-hoc by the consumer using knot's `entity_id` /
`relation_id` keys to link back to the per-source layer. This works
*because* knot keeps the per-source and resolved layers as separate
materializations addressable by ID.

## Implications for knot's architecture

1. **The reified-relations choice is GraphFrames-friendly, not
   hostile.** Each reified relation class becomes one
   `EdgePropertyGroup` (or one row-class in the unified edge
   DataFrame), with relation properties as extra columns. The mapping
   is mechanical and lossless for binary relations.

2. **Knot's per-class lake table layout aligns with
   PropertyGraphFrame's per-group DataFrame model.** Both are "one
   DataFrame per kind, typed by that kind". The translation is a
   UNION with discriminator + ID hashing — already a standard pattern
   in the GraphFrames codebase.

3. **N-ary relations are representable but consumer-side
   non-trivial.** This is *not* a knot architecture problem — knot
   already represents n-ary relations correctly via reification. It's
   a consumer-side decision (relation-as-vertex vs. binary
   projection) that knot can ship as a documented recipe rather than
   as a core change.

4. **Provenance does not survive GraphFrames algorithms.** This is
   inherent to GraphFrames, not fixable in knot. Knot's split into
   per-source + resolved layers is exactly the right shape: feed
   resolved into algorithms, re-join per-source for explanation.

5. **Reserved column names are a constraint.** `id`, `src`, `dst`,
   `weight`, `attr`, `new_id`, `new_src`, `new_dst`, `MSG`,
   `_pregel_msg`, `_pregel_is_active`, `pagerank`, `component`,
   `label`, `distances`, `count`, `outDegree`, `inDegree`, `degree`,
   `column1..4` (see
   `refs/graphframes/docs/src/04-user-guide/14-special-columns.md`).
   Knot LinkML slot names that collide with these would need
   projection-time renaming when materializing the GraphFrames view.
   Worth a static check.

## Recommended changes (if any)

**Modest, optional.** None of these are blocking; the architecture as
written supports GraphFrames consumers without modification. But
adding them would lower friction:

1. **Document a "GraphFrames-shape view" recipe in knot's docs.**
   Show the canonical SQL: UNION across entity-class resolved tables
   to produce `vertices(id, __kind, ...)`; UNION across binary
   relation-class resolved tables to produce `edges(src, dst, __kind,
   ...)`. The translator API (`POST /translate/...`) is the natural
   home if knot wants to emit this SQL on demand. **No code change to
   the materialization layer** — this is a query-time UNION view, not
   a third lake table.

2. **Optionally ship a `graph__vertices` / `graph__edges` pair as a
   first-class materialization target.** Pros: zero work at consumer
   side, bounded write latency, can pre-hash IDs. Cons: extra storage
   (3rd copy of the data), schema drift any time an entity class is
   added/removed, conflicts with knot's stated boundary "knot does
   not own lake writing — orchestrator's Spark job does". Verdict:
   skip for v1, prefer the view recipe in option 1; revisit only if
   real consumers complain about UNION cost.

3. **Add a static check for GraphFrames reserved-name collisions.**
   When a LinkML class declares a slot named `id`, `src`, `dst`,
   `weight`, etc., the static pre-flight validator (already
   architected, see `knot-architecture-v1.md` §"Static pre-flight
   validation") could warn that this slot will need renaming in any
   GraphFrames-shape projection. This is a one-liner in the
   ontology-conformance check; cheap and prevents downstream
   surprises.

4. **Document the n-ary GraphFrames recipe.** Two-paragraph note in
   the consumer docs: "If your reified relation has 3+ participants,
   project it to GraphFrames as a synthetic vertex of the relation
   class plus N binary edges to participants." Knot's resolution is
   to *not* attempt to flatten n-ary into binary at the lake layer.

5. **Don't change the materialization compiler or the per-class lake
   table layout.** The reified-relations design is correct for
   GraphFrames consumers as-is. The PropertyGraphFrame source code
   confirms: even GraphFrames itself thinks the right way to handle
   heterogeneous schemas is per-group typed DataFrames + a runtime
   UNION step.

## Example end-to-end use case (PageRank on the knot graph)

A Spark job that wants PageRank scores for all entities in the
knot-published graph would do:

```python
from pyspark.sql import SparkSession, functions as F
from graphframes import GraphFrame

spark = SparkSession.builder.appName("knot-pagerank").getOrCreate()

# 1. Fetch the current knot version pointer (so the snapshot is
#    consistent across reads). E.g., one HTTP call to knot's API:
#       GET /node/Movie/current_version
#    -> "v1.7"
# Then read the resolved tables at that revision.

# 2. UNION entity classes into a single vertex DataFrame.
person   = spark.read.parquet("s3://lake/knot/person_resolved/v1.7/")\
              .select(F.col("entity_id").alias("id"),
                      F.lit("Person").alias("__kind"),
                      "name", "birthdate")
movie    = spark.read.parquet("s3://lake/knot/movie_resolved/v1.7/")\
              .select(F.col("entity_id").alias("id"),
                      F.lit("Movie").alias("__kind"),
                      "title", "release_year")
award    = spark.read.parquet("s3://lake/knot/award_resolved/v1.7/")\
              .select(F.col("entity_id").alias("id"),
                      F.lit("Award").alias("__kind"),
                      "name", "category")

vertices = person.unionByName(movie,  allowMissingColumns=True)\
                 .unionByName(award,  allowMissingColumns=True)

# 3. UNION binary reified relations into the edge DataFrame.
cast = spark.read.parquet("s3://lake/knot/castmembership_resolved/v1.7/")\
            .select(F.col("actor_ref").alias("src"),
                    F.col("movie_ref").alias("dst"),
                    F.lit("CastMembership").alias("__kind"))
directed = spark.read.parquet("s3://lake/knot/directedby_resolved/v1.7/")\
                .select(F.col("director_ref").alias("src"),
                        F.col("movie_ref").alias("dst"),
                        F.lit("DirectedBy").alias("__kind"))

# 4. For ternary WonAwardFor, project as relation-as-vertex.
waf = spark.read.parquet("s3://lake/knot/wonawardfor_resolved/v1.7/")
waf_vertices = waf.select(F.col("relation_id").alias("id"),
                          F.lit("WonAwardFor").alias("__kind"))
waf_edges = waf.select(F.col("relation_id").alias("src"),
                       F.col("person_ref").alias("dst"),
                       F.lit("waf_person").alias("__kind"))\
       .unionByName(waf.select(F.col("relation_id").alias("src"),
                               F.col("movie_ref").alias("dst"),
                               F.lit("waf_movie").alias("__kind")),
                    allowMissingColumns=True)\
       .unionByName(waf.select(F.col("relation_id").alias("src"),
                               F.col("award_ref").alias("dst"),
                               F.lit("waf_award").alias("__kind")),
                    allowMissingColumns=True)

vertices = vertices.unionByName(waf_vertices, allowMissingColumns=True)
edges    = cast.unionByName(directed, allowMissingColumns=True)\
               .unionByName(waf_edges,  allowMissingColumns=True)

# 5. Build the GraphFrame and run PageRank.
g = GraphFrame(vertices, edges)
ranked = g.pageRank(resetProbability=0.15, maxIter=10)

# 6. Filter back to the entity kinds the user cares about.
ranked.vertices.filter(F.col("__kind") == "Person")\
       .orderBy(F.col("pagerank").desc()).show(20)
```

Cost surface: 3-4 reads per entity/relation class, one UNION per
side, then standard GraphFrames execution. The UNIONs are
schema-tolerant (`unionByName(allowMissingColumns=True)`); each
contributing DataFrame stays small enough to push down predicates at
the Parquet level.

If knot ever ships option 2 above (pre-materialized
`graph__vertices` / `graph__edges` tables), this simplifies to two
`spark.read.parquet(...)` calls and a `GraphFrame()` constructor.

## Open questions

1. **Is there a real consumer for the GraphFrames-shape view?** None
   identified yet. Until one shows up, the recipe-in-docs approach is
   strictly cheaper than a third materialization target.

2. **Does knot want to emit pre-hashed global IDs at materialization
   time?** Currently per-class IDs are unique within class. Either
   knot does the hashing (which couples knot to GraphFrames'
   conventions), or every consumer does it (which means consumers
   that want to *join* knot data with non-graph data may pick
   different hashing). Defer; observe consumer behaviour first.

3. **How does the translator handle GraphFrames-aware queries?** The
   architecture lists GraphQL/Cypher/SPARQL translators. Cypher
   especially has a graph-shape that maps directly to GraphFrames.
   Could the Cypher translator emit GraphFrames motif-DSL `find`
   strings as an alternative target? Out of scope for v1; flag for
   future translator work.

4. **What happens when an ontology class is renamed or split?** The
   `__kind` discriminator embeds the class name into the data plane.
   Schema-evolution policy is an existing open item in
   `knot-architecture-v1.md` §"Open design items"; the GraphFrames
   view inherits whatever knot decides there.

5. **PropertyGraphFrame as a reference implementation pattern.** It's
   worth re-reading
   `core/src/main/scala/org/graphframes/propertygraph/PropertyGraphFrame.scala`
   when knot drafts the GraphFrames-view recipe — it has settled
   conventions for ID hashing, group filters, and `joinVertices`-style
   reverse-mapping that knot's docs can lean on rather than reinvent.
