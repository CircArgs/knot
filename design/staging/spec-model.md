# Pydantic spec model

**Status:** staging — captured for review, not yet integrated into authoritative docs.

The internal Pydantic representation knot uses to model ontologies. **Source of truth** for ontology structure; the SDK generator, SQL generator, and impact-analysis machinery all operate on it.

---

## References: real objects in-memory, names at the boundary

The in-memory spec uses **real Pydantic object references** between metaschema entities — not string names. `Slot.range` holds an `OntologyClass | TypeDefinition` directly; `OntologyClass.slots` holds a `list[Slot]` of real Slot objects; a derivation rule's `project` is a `Slot`, not a `(class_name, slot_name)` tuple.

Why real refs:

- Authoring code, codegen passes, and the SDK / SQL gen all walk the spec graph naturally. No `spec.slots[name]` lookup detour at every step.
- Type-checker friendly: `slot.range.name` is statically resolvable; a string-keyed lookup is not.
- SQLAlchemy precedent: ORM holds `relationship()` references directly; the FK-by-id flattening is a persistence concern.

Where strings live: **the persistence boundary.** When the spec is written to JSONB (or read back), references are flattened to names; the persistence layer rehydrates real references on read. The API / UI / postgres layer owns this round-trip. The Pydantic in-memory shape is reference-typed throughout.

---

## Three levels of class — naming map

| Level | Example | What it is |
|---|---|---|
| **Metaschema class** | `OntologyClass`, `Slot`, `Constraint` | Pydantic classes describing the shape of an ontology spec. The schema language. |
| **Per-ontology Pydantic spec class** | `MovieSpec`, `CreditSpec` | Pydantic instances generated from a specific ontology, used for storage (JSONB) and validation. |
| **SDK descriptor class** | `Movie`, `Credit` | Descriptor-only Python class generated alongside the spec class. Bound impls import these for query expressions (`Movie.year > 1900`). NOT a Pydantic model. See `auto-generated-sdk.md`. |

`OntologyClass` (metaschema) describes how to define a class. A user authoring an ontology produces an instance like `OntologyClass(name="Movie", slots=...)`, which knot's codegen turns into `MovieSpec` (Pydantic) + `Movie` (SDK).

---

## Spec root container

`Spec` is the single root object passed to the SQL generator, SDK generator, impact-analysis graph builder, and content-hashing canonicalizer.

```python
class Spec(SpecBase):
    id: str
    version: str
    classes: list[OntologyClass] = []
    slots: list[Slot] = []
    types: list[TypeDefinition] = []
    constraints: list[Constraint] = []
    prefixes: dict[str, str] = {}
    default_range: TypeDefinition | None = None
```

Lists, not dicts. Identity is the entity's `name` field; uniqueness is enforced at parse time. Lookup helpers (`spec.class_by_name(...)`, etc.) are convenience over the lists.

`SpecBase` is a shared `BaseModel` parent (per `spec-versioning.md`) with `model_config = ConfigDict(extra="forbid")` and no aliases. All metaschema classes inherit from it.

---

## OntologyClass

```python
class OntologyClass(SpecBase):
    name: str
    is_a: OntologyClass | None = None
    mixins: list[OntologyClass] = []
    slots: list[Slot] = []                       # real Slot references
    slot_overrides: list[SlotOverride] = []      # per-class refinements
    unique_keys: list[UniqueKey] = []
    identifier_pattern: IdentifierPattern | None = None  # reified-class only
    abstract: bool = False
    description: str | None = None
```

- **`is_a`** — single inheritance; Pydantic class inheritance in codegen.
- **`mixins`** — multiple inheritance; C3-style MRO with deterministic precedence; parse-time conflict diagnostics.
- **`abstract`** — abstract classes cannot be the range of a non-derivation slot. Codegen raises `AbstractRangeError` at spec-parse time.

---

## Slot

```python
class ResolutionPolicy(str, Enum):
    ARGMAX_TRUST     = "argmax_trust"      # default: highest-trust contribution wins
    MODE             = "mode"              # most-frequent value (ties → ARGMAX_TRUST tiebreak)
    WEIGHTED_VOTE    = "weighted_vote"     # value with highest sum of trust scores
    MEDIAN_NUMERIC   = "median_numeric"    # numeric only; median of values
    LATEST_WATERMARK = "latest_watermark"  # contribution with latest asserted_at
    UNIQUE_OR_FAIL   = "unique_or_fail"    # all sources must agree; disagreement raises

class Slot(SpecBase):
    name: str
    range: TypeDefinition | OntologyClass        # real reference
    identifier: bool = False
    required: bool = False
    multivalued: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    pattern: str | None = None                   # Field(pattern=...) in codegen
    minimum_value: float | None = None           # Field(ge=...)
    maximum_value: float | None = None           # Field(le=...)
    permissible_values: list[PermissibleValue] | None = None
    derivation: DerivationExpr | None = None     # see derivation-and-constraints.md
    reference: ReferencePattern | None = None
    description: str | None = None
```

`range` is a real reference: either a `TypeDefinition` (primitive or custom type) or an `OntologyClass`. Pydantic v2 handles the discriminated union via the field's static type; resolution and parse-time validation flag missing references.

`resolution_policy` controls how the trust-resolution CTE reduces the contribution bag to one value under `RESOLVED`-stance protocols. It is declared on the Slot (not per call-site) so the same `Movie.year` always resolves the same way, preserving audit determinism. The spec validator rejects `MEDIAN_NUMERIC` on any slot whose `range` is non-numeric at parse time. Full semantics and the per-policy SQL reduction expressions are in `staging/multi-valued-semantics.md`.

---

## Globally-shared slots

Slots live in `Spec.slots` (top-level), not embedded per-class. Multiple classes can share a Slot definition without duplication. `OntologyClass.slots` holds **real references** into the global slot pool.

Per-class refinements live in `OntologyClass.slot_overrides`:

```python
class SlotOverride(SpecBase):
    slot: Slot                                   # which slot is overridden (real ref)
    required: bool | None = None
    range: TypeDefinition | OntologyClass | None = None
    pattern: str | None = None
    minimum_value: float | None = None
    maximum_value: float | None = None
    description: str | None = None
```

Codegen merges base Slot + matching SlotOverride at generation time. Override fields shadow the shared definition; unset fields inherit. A SlotOverride whose `slot` isn't in the owning class's `slots` list is a parse-time error.

---

## Constraint

Cross-row and cross-class invariants. Replaces the SHACL-style "shape" mechanism with a declarative Pydantic node whose body is an expression tree (per `derivation-and-constraints.md`). knot's SQL generator compiles each Constraint to validation SQL with the uniform `(rule_id, class_name, slot_name, offending_pk, detail)` shape.

```python
class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"

class Constraint(SpecBase):
    name: str
    primary: OntologyClass                       # class the constraint applies to
    body: BoolExpr | RelationAll | RelationAny   # condition every primary row must satisfy
    severity: Severity = Severity.ERROR
    message: str | None = None                   # human-readable failure message
```

Per-row example (within-class, no traversal):

```python
Constraint(
    name="movie_year_range",
    primary=Movie,
    body=BoolOp(op=BoolOpKind.AND, args=[
        Compare(op=CompareOp.GE, left=SlotPath(from_class=Movie, slots=[year_slot]),
                right=Literal_(value=1888)),
        Compare(op=CompareOp.LE, left=SlotPath(from_class=Movie, slots=[year_slot]),
                right=Literal_(value=2100)),
    ]),
    severity=Severity.ERROR,
)
```

Cross-row example (sequel year monotonicity, traversing `Movie.prequel`):

```python
Constraint(
    name="sequel_year_monotonic",
    primary=Movie,
    body=Compare(
        op=CompareOp.GE,
        left=SlotPath(from_class=Movie, slots=[year_slot]),
        right=SlotPath(from_class=Movie, slots=[prequel_slot, year_slot]),
    ),
    severity=Severity.ERROR,
    message="Sequel year must be >= prequel year",
)
```

Cross-class example (every Person credited as director has at least one Movie):

```python
Constraint(
    name="director_has_movie",
    primary=Person,
    body=RelationAny(
        relation=FilteredRelation(
            relation=RelationRef(from_class=Person, slot=person_credits_slot),
            filter=Compare(op=CompareOp.EQ,
                           left=SlotPath(from_class=Credit, slots=[role_slot]),
                           right=Literal_(value="director")),
        ),
    ),
    severity=Severity.WARNING,
)
```

Same expression-tree machinery as derivations and SDK queries. No SHACL escape hatch, no SPARQL engine.

---

## UniqueKey

```python
class UniqueKey(SpecBase):
    slots: list[Slot]                            # ordered slot references
```

knot emits validation SQL: `GROUP BY <slots> HAVING COUNT(*) > 1`. Same uniform `(rule_id, class_name, slot_name, offending_pk, detail)` shape as other validation queries.

---

## ReferencePattern

Tagged union; discriminated by `ref_type`. All slot references are real `Slot` objects; class references are real `OntologyClass` objects.

```python
class DirectRef(SpecBase):
    ref_type: Literal["direct"] = "direct"
    target_class: OntologyClass
    fk_slot: Slot                                # slot on *this* class holding the FK value

class DiscriminatedRef(SpecBase):
    ref_type: Literal["discriminated"] = "discriminated"
    target_class: OntologyClass
    class_slot: Slot                             # slot holding the target class name (discriminator)
    key_slot: Slot                               # slot holding the target entity key

class PolymorphicRef(SpecBase):
    ref_type: Literal["polymorphic"] = "polymorphic"
    candidates: list[OntologyClass]              # ordered; first match wins
    fk_slot: Slot

ReferencePattern = Annotated[
    DirectRef | DiscriminatedRef | PolymorphicRef,
    Field(discriminator="ref_type"),
]
```

Covers: plain FK relationships, the discriminator pattern, polymorphic references. The structural-reference graph (per `impact-analysis.md`) derives FK join paths from these.

---

## IdentifierPattern

Class-level on the **reified class only** — i.e., the class that plays an identifier role declares this once; referencing classes use `ReferencePattern` instead.

```python
class IdentifierPattern(SpecBase):
    class_slot: Slot                             # slot holding the entity class name
    key_slot: Slot                               # slot holding the entity src key
    scope: OntologyClass | None = None           # if set, valid only within this scope
```

---

## Polymorphic references and consumer-declared dependencies

Two reference styles coexist in the spec:

- **Typed class references** — `Slot.range` is a real `OntologyClass` ref (e.g., a relation class's `work: Movie`). The compiler reads the spec alone and adds the structural edge to the dependency graph. Impact analysis is exact: rename `Movie.year` → walk through the relation class's slot-range edge.
- **Polymorphic references via discriminator** — `IdentifierPattern` doesn't carry a typed class ref. Its `class_slot` and `key_slot` point at *string-valued* slots that hold the target class name and key as data. From the spec alone, knot can't enumerate which concrete classes the discriminator points at; that information is in the data rows.

**The tracking implication.** A polymorphic reference is invisible to knot's static dependency graph until a *consumer* explicitly declares the dependency. For example, an ER strategy on Movie that uses Identifier facts as a signal would carry an explicit `cross_references: [Identifier]` declaration, which adds the strategic edge `Movie → Identifier` to the dependency graph (per `cross-class-pinning.md`).

The principle: **to use a polymorphic class as a dependency, the consumer must fully specify the relationship.** The polymorphic class itself can stay generic (an Identifier can identify *anything*), but every consumer that wants knot to track and validate the dependency declares it explicitly. That makes impact analysis and dependency ordering tractable without runtime data inspection.

This generalizes beyond IdentifierPattern: any discriminator-style class works the same way. Free-floating polymorphic classes are fine *as long as* their consumers declare the specific class connections.

---

## PermissibleValue

```python
class PermissibleValue(SpecBase):
    text: str
    description: str | None = None
    meaning: str | None = None                   # URI / CURIE; optional
```

`Slot.permissible_values: list[PermissibleValue]`. Codegen emits a `Literal` or `Enum` type for slots with permissible values.

---

## TypeDefinition

```python
class TypeDefinition(SpecBase):
    name: str
    base: str | None = None                      # Python base type name, e.g. "str", "int"
    pattern: str | None = None                   # optional regex constraint at the type level
    description: str | None = None
```

The Spec ships with built-in TypeDefinition objects for primitives (`string`, `integer`, `boolean`, `float`, `date`, `datetime`, `uri`). Custom types added via `Spec.types`. `Slot.range` references a TypeDefinition (built-in or custom) by real reference.

---

## Persistence boundary

The in-memory model uses real references; postgres / JSONB serialization uses name-keyed flattening. This is a knot concern (the API and storage layers). The contract:

- **Write (rehydrated → flattened):** the API layer dumps the spec by emitting names in place of object references. E.g., `{"slot": "title"}` with the field's static type carrying enough context to re-resolve.
- **Read (flattened → rehydrated):** two-pass parse. First pass: build all entities (classes, slots, types) by name. Second pass: resolve every reference field to the real object. Pydantic `model_validator(mode="before")` or a hand-rolled rehydration step.

Tests treat in-memory Pydantic specs as the canonical form. Round-trip tests (in-memory → JSONB → in-memory) live at the API/storage boundary.

---

## Feature support summary

| Feature | Disposition | Rationale |
|---|---|---|
| `is_a` (single inheritance) | **Supported** — native Pydantic class inheritance | Straightforward mapping. |
| `mixins` (multiple inheritance) | **Supported** — C3-style MRO, parse-time conflict diagnostics | Deterministic precedence. |
| `slot_overrides` (per-class refinement) | **Supported** — `OntologyClass.slot_overrides: list[SlotOverride]` with real Slot refs | Lowered; no parallel field declaration. |
| `pattern` + range constraints | **Supported** — `Field(pattern=, ge=, le=)` | Direct Pydantic v2 mapping. |
| `permissible_values` (enums) | **Supported** — `list[PermissibleValue]` | Codegen emits `Literal`/`Enum`. |
| within-object computed slots | **Lowered** — `ScalarDerivation` (per `derivation-and-constraints.md`) | Unified derivation representation. |
| `unique_keys` (multi-field uniqueness) | **Supported** — `OntologyClass.unique_keys: list[UniqueKey]` | knot emits validation SQL. |
| Pattern-string slot derivations | **Lowered** — `FormatDerivation` | Unified with derivation; no special-case parsing. |
| Classification rules (rule-based class membership) | **Rejected** for now — raises `UnsupportedFeatureError` | Complexity vs. value; use a derived slot instead. |

---

## Config and impl bindings

Config is per-impl-binding: each bound impl declares its own Pydantic Config class; knot stores the Config snapshot per binding in postgres-control. Config field types determine the shape of `ConfigRef` nodes in DataContext expressions — primitive fields (`int`, `float`, `str`, `bool`) map to scalar refs; slot-typed fields (`list[Slot]`, `Slot`) map to spec entity refs; class-typed fields map to `OntologyClass` refs. This means the spec model owns what Config field values resolve to at compile, while `datacontext-config-binding.md` owns the substitution mechanism.

The spec validator catches Config-field typos at registration: any `Config.<field>` reference in a DataContext expression that does not resolve to a real field on the bound Config class raises immediately (commitment 16 — loud failures). Type mismatches between the `ConfigRef`'s declared type and the Config field's declared type are likewise caught at registration, not at compile.

## Description canonicality

`description` fields are **runtime** by default — changes do not bust the content hash. `diff()` still emits `DescriptionEdited` events. Matches dbt manifest convention; lower-risk than treating descriptions as canonical (typo fixes shouldn't rehash the universe). See `spec-versioning.md` for the runtime-vs-canonical field taxonomy.

---

## Constraints

- Must serialize cleanly across the persistence boundary (real refs in-memory; name-flattened in JSONB; round-trips via knot's API layer).
- Must validate at parse — typos, wrong types, missing references surface as `ValidationError` with field paths. `extra="forbid"` on all metaschema classes. Reference resolution failures (e.g., `slot.range` names a class that doesn't exist) surface during the rehydration pass.
- Must be the single source of truth — SDK generator and SQL generator read this model; no parallel structures.
- Must accommodate the structural-reference graph used for auto-joins — `ReferencePattern` fields on `Slot` are the source for FK join paths in `compute_spec_reference_graph` (per `impact-analysis.md`).
