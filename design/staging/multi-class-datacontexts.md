# Multi-class DataContexts — primary as a list of classes or derived slots

**Status:** decided. Integrates with commitments 4, 10, 15. Closes the question of how a single Materialization impl publishes whole-graph (or graph-subset) views to a target.

---

## What changed

`DataContext.primary` accepts a list, not just a single class:

```python
primary: OntologyClass | list[OntologyClass] | DerivedSlot | list[DerivedSlot]
```

Single-class case is unchanged. List case fans out at fulfill time to N typed views, one per class (or one per derived slot). The impl receives a dict-shaped handoff: `{cls: view, ...}` (or `{slot: edge_view, ...}`).

This is one expression-tree extension — a `MultiPrimary` node walked by the same single-dispatch visitors as everything else (commitment 10). Not a new abstraction; not a parallel meta-structure.

## The three shapes

### 1. Single class (unchanged)

```python
movies: DataContext = DataContext(primary=Movie)
```

→ ONE view at fulfill:

```
resolved_facts/Movie
├─ canonical_id      str
├─ year              int        # trust-resolved per slot.resolution_policy
├─ title             str
├─ runtime_minutes   int
└─ ...
```

### 2. Multi-class (the real extension)

```python
core: DataContext = DataContext(primary=[Movie, Person, Credit])
```

→ THREE views, identical shape to (1) but N of them:

```
resolved_facts/Movie    canonical_id | year | title | runtime_minutes | ...
resolved_facts/Person   canonical_id | name | birthdate | ...
resolved_facts/Credit   canonical_id | person_canonical_id | work_canonical_id | role | ...
```

Impl receives `{Movie: <view>, Person: <view>, Credit: <view>}`. Iterates and writes per class.

### 3. Whole graph (list-comp over `spec.classes`)

```python
all_classes: DataContext = DataContext(primary=spec.classes)

# Or with a subtraction:
public: DataContext = DataContext(
    primary=[c for c in spec.classes if c != Identifier],
)
```

→ N views, same shape as (2), one per class in the resolved set. Spec edits adding a class flow into the next compile automatically (`spec.classes` is the live typed entity tree; commitment 2).

No new `Graph` symbol, no `ClassSet` algebraic type, no operator overloading. `spec.classes` is `list[OntologyClass]` — plain Python set ops over real Pydantic refs are sufficient.

## Joins via slot.range traversal

DataContext expressions already traverse `slot.range`. Anything writable as a slot path becomes a JOIN in the generated SQL:

```python
# Project Movie + its director's name and birthdate via the derived slot
denormalized: DataContext = DataContext(
    primary=Movie,
    project=[
        Movie.canonical_id,
        Movie.title,
        Movie.director.name,        # → JOIN through Credit, Person
        Movie.director.birthdate,
    ],
)

# Filter through related class
big_recent: DataContext = DataContext(
    primary=Movie,
    where=(Movie.year > 2000) & (Movie.director.country.name == "USA"),
)
```

Both compile to JOINs. The impl sees a single denormalized view at fulfill.

## Derived-edge views

A derived slot like `Movie.director` (defined as `exists Credit(person=Person, work=Movie, role='director')`) materializes as its own edge view, not as a column on `Movie`:

```python
director_edges: DataContext = DataContext(primary=Movie.director)
```

→ ONE edge view:

```
movie_director_edges
├─ movie_canonical_id    str    # source canonical_id
└─ person_canonical_id   str    # destination canonical_id
```

knot's SQL gen forward-chains from the derivation rule; the materializer impl writes `(:Movie)-[:DIRECTED_BY]->(:Person)` (Neo4j) or whatever its target needs.

A list of derived slots fans out the same way:

```python
all_edges: DataContext = DataContext(primary=[
    Movie.director,
    Movie.actors,
    Movie.writers,
    # ...
])
```

→ N edge views, one per derived slot.

## Reference impl shape — Neo4j publisher

```python
class Neo4jPublisher(MaterializerProtocol):
    Config: ClassVar[type] = Neo4jConfig

    nodes: DataContext = DataContext(
        primary=[c for c in spec.classes if c not in Config.exclude_classes],
    )
    edges: DataContext = DataContext(
        primary=Config.derived_edges,   # list[DerivedSlot]
    )

    def materialize(self, ctx, nodes, edges):
        for cls, view in nodes.items():
            ctx.materializer.write_csv(
                f"s3://{Config.bucket}/nodes/{cls.name}.csv", view,
            )
        for slot, view in edges.items():
            ctx.materializer.write_csv(
                f"s3://{Config.bucket}/edges/{slot.parent.name}_{slot.name}.csv", view,
            )
        # Impl fires admin-import or LOAD CSV via Bolt
        self._bulk_load_neo4j(...)
```

~30 lines. Spec adds a class → next compile adds it to `nodes` automatically (assuming it isn't in `Config.exclude_classes`). Same shape works for Neptune (vertex/edge CSVs + `aws neptune-bulk-loader`); RDF mode for Neptune swaps the file format (Turtle / N-Triples) but keeps the same DataContext shape.

## Single-table whole-graph — possible but not the typical pattern

A single denormalized table — heavy JOINs across many classes into one wide view — is doable:

```python
mega: DataContext = DataContext(
    primary=Movie,
    project=[Movie.canonical_id, Movie.title, Movie.director.name, ..., Movie.studio.name, ...],
)
```

But it's not what graph stores actually want. Neo4j and Neptune both want **separate node and edge artifacts**. The single-table pattern (e.g., Hugegraph's "vertex-and-edge in one wide CSV" mode) is supported but loses per-class clarity for a brittle row format. Use it only when the target genuinely consumes a single denormalized shape.

## What the multi-class extension does NOT add

- No `Graph` symbol — `spec.classes` is the whole graph (commitment 2: typed entity tree IS the graph).
- No `ClassSet` algebraic wrapper — list / list-comp over `spec.classes` is sufficient.
- No `+`/`-`/`&` operator overloading on classes — Python list ops cover it.
- No new fulfillment lens — the per-class fan-out reuses the existing single-class fulfillment N times.

## What's still implementation detail

- The exact dict key in the handoff (`{cls: view}` vs positional) — code-shape choice.
- Whether `nodes.items()` is a typed iterator with `(OntologyClass, View)` pairs or a plain dict — same answer either way.
- Per-target edge-CSV format (Neo4j admin-import vs LOAD CSV vs Bolt MERGE) — impl-side choice.
- Whether the impl can write multiple targets from one publish run — already supported (commitment 15: one impl can span multiple targets).

## Cross-references

- `core-design.md` § 4 (universal DI seam) — `DataContext.primary` accepts a list; the seam is unchanged.
- `core-design.md` § 10 (one unified expression tree) — `MultiPrimary` is one new node walked by the same visitors.
- `core-design.md` § 15 (free-form materialization) — confirmed; this doc shows the canonical multi-class shape.
- `staging/di-input-contract.md` — DataContext shape now includes the list-primary case.
- `staging/auto-generated-sdk.md` — slot-range traversal joins remain the canonical pattern.
- `staging/derivation-and-constraints.md` — derivation rules drive the edge-view SQL.
- `staging/pipeline-stages.md` — Materialization section incorporates this shape.
- `staging/incremental-execution.md` — multi-class views compose with per-stage cache keys.
