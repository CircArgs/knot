# 5. Pipeline and sources

Three commitments — 9, 10, and 11 — define the boundary between knot and the team's data infrastructure (where knot reads from, where its identity model starts), the unified expression-tree machinery that powers every spec-level computation, and the rule for how polymorphic / discriminator-style classes participate in dependency tracking.

These three commitments together describe the *spec-side* discipline that makes audit walk-back keepable: where the audit chain begins, what canonical form it walks, and what the spec must declare for knot to track polymorphic dependencies.

---

## Commitment 9 — Source-layer contract. Knot starts at normalize.

Sources are team-owned lake declarations: a URI, a typed Pydantic spec for the columns, an identifier slot, optional watermark, a mapping to one or more ontology classes (homogeneous or discriminator-routed with explicit wildcard drop). **Team owns getting data into the location**; knot owns reading from there onward.

There is no `SourceReader` interface. No "knot pulls from external systems." No knot-managed credentials for ingestion. The compiled workflow's first task per source is `normalize:<source>`.

### Rationale

Layer 1 ([Constraint 2 in constraints-and-posture.md](../1-why/constraints-and-posture.md#constraint-2--lake-first-knot-starts-at-normalize)) establishes three reasons:

1. **Credentials hygiene.** Pulling from external systems means knot holds those credentials. The team already has a system for that; doubling it inside knot is reinvention.
2. **Replay reproducibility.** If knot pulled live from external systems, every replay would risk drift. With sources landing in the lake, replay is bounded by what the lake holds.
3. **Boundary clarity.** The team's responsibility ends at "land typed rows in the lake at this URI on this watermark." Everything after that is knot's problem.

Layer 1 also establishes that audit walk-back must be deterministic ([Hard problem 5](../1-why/the-hard-problems.md#hard-problem-5-audit-walk-back-is-the-load-bearing-user-benefit)). For replay to work, the source layer has to be a stable point: knot reads what's in the lake at the source watermark; what's in the lake doesn't shift mid-replay.

The earlier sketches of the boundary spec listed `SourceReader` as one of eight interfaces with a `read(source_node_id)` method. [`design/source-layer-contract.md`](../../../design/source-layer-contract.md) supersedes that. `SourceReader` is removed; sources are passive lake declarations, not active runtime objects.

### Comparator anchor

| System | Source ingestion model | Audit identity at ingestion |
|---|---|---|
| **dbt** | Sources are declared (`sources:` in YAML); dbt does not pull. | Source freshness checks; identity is the source's table state at run time. |
| **Airflow operators** | Operators pull / push as part of DAGs; knot-managed credentials | Operator run records carry the source state; sidecar lineage. |
| **Singer / Meltano / Airbyte** | Connector frameworks pulling into a lake | Their lineage; knot would consume the lake afterward. |
| **knot** | Lake-first; the team owns ingestion outside knot | Knot's audit chain starts at normalize, with the source watermark as the anchor. |

dbt's source declaration is the closest neighbor — both treat sources as passive declarations. dbt assumes the source table is in the warehouse; knot assumes the source rows are at a declared URI in the lake. The boundary discipline is the same.

### Tradeoffs / honest costs

- **Team owns ingestion.** A team without an existing ingestion story has to build one (or import a tool: Singer / Meltano / Airbyte / a custom CDC pipeline). Knot doesn't ship a connector library; the architecture explicitly excludes one. For a team with mature ingestion this is right; for a team starting from zero it's real upfront work.
- **Source freshness is the team's SLA.** If the team's batch job for `imdb_movies` skips a day, knot's normalize task reads stale data. Knot has no opinion on the team's refresh cadence; the watermark is what knot uses to ask "rows since X." Failures surface as data freshness via the built-in DQ check, not as ingestion failures.
- **No federation across lakes.** Sources are lake declarations at one URI per source. A team that wants to read directly from a remote system at query time has to build a bound impl that does it (probably wrapped in a `QueryReader` for that source-shape) — the architecture allows it; the recommended posture is "land in the lake first."
- **Discriminator routing requires explicit enumeration.** Every value the discriminator can take must appear in the `mappings` section — either pointing at a class or with `drop: true`. An unmapped value fails the run loudly. Wildcard `"*": {drop: true}` is the only way to drop unknown values lazily, and it's explicit on the source node. The cost is upfront enumeration; the benefit is "no silent drops."

??? details "Deep-dive: source node shapes (homogeneous + heterogeneous)"

    From [`design/source-layer-contract.md`](../../../design/source-layer-contract.md):

    Homogeneous shorthand — `mapping:` (singular). Every row in the source's lake table is the same kind of thing.

    ```yaml
    id: https://knot.demo/imdb_movies
    name: imdb_movies
    initial_trust: 0.85
    location: lake://imdb_movies
    classes:
      ImdbMovie:
        attributes:
          tt_id:        {range: string,   identifier: true}
          title:        {range: string}
          director:     {range: string}
          release_year: {range: integer}
          runtime_min:  {range: integer}
          ingested_at:  {range: datetime, watermark: true}
    mapping:
      ontology_class: Movie
      fields:
        tt_id:        source_natural_key
        title:        title
        director:     director
        release_year: release_year
        runtime_min:  runtime_minutes      # source-name → ontology-name
    ```

    Heterogeneous — `discriminator:` + `mappings:` (plural). One source produces rows for multiple classes.

    ```yaml
    discriminator: role
    mappings:
      actor:
        ontology_class: Person
        fields:
          credit_id:   source_natural_key
          person_name: name
      director:
        ontology_class: Director
        fields:
          credit_id:   source_natural_key
          person_name: name
      casting_director:
        drop: true
      # No "*" wildcard — any unknown role fails the run loudly.
    ```

    Strict enumeration over discriminator values. Wildcard `"*": {drop: true}` is the only way to drop unknown values; it's explicit on the source node.

    What the team owns vs what knot owns:

    | Concern | Owner |
    |---|---|
    | Pulling rows from external systems | Team (out of scope) |
    | Lake storage format (parquet, Iceberg, postgres rows) | Team's lake impl |
    | Refresh schedule | Team |
    | URI scheme resolution | Bound `QueryReader` impl |
    | Source-spec to ontology-class mapping | Spec |
    | Reading from `location` URI onward | Knot's compiled workflow |

    Architectural rules that follow ([`design/source-layer-contract.md`](../../../design/source-layer-contract.md) §"Architectural rules"):

    1. Knot core code does not pull from external systems.
    2. There is no `SourceReader` interface. Earlier boundary specs listed it; this document supersedes that.
    3. Compiled workflows begin at normalize. No `ingest` task in `WorkflowSpec`.
    4. The `location` URI scheme is the lake impl's concern — bound `QueryReader` resolves.

### Graph-level incremental skip

knot computes a content-addressed **cache key** per stage at compile time, from the stage's full input set: stage kind, class, spec revisions, impl source hash, impl config revision, source watermarks, and pinned parent run hashes (for relation classes). The orchestrator receives per-stage `cache_hit` / `cache_miss` flags in the dispatched `WorkflowSpec` and skips execution on hits, reusing the prior run's artifact as the stage's output. "Unchanged" propagates forward through the toposort: if `normalize:imdb_movies` hits cache, `resolve:Movie` inherits a stable watermark and may also hit cache; if everything upstream of `materialize:neo4j_publisher` hits cache, the publisher skips too. knot stays compile-only (commitment 1); the orchestrator does the actual skipping. See [`design/staging/incremental-execution.md`](../../../design/staging/incremental-execution.md).

### Cross-links

- [`design/source-layer-contract.md`](../../../design/source-layer-contract.md) — canonical statement.
- [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"Normalize" — what normalize does at the boundary.
- [`design/core-design.md`](../../../design/core-design.md) commitment 9.
- [`design/staging/incremental-execution.md`](../../../design/staging/incremental-execution.md) — per-stage cache keys, orchestrator skip logic, composition with cross-class pinning.

---

## Commitment 10 — One unified expression tree.

A single Pydantic expression tree (`RelationRef`, `FilteredRelation`, `RelationProject`, `RelationCount`, `RelationAggregate`, `RelationAny`, `RelationAll`, `RelationFirst`, `ScalarDerivation`, `FormatDerivation`, plus `Compare` / `BoolOp` / `SlotPath` / `Literal_` for predicates) powers:

- Slot derivations
- Constraint bodies (cross-row, cross-class)
- ER signal slots (in config)
- DataContext bodies on bound impls
- Translator backward-chain queries

**One canonical form. One SQL generator. One impact-analysis visitor.** Real Pydantic refs throughout. Enums (not Literal strings) for same-shape variants. The terminator type determines the SQL pattern; no per-kind wrapper classes.

### Rationale

The earlier per-kind wrapper-class approach (separate `ExistsRule` / `ForallRule` / `CountRule` / `AggRule` / `PickRule` types with `kind: Literal[...]` discriminators and a `JoinStep` / `join_path` field) is rejected per [`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md). The unified tree subsumes all of these — Pydantic discriminates by class type at serialization; the class IS the discriminator.

The design-thinking pattern is direct (from [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md), pattern 1): "real types over discriminator strings." The earlier `kind: Literal["..."]` approach was string-discriminator based; the unified tree uses real Pydantic class types. Pattern 2 ("kill parallel meta-structures when the typed graph IS the graph") applies too — the typed expression tree IS the impact-analysis surface; no separate registry.

The reuse across surfaces is what makes the discipline pay off:

- **Slot derivations** — `Movie.director` = `RelationProject(relation=FilteredRelation(...), project=...)`.
- **Constraint bodies** — `Constraint.body` is a `BoolExpr` (or `RelationAll` / `RelationAny`).
- **ER signal slots** — config carries `blocking: list[Slot]`, `matching: list[Slot]`, `cross_references: list[OntologyClass]` — slot lists used as data references; the same expressions serialize to JSON the same way.
- **DataContext bodies** — `DataContext(primary=Movie, include=[Movie.identifiers], filter=(Movie.year > 1900))` is a tree of `RelationRef` / `FilteredRelation` / `Compare` / `Literal_`.
- **Translator backward-chain queries** — at runtime, consumer queries are built as expression trees; translator compiles to SQL via the same machinery.

One SQL generator emits all of these. One impact-analysis visitor walks all of these. No per-kind divergence.

### Comparator anchor

| System | Expression tree shape | Reuse across surfaces |
|---|---|---|
| **SQLAlchemy 2.x** | `select()` / `Compare` / `BinaryExpression` AST | Powers ORM queries + Core SQL building. Single AST. |
| **dbt** | Jinja-templated SQL strings | Compilation rendered SQL; no shared AST. |
| **LinkML** | Multiple constraint sub-languages (range, pattern, expression languages); per-target codegen | YAML constructs translate per target. |
| **SHACL** | SHACL Core / SHACL-SPARQL | Two languages (constraint shapes vs SPARQL bodies). |
| **knot** | Single Pydantic expression tree; one canonical form | Powers derivations, constraints, ER signals, DataContexts, translator. |

SQLAlchemy 2.x is the structural ancestor — a single typed expression AST that powers ORM queries and Core SQL building. Knot's tree is the same idea applied to ontology-level expressions. LinkML and SHACL fragment across multiple sub-languages, which is what the unified tree avoids.

### Tradeoffs / honest costs

- **Recursive CTEs are rejected.** Both [`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md) and [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) reject recursive CTEs at compile time. Transitive closure rules must be rejected with `UnsupportedDerivationError`. A team that needs transitive-closure semantics writes a bound materialization impl that handles it lake-side; knot's expression-tree machinery doesn't emit recursive SQL. Real constraint; flagging.
- **One SQL statement per rule.** "One statement" is not "one basic SELECT" — subqueries, joins, aggregations, window functions are fine. But patterns requiring incremental view maintenance (negation under aggregation across runs, DELETE/INSERT propagation) are out of scope; rejected at compile time.
- **`ScalarDerivation` arithmetic AST is open.** `ArithmeticExpr` is sketched in the design but not specified (binary `+ - * /`, function calls). Deferred until concrete use cases require it. Not blocking, but not closed.
- **Per-kind class hierarchy is verbose.** Each terminator (`RelationProject`, `RelationCount`, `RelationAggregate`, etc.) is its own class with its own fields. Verbose vs a discriminator-string approach; the type-checker friendliness pays off.
- **Window functions are open.** Surface in the SDK (per [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md)); SQL gen has no formal design yet (per [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) §"Open"). Tractable but not specified.

??? details "Deep-dive: tree shape and SQL lowering"

    From [`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md):

    Relation expressions (navigation):

    ```python
    class RelationRef(BaseModel):
        from_class: OntologyClass
        slot: Slot

    class FilteredRelation(BaseModel):
        relation: RelationExpr
        filter: BoolExpr
    ```

    Scalar expressions (per-row values):

    ```python
    class SlotPath(BaseModel):
        from_class: OntologyClass
        slots: list[Slot]

    class Literal_(BaseModel):
        value: str | int | float | bool | None
    ```

    Boolean expressions:

    ```python
    class CompareOp(Enum):
        EQ = "eq"; NE = "ne"; LT = "lt"; LE = "le"; GT = "gt"; GE = "ge"
        IN = "in"; NOT_IN = "not_in"
        IS_NULL = "is_null"; IS_NOT_NULL = "is_not_null"

    class BoolOpKind(Enum):
        AND = "and"; OR = "or"; NOT = "not"

    class Compare(BaseModel):
        op: CompareOp
        left: ScalarExpr
        right: ScalarExpr | None = None

    class BoolOp(BaseModel):
        op: BoolOpKind
        args: list[BoolExpr]
    ```

    Derivation terminators (top-level):

    ```python
    class RelationProject(BaseModel):
        relation: RelationExpr
        project: SlotPath

    class RelationAggregate(BaseModel):
        relation: RelationExpr
        func: AggFunc
        operand: SlotPath | None = None
        distinct: bool = False
        group_by: GroupByMode = GroupByMode.NONE
        order_by: list[SlotPath] = []
        pivot: bool = False

    # ... RelationAny, RelationAll, RelationCount, RelationFirst, ScalarDerivation, FormatDerivation
    ```

    SQL lowering by terminator (per [`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md)):

    | Terminator | SQL pattern |
    |---|---|
    | `RelationProject` | `SELECT primary.*, project FROM primary JOIN relation ON … WHERE filter` |
    | `RelationCount` | Correlated subquery `(SELECT COUNT(*) ...)` or `LEFT JOIN … GROUP BY` |
    | `RelationAggregate` | `SELECT primary_pk, AGG(operand) FROM joined WHERE filter GROUP BY primary_pk` |
    | `RelationAny` | `SELECT … WHERE EXISTS (SELECT 1 FROM joined WHERE filter)` |
    | `RelationAll` | `SELECT … WHERE NOT EXISTS (SELECT 1 FROM joined WHERE NOT body)` |
    | `RelationFirst` | `LEFT JOIN joined ON … ORDER BY order_by LIMIT 1 per primary_pk` |
    | `ScalarDerivation` | Inline arithmetic in SELECT |
    | `FormatDerivation` | String concatenation in SELECT |

    Reject-at-compile policy ([`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md) §"Reject-at-compile policy"):

    1. Derivation cycles.
    2. Vacuous `RelationAll`.
    3. Invalid pivot.
    4. IVM-requiring negation.
    5. Scalar-on-multivalued without aggregation.
    6. Deep transitive derivation.
    7. Unresolvable `SlotPath`.

    SQL generation targets are Spark + Trino only ([`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md)). sqlglot AST + per-dialect emitters. Fragments IR (`knot.sql_gen.fragments`) handles dialect-specific constructs (MAP, COLLECT_LIST, MAP_AGG, PIVOT) that don't map cleanly to sqlglot AST. No raw SQL strings outside `fragments`.

### Cross-links

- [`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md) — full expression-tree spec.
- [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) — SQL emit and dialect handling.
- [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) §"Constraint" — Constraint bodies use the same tree.
- [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md) — SDK is the in-Python fluent builder for the same tree.
- [`design/core-design.md`](../../../design/core-design.md) commitment 10.

---

## Commitment 11 — Polymorphic-reference principle.

A polymorphic / discriminator-style class (e.g., `Identifier` with `entity_class` + `entity_src_key`) is invisible to knot's static dependency graph until a *consumer* explicitly declares the dependency. The polymorphic class itself stays generic; consumers (ER configs, DataContexts) name the specific class connections.

Principle: **to use a polymorphic class as a dependency, the consumer must fully specify the relationship.** This makes impact analysis tractable without runtime data inspection.

### Rationale

Layer 1 doesn't have a "polymorphic" page; the load-bearing motivation is structural. From [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) §"Polymorphic references and consumer-declared dependencies":

> Two reference styles coexist in the spec:
>
> - **Typed class references** — `Slot.range` is a real `OntologyClass` ref. The compiler reads the spec alone and adds the structural edge to the dependency graph.
> - **Polymorphic references via discriminator** — `IdentifierPattern` doesn't carry a typed class ref. Its `class_slot` and `key_slot` point at *string-valued* slots that hold the target class name and key as data. From the spec alone, knot can't enumerate which concrete classes the discriminator points at; that information is in the data rows.

The static dependency graph powers impact analysis ("if I rename `Movie.year`, what breaks?") and dependency ordering for the topological sort over the ER dependency graph (per [Commitment 8 in 3-data-and-resolution.md](3-data-and-resolution.md)). Both require knowing which classes connect to which classes. For typed references, the spec alone suffices. For polymorphic references, the spec alone does not — the data does. But knot's impact analysis runs at compile time, before the data is read.

The principle resolves this: **the polymorphic class itself can stay generic; consumers that want knot to track the dependency must declare it.** An ER strategy on Movie that uses Identifier facts as a signal carries an explicit `cross_references: [Identifier]` declaration. That declaration adds the strategic edge `Movie → Identifier` to the dependency graph. Without the declaration, the polymorphic class is invisible — fine for cases where it's truly opaque ("an Identifier identifies anything; we don't track that connection"); broken if a consumer relies on it without saying so.

This generalizes beyond `Identifier`. Any discriminator-style class works the same way. Free-floating polymorphic classes are fine *as long as* their consumers declare the specific class connections.

### Comparator anchor

| System | Polymorphic / discriminator handling | Static dependency tracking |
|---|---|---|
| **RDF / OWL** | Property characteristics + open-world; entailment via reasoner | Not tractable for pipeline-style dependency tracking. |
| **Apache Atlas** | Entity types + relationships; discriminator handled per relationship type | Lineage at the type level; runtime data not consulted. |
| **GraphQL polymorphic interfaces** | Interface types + concrete implementations | Schema-level; concrete types declared. |
| **dbt** | Polymorphic models via Jinja branching | Lineage at the model level; polymorphic dispatch is opaque. |
| **knot** | Polymorphic class is generic; consumers declare specific class connections | Compile-time graph is exact; declared edges fully specify. |

The closest neighbor is GraphQL's polymorphic-interfaces pattern, where concrete implementations are declared in the schema. Knot's pattern is the same shape applied at the consumer level — the polymorphic class is the interface; consumers that want tracking declare the concrete connection.

### Tradeoffs / honest costs

- **Consumers must remember to declare.** A team writing an ER impl that uses Identifier facts has to put `cross_references: [Identifier]` in the config. Forgetting means knot doesn't track the dependency, which means impact analysis won't catch a rename of `Identifier.system`. The publish gate's DataContext cross-check helps (config-referenced spec entities ⊆ DataContext-reachable slots), but the team has to remember to declare the cross_reference in the first place.
- **The "tracking implication" is non-obvious.** Layer 1 doesn't surface this; it's a Layer 2 commitment because the principle isn't visible until you try to do impact analysis on a polymorphic class. New impl authors will need this surfaced in docs.
- **Polymorphic *Identifiers* still work.** The `Identifier` class is generic — it can identify Movies, Persons, anything. The genericity isn't broken by the principle; only the *tracking* is consumer-driven. An Identifier fact for an unknown-to-knot class type is still legitimate data.

??? details "Deep-dive: ReferencePattern shapes and consumer declarations"

    From [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) §"ReferencePattern":

    Three reference styles, all with real Pydantic refs:

    ```python
    class DirectRef(SpecBase):
        ref_type: Literal["direct"] = "direct"
        target_class: OntologyClass
        fk_slot: Slot

    class DiscriminatedRef(SpecBase):
        ref_type: Literal["discriminated"] = "discriminated"
        target_class: OntologyClass
        class_slot: Slot
        key_slot: Slot

    class PolymorphicRef(SpecBase):
        ref_type: Literal["polymorphic"] = "polymorphic"
        candidates: list[OntologyClass]      # ordered; first match wins
        fk_slot: Slot

    ReferencePattern = Annotated[
        DirectRef | DiscriminatedRef | PolymorphicRef,
        Field(discriminator="ref_type"),
    ]
    ```

    `DirectRef` and `PolymorphicRef` carry typed class refs and are tractable for static dependency tracking from the spec alone.

    `DiscriminatedRef` (with `class_slot` pointing at a string-valued slot) is the polymorphic-via-discriminator case. Without consumer declaration, knot can't know which concrete classes the discriminator points at.

    From [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) §"Polymorphic references and consumer-declared dependencies":

    > Free-floating polymorphic classes are fine *as long as* their consumers declare the specific class connections.

    Consumer declaration shape (in an ER impl's Config):

    ```python
    class Config(BaseModel):
        blocking: list[Slot]
        matching: list[Slot]
        cross_references: list[OntologyClass] = []   # explicit consumer declaration
        threshold: float = 0.85
    ```

    A `MovieResolver(ERProtocol)` whose Config has `cross_references=[Identifier]` adds the strategic edge `Movie → Identifier` to the ER dependency graph (per [Commitment 8 in 3-data-and-resolution.md](3-data-and-resolution.md)). Without this, the dependency is invisible.

    Identifier reification (per [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md) §"Cross-references via the Identifier class"):

    > Cross-references reify as `Identifier` — a first-class ontology class. The impl accesses Identifier facts via a declared DataContext — e.g., `DataContext(primary=Identifier, filter=Identifier.entity_class == Movie)` as its own attribute, or by including them on a primary-class DataContext (`include=[Movie.identifiers]`). Cross-source `(system, value)` agreement is strong-evidence input to its scoring.

### Cross-links

- [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) §"Polymorphic references and consumer-declared dependencies".
- [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md) §"Cross-references via the Identifier class".
- [`design/staging/cross-class-pinning.md`](../../../design/staging/cross-class-pinning.md) §"ER dependency graph and topological order" — strategic dependencies.
- [`design/core-design.md`](../../../design/core-design.md) commitment 11.
- [`design/goals.md`](../../../design/goals.md) §"Goals" #9.

---

## Open tensions on this page

- **Recursive CTE rejection rules out useful patterns.** Transitive-closure semantics (e.g., "find all ancestor entities through `is_a`") aren't expressible via the unified tree. A team that needs them writes a bound materialization impl that emits the recursive SQL lake-side. The architectural answer is "materialization impl"; the cost is real for any team with native transitive needs.
- **Unified-tree machinery is verbose.** The tree's expressivity is rich but the in-Python fluent builder layer (per [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md)) is what keeps everyday authoring readable. A team writing constraint bodies directly in JSON would find the verbosity intense; the SDK is what mitigates this. Good design; flagging for honesty.
- **Polymorphic-reference principle requires team discipline.** A team that consistently forgets to declare `cross_references` in their ER impl configs will get incorrect impact analysis. The publish gate's DataContext cross-check catches some of this (config slots ⊆ DataContext-reachable slots) but not the "I rely on Identifier without saying so" case. Surface in impl-author docs.
