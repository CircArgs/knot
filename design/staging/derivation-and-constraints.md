# Derivations and constraints — the expression tree

**Status:** staging — captured for review, not yet integrated into authoritative docs.

A single Pydantic expression tree powers derivations, constraints, ER signal slots, DataContext bodies, and translator backward-chain queries. **One canonical form. One SQL generator. One impact-analysis visitor.**

The expression tree replaces an earlier per-kind wrapper-class approach (separate `ExistsRule` / `ForallRule` / `CountRule` / `AggRule` / `PickRule` types with `kind: Literal[...]` discriminators and a `JoinStep` / `join_path` field). All references are real Pydantic object refs (per `spec-model.md` § "References").

---

## What a derivation is

A **derived slot's value is computed from existing facts** rather than asserted by a source. Mechanically: a `Slot` carries a `derivation` field whose value is a Pydantic expression tree. knot introspects the tree at compile time, walks the references for impact analysis, and emits SQL for forward / backward chaining.

There is **no per-kind wrapper class**. The expression's terminator type (the outer Pydantic class) determines the SQL lowering pattern.

Motivating example:

> `Movie.director` = the Person referenced by a Credit where `Credit.work = Movie` and `Credit.role = "director"`.

```python
director_slot = Slot(
    name="director",
    range=Person,                 # real OntologyClass ref
    multivalued=True,
    derivation=RelationProject(
        relation=FilteredRelation(
            relation=RelationRef(from_class=Movie, slot=credits_slot),    # real refs
            filter=Compare(
                op=CompareOp.EQ,
                left=SlotPath(from_class=Credit, slots=[role_slot]),
                right=Literal_(value="director"),
            ),
        ),
        project=SlotPath(from_class=Credit, slots=[person_slot]),
    ),
)
```

The expression tree is the canonical form. The API receives it as JSON; Pydantic validates; the persistence boundary rehydrates name strings into real refs.

---

## Expression tree node types

All nodes are Pydantic `BaseModel` classes. Pydantic discriminates by class type at serialization (the JSON includes a type tag matching the class name); in-memory the class IS the discriminator. **No `kind` / `op` Literal fields.** Enums (`CompareOp`, `BoolOpKind`, `AggFunc`, `GroupByMode`) are real Python enum types, not string literals.

All references between metaschema entities (`OntologyClass`, `Slot`) are real Pydantic object refs.

### Relation expressions

Navigate from a class to a related class via slot traversal.

```python
class RelationRef(BaseModel):
    """Follow a slot whose range is another class — Movie.credits, Person.identifiers, etc."""
    from_class: OntologyClass
    slot: Slot                                # slot whose range is an OntologyClass

class FilteredRelation(BaseModel):
    """A relation with a row-level predicate applied — Movie.credits.where(role == 'director')."""
    relation: RelationExpr                    # nested: any RelationExpr
    filter: BoolExpr

RelationExpr = RelationRef | FilteredRelation
```

A `RelationExpr` evaluates to a multiset of rows from the target class. Chained traversals compose by nesting `FilteredRelation` around the inner `RelationExpr`.

### Scalar expressions

Per-row values — slot paths, literals, computed scalars.

```python
class SlotPath(BaseModel):
    """Path from a class through a chain of slots, terminating at any slot.
    
    For scalar slots, the path resolves to a value.
    For relation slots, intermediate steps traverse into related classes.
    """
    from_class: OntologyClass
    slots: list[Slot]                         # ordered slots; each slot's range
                                              # determines the next hop's class

class Literal_(BaseModel):
    value: str | int | float | bool | None

ScalarExpr = SlotPath | Literal_
```

Each step in `SlotPath.slots` is a real `Slot` reference; knot walks it to enumerate touched spec entities for impact analysis.

### Boolean expressions

For filters and constraint bodies.

```python
class CompareOp(Enum):
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"

class BoolOpKind(Enum):
    AND = "and"
    OR = "or"
    NOT = "not"

class Compare(BaseModel):
    op: CompareOp
    left: ScalarExpr
    right: ScalarExpr | None = None           # None for unary ops (IS NULL, IS NOT NULL)

class BoolOp(BaseModel):
    op: BoolOpKind
    args: list[BoolExpr]                      # len 1 for NOT; len ≥ 2 for AND/OR

BoolExpr = Compare | BoolOp
```

### Aggregation enums

```python
class AggFunc(Enum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COLLECT = "collect"
    FIRST = "first"

class GroupByMode(Enum):
    NONE = "none"
    SOURCE = "source"
```

---

## Derivation terminator types

Top-level expression types that can appear as a Slot's `derivation`. Each terminator dictates one SQL lowering pattern.

```python
class RelationProject(BaseModel):
    """Surface a slot value (or chain of slot values) from each row of the relation.
    
    Lowers to: SELECT … FROM primary JOIN relation ON … WHERE filter
    Multivalued if the slot is multivalued; scalar otherwise.
    """
    relation: RelationExpr
    project: SlotPath

class RelationCount(BaseModel):
    """Count rows in the relation. Scalar integer."""
    relation: RelationExpr
    distinct: bool = False

class RelationAggregate(BaseModel):
    """Aggregate over rows in the relation."""
    relation: RelationExpr
    func: AggFunc
    operand: SlotPath | None = None           # None for COUNT-style (implicit *)
    distinct: bool = False
    group_by: GroupByMode = GroupByMode.NONE
    order_by: list[SlotPath] = []             # used by FIRST and ordered COLLECT
    pivot: bool = False                       # only valid when group_by == SOURCE

class RelationAny(BaseModel):
    """Boolean: any row in the relation exists.
    
    Lowers to: EXISTS (SELECT 1 FROM ...).
    """
    relation: RelationExpr
    
class RelationAll(BaseModel):
    """Boolean: every row in the relation satisfies a body predicate.
    
    Lowers to: NOT EXISTS (SELECT 1 FROM ... WHERE NOT body).
    """
    relation: RelationExpr
    body: BoolExpr

class RelationFirst(BaseModel):
    """Surface the first row's projection, by an ordering. Optionally assert uniqueness."""
    relation: RelationExpr
    project: SlotPath
    order_by: list[SlotPath] = []
    assert_unique: bool = False

class ScalarDerivation(BaseModel):
    """Within-row computed value — no relation traversal.
    
    Used for slots like Person.full_name = first_name || ' ' || last_name.
    """
    expression: ScalarExpr | BoolExpr | ArithmeticExpr   # ArithmeticExpr shape: see Open § "ScalarDerivation arithmetic AST"

class FormatDerivation(BaseModel):
    """Pattern-string serialization — Person.display_name = '{last}, {first}'."""
    template: str
    slots: list[SlotPath]                     # ordered, interpolated into template

DerivationExpr = (
    RelationProject
    | RelationCount
    | RelationAggregate
    | RelationAny
    | RelationAll
    | RelationFirst
    | ScalarDerivation
    | FormatDerivation
)
```

A Slot's `derivation` field is `DerivationExpr | None`.

---

## Examples

### `Movie.actor_count` — distinct count of actors per movie

```python
RelationCount(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Movie, slot=credits_slot),
        filter=Compare(op=CompareOp.EQ,
                       left=SlotPath(from_class=Credit, slots=[role_slot]),
                       right=Literal_(value="actor")),
    ),
    distinct=True,
)
```

### `Movie.actor_names` — collected list of actor names

```python
RelationAggregate(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Movie, slot=credits_slot),
        filter=Compare(op=CompareOp.EQ,
                       left=SlotPath(from_class=Credit, slots=[role_slot]),
                       right=Literal_(value="actor")),
    ),
    func=AggFunc.COLLECT,
    operand=SlotPath(from_class=Credit, slots=[person_slot, name_slot]),
    distinct=False,
)
```

### `Movie.has_director` — boolean

```python
RelationAny(
    relation=FilteredRelation(
        relation=RelationRef(from_class=Movie, slot=credits_slot),
        filter=Compare(op=CompareOp.EQ,
                       left=SlotPath(from_class=Credit, slots=[role_slot]),
                       right=Literal_(value="director")),
    ),
)
```

---

## SQL lowering by terminator

| Terminator | SQL pattern |
|---|---|
| `RelationProject` | `SELECT primary.*, project FROM primary JOIN relation ON … WHERE filter` |
| `RelationCount` | Correlated subquery `(SELECT COUNT(*) FROM joined WHERE filter)` or `LEFT JOIN … GROUP BY` (heuristic deferred) |
| `RelationAggregate` | `SELECT primary_pk, AGG(operand) FROM joined WHERE filter GROUP BY primary_pk`; `+ DISTINCT` per `distinct`; `group_by=SOURCE` adds source column + emits `MAP`/`ARRAY_AGG` per `sql-generation.md` |
| `RelationAny` | `SELECT … WHERE EXISTS (SELECT 1 FROM joined WHERE filter)` |
| `RelationAll` | `SELECT … WHERE NOT EXISTS (SELECT 1 FROM joined WHERE NOT body)` |
| `RelationFirst` | `LEFT JOIN joined ON … ORDER BY order_by LIMIT 1 per primary_pk`; uniqueness assertion as separate `COUNT(*)` when `assert_unique=True` |
| `ScalarDerivation` | Inline arithmetic / function expression in the SELECT list |
| `FormatDerivation` | String concatenation / `FORMAT()` in the SELECT list |

All terminators emit **one SQL statement** — subqueries, joins, aggregations, and CTEs are fine. "One statement" is not "one basic SELECT."

**Recursive CTEs: REJECTED.** Aligns with `sql-generation.md`. Transitive closure rules are rejected at compile time.

Dialect-specific gaps (MAP, COLLECT_LIST, MAP_AGG) are handled via the SQL generator's IR + per-dialect emit dispatch (per `sql-generation.md`).

---

## Constraints — reuse the same machinery

`Constraint.body` (per `spec-model.md` § "Constraint") is a `BoolExpr` (or a `RelationAll` / `RelationAny` for cross-row invariants over a primary class). knot's SQL generator emits validation SQL with the standard `(rule_id, class_name, slot_name, offending_pk, detail)` shape (per `sql-generation.md`).

Constraints don't need their own grammar — they're just expressions evaluated per primary row.

---

## Reuse beyond derivations and constraints

The same expression-tree machinery powers:

- **ER strategy `blocking` / `matching` / `cross_references`** — slot lists used as data references (per `er-and-storage.md` and `di-input-contract.md`).
- **DataContext bodies** — bound impls declare their fetch shape using the same expression tree (per `di-input-contract.md`).
- **Translator backward-chain queries** — at runtime, consumer queries are built as expression trees; translator compiles to SQL via the same machinery.

One canonical form. One SQL generator. One impact-analysis visitor.

---

## Reject-at-compile policy

knot raises `UnsupportedDerivationError` for:

1. **Derivation cycles** — derivation graph has a cycle (A derives from B, B derives from A).
2. **Vacuous `RelationAll`** — relation reaches a class with no rows; "all" over an empty set is trivially true and usually a bug. (Optional check; can be a warning.)
3. **Invalid pivot** — `RelationAggregate(pivot=True, group_by=GroupByMode.NONE)`.
4. **IVM-requiring negation** — negation that cannot be expressed as `NOT EXISTS` in a single statement.
5. **Scalar-on-multivalued without aggregation** — a multivalued relation with no aggregation terminator (and not `RelationAny` / `RelationAll`); ambiguous projection.
6. **Deep transitive derivation** — a relation traversal that crosses more than one level of derived slots; not supported.
7. **Unresolvable `SlotPath`** — a `SlotPath.slots` element references a slot not on the path's current class. Caught at parse-time reference resolution (per `spec-model.md` persistence boundary).

---

## Derived slots as DataContext primary

A derived slot can be the `primary` of a `DataContext`, producing an **edge view** rather than a node view:

```python
director_edges: DataContext = DataContext(primary=Movie.director)
# → edge view: movie_canonical_id | person_canonical_id
```

knot's SQL gen forward-chains from the derivation rule; the materializer impl writes whatever its target needs (e.g., `(:Movie)-[:DIRECTED_BY]->(:Person)` in Neo4j). A list of derived slots fans out to N edge views the same way a list of classes fans out to N node views. See `staging/multi-class-datacontexts.md`.

## Open

- **`ScalarDerivation` arithmetic AST.** `ArithmeticExpr` is sketched but not specified (binary `+ - * /`, function calls). Deferred until concrete use cases require it.
- **`RelationCount` heuristic.** Correlated subquery vs `LEFT JOIN + GROUP BY` — codegen-context-dependent. Defer to implementation; both are correct.
- **Composite traversals.** `Movie.credits.where(...).person.identifiers` — chains of relations through projections. The current shape supports this via nested `RelationRef` inside `SlotPath`-projecting nodes; concrete shape may want a `ChainedRelation` node for clarity. Revisit when modeling exposes real cases.
