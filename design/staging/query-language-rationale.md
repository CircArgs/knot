# Query / expression language rationale

**Status:** decided. Captures why knot's typed Pydantic AST is the source-of-truth expression language (commitment 10), where graph-native query languages (Gremlin / Cypher / SPARQL) fit instead, and which Gremlin ergonomics are worth borrowing as method surface on knot's AST.

---

## The decision

knot's source-of-truth expression substrate is a **typed Pydantic AST** (per commitment 10). Graph-native query languages (Gremlin, Cypher, SPARQL) earn their place only as **emit targets at the Translator / Materializer seam** — never as the authoring substrate.

A 3-persona debate (Type Maximalist, Comparative Anchorer, Reality Checker) converged on this verdict; full reports archived in the project history.

## Why a typed AST is load-bearing

Seven concrete impl-writer scenarios decide it. Each fails under string-DSL / typed-step alternatives in ways the typed AST handles by construction:

| Scenario | Typed AST | Gremlin string | gremlin-python typed steps |
|---|---|---|---|
| Slot autocomplete (`Movie.year` reaches `runtime_minutes` etc.) | ✓ | ✗ (strings) | ✗ (strings within steps) |
| LSP rename `Movie.year → Movie.release_year` propagates | ✓ | ✗ (silent drift) | ✗ (silent drift) |
| Lens stance enforcement (`MultiValued[T]` rejects `__gt__` under DISAGREEMENT_AWARE) | ✓ | ✗ (no concept) | ✗ (no concept) |
| `ConfigRef` substitution at compile (typed node in static tree) | ✓ | ✗ (string template) | ✗ (eager runtime sub — defeats static traceability) |
| Multi-class DataContext over `spec.classes` | ✓ (plain Python over typed refs) | ✗ (no spec primitive) | ✗ (no spec primitive) |
| Forward + backward chain from one derivation rule | ✓ (single declaration) | ✗ (two hand-authored traversals) | ✗ (two hand-authored traversals) |
| Catch broken refs at registration before compile-hash mint | ✓ | ✗ (runtime fail after dispatch) | ✗ (runtime fail after dispatch) |

Scenario 5 (spec rename) and Scenario 7 (lens stance) are decisive. A spec rename under a Gremlin string keeps parsing, hashes, dispatches, and fails at runtime AFTER the orchestrator has the WorkflowSpec — coupling commitment 3 (compile hash = run identity) to a runtime concern it was supposed to be insulated from. Lens stance enforcement requires codegen that reads the protocol's `disagreement_stance`; that lever exists only when the SDK is generated against a typed AST.

## Comparative anchoring — two camps

Prior art falls into two camps. knot lives in Camp B and should stay there.

| Camp | Examples | Cost | Win |
|---|---|---|---|
| **A — string DSL with schema-side validation** | dbt, Cypher, SPARQL, Atlas, ksqlDB, Splink-SQL | Static analysis bolted on after the fact; refactoring is grep-driven; SHACL's SPARQL escape hatch is the canonical "where typing dies" pattern | Cheap to author for SQL/graph-native users (irrelevant under single-team posture) |
| **B — typed AST/builder as primary** | Datomic (data-as-query), gremlin-python Bytecode, DJ, Streams DSL | Steeper learning curve (irrelevant for trusted single-team Python authors) | Strong impact analysis, refactor safety, single-source-of-truth |

The Camp A vs B split tracks the user model: tools designed for adoption-by-N-teams gravitate to strings (lower onboarding cost); tools designed for single-team / developer-led use gravitate to typed builders. knot is the latter (commitment 5, single-team posture).

## Anti-patterns to avoid

1. **Stringly-typed escape hatches.** SHACL's SPARQL escape, Cypher's APOC string params, dbt's raw-SQL-inside-Jinja — every one of these systems eventually needed an escape hatch and every escape hatch became where typing died. **If the team finds an expression the typed AST cannot represent, extend the AST (a new node type), don't add a string DSL alongside.**

2. **Two-surface front-ends.** ksqlDB (string SQL) alongside Kafka Streams DSL (typed builder) means two sets of edge cases, two debugging stories, two evolution paths. knot has *one* expression tree (commitment 10); don't replicate Confluent's two-surface tax.

3. **Adoption-driven schema-string coupling.** GraphQL deprecation cycles exist because schemas are public. knot's spec is not public; consumers query via Translator. Don't import GraphQL's `@deprecated` machinery.

## Where Gremlin / Cypher / SPARQL natively fit

Per `multi-valued-semantics.md`'s protocol-stance table, Translator impls own non-lake targets:

| Target | Translator impl | Wire form |
|---|---|---|
| Neo4j | `Neo4jTranslator` (or per-team named) | Cypher |
| AWS Neptune (property graph) | `NeptuneGremlinTranslator` | Gremlin Bytecode |
| AWS Neptune (RDF mode) | `NeptuneSparqlTranslator` | SPARQL |
| TinkerPop-compatible (JanusGraph, Cosmos DB) | `GremlinTranslator` | Gremlin Bytecode |
| Vector store | `VectorTranslator` | similarity API |

Each translates a knot AST expression at the seam. The AST is the source of truth; the wire form is the protocol of the target.

## Borrowed ergonomics from Gremlin

Even with the typed-Pydantic substrate intact, Gremlin's vocabulary informs which method surface knot's AST should expose. Three additions land cleanly as new typed expression nodes:

### `Within` — set-membership predicate

```python
class Within(SpecBase):
    """Predicate: value ∈ {a, b, c, ...}. Emits SQL `IN (...)`."""
    op: ClassVar[Literal["within"]] = "within"
    left: SlotPath
    values: list[Literal_]
```

SDK surface: `Movie.genres.within(["Action", "Sci-Fi"])`. Replaces ad-hoc `(x == a) | (x == b) | (x == c)` chains.

### `Between` — range predicate

```python
class Between(SpecBase):
    """Predicate: lower ≤ value ≤ upper (inclusive=True) or strict bounds."""
    op: ClassVar[Literal["between"]] = "between"
    left: SlotPath
    lower: Literal_
    upper: Literal_
    inclusive: bool = True
```

SDK surface: `Movie.year.between(1990, 2000)`. Replaces `(x > a) & (x < b)`.

### `RecursiveTraversal` — transitive / hierarchical walks

```python
class RecursiveTraversal(SpecBase):
    """Walk a relation transitively until a stopping predicate.

    Used for class hierarchies (Title → Movie / Series / Episode / Game subclasses
    via is_a chains) and recursive structural relations.
    """
    op: ClassVar[Literal["recursive"]] = "recursive"
    start: RelationRef
    step: SlotPath
    until: BoolExpr | None = None
    max_depth: int | None = None
```

SDK surface: `Title.descendants()` (walks `is_a` chain), `Person.knows.transitive(max_depth=3)`.

### Method ergonomics on existing nodes

Add to the SDK without new node types:

- `slot.matches(pattern: str)` → emits SQL `LIKE` or regex match (currently absent)
- `slot.starts_with(prefix: str)` / `ends_with(suffix: str)`
- `RelationProject.select(*slots)` — multi-slot labeled projection (Gremlin's `select('a', 'b', 'c')` shape)

What's NOT borrowed:

- String labels (commitment 2 — real refs)
- Stateful traversers (knot's AST is pure data, walked by visitors)
- Imperative side-effect chains (`addV`, `addE` — those are auto-generated by knot's SQL emission, not authored)

## Cross-references

- `core-design.md` § 10 (one unified expression tree) — this doc strengthens the rationale; the three new node types extend the tree without adding parallel meta-structures.
- `core-design.md` § 13 (in-flight corrections / Translator) — Translator impls per non-lake target (Gremlin / Cypher / SPARQL / vector).
- `staging/multi-valued-semantics.md` — protocol-stance table; Translator (bound) is RESOLVED for non-lake targets.
- `staging/multi-class-datacontexts.md` — `spec.classes` iteration; depends on commitment 2's typed entity tree.
- `staging/datacontext-config-binding.md` — `ConfigRef` symbolic refs; impossible under string DSLs.
- `src/knot/metaschema.py` — current expression tree to extend with `Within`, `Between`, `RecursiveTraversal`.
