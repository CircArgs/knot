# Impact analysis — spec reference graph + diff

**Status:** staging — captured for review, not yet integrated into authoritative docs.

When the ontology changes (slot rename, class removal, derivation rule edit), tools need to determine what's affected downstream. **knot computes both the spec-internal reference graph and the pipeline-wide extension** (sources, ER strategies, configs, bound impl declarations).

This split mirrors established convention: dbt-core emits `manifest.json`; SQLAlchemy exposes `inspect()` over Core. The compiler / spec layer publishes the graph; the runtime / pipeline layer consumes and extends.

---

## SpecGraph

Pure-spec, deterministic graph computed from the Pydantic spec alone — no runtime state, no pipeline knowledge required. Pure function: `Spec → SpecGraph`. Side-effect-free, testable in isolation.

```python
class SpecGraph(BaseModel):
    spec_content_hash: str                 # sha256 of canonical_dump(spec); revision implicit
    nodes: list[GraphNode]                 # classes, slots, types, constraints, derivations, identifier patterns
    edges: list[GraphEdge]                 # see edge types below

    def references_to(self, node: NodeRef) -> list[GraphEdge]: ...
    def references_from(self, node: NodeRef) -> list[GraphEdge]: ...
    def transitively_dependent_on(self, node: NodeRef) -> set[NodeRef]: ...

def compute_spec_reference_graph(spec: Spec) -> SpecGraph: ...
```

`spec_content_hash` is the cache key and the identity token for `SpecGraph` storage. Revision is implicit via the hash; no separate `revision_id` field.

---

## Edge types

| Edge name | Meaning | Source field |
|---|---|---|
| `slot_range -> class` | A slot's range refers to an ontology class | `Slot.range` (when value is an `OntologyClass` reference) |
| `mixin -> class` | A class includes a mixin | `OntologyClass.mixins` |
| `is_a -> class` | A class extends a parent class | `OntologyClass.is_a` |
| `reference_pattern -> class` | A class participates in a reference pattern (e.g., discriminator) | `Slot.reference` |
| `derivation -> slot` | A derivation expression references a slot | walked from `RelationRef` / `SlotPath` nodes inside the expression tree |
| `derivation -> class` | A derivation expression traverses a class | walked from `RelationRef.from_class` and `SlotPath.from_class` nodes |
| `slot_override -> slot` | A class's `slot_overrides` entry refines a base slot | `OntologyClass.slot_overrides` |
| `identifier_pattern -> slot` | A class's `IdentifierPattern` references component slots | `OntologyClass.identifier_pattern` |
| `constraint -> slot` / `constraint -> class` | A `Constraint.body` traverses spec entities | walked from the expression tree |

The graph builder walks expression trees recursively (per `derivation-and-constraints.md`) and collects every `OntologyClass` / `Slot` / `TypeDefinition` reference encountered.

---

## Change taxonomy

Typed change classes cover high-frequency semantic changes. A catch-all handles the long tail.

Change events identify entities by **name** (not by Python object reference) because diff compares two distinct `Spec` instances — the entities in `spec_a` and `spec_b` are different objects even when they represent "the same" class. Name is the cross-spec identity. Within a single spec, real refs are used everywhere else; the diff layer is the one place names are first-class.

```python
class Change(BaseModel): ...

class NodeKind(Enum):
    CLASS = "class"
    SLOT = "slot"
    TYPE = "type"
    CONSTRAINT = "constraint"

# Class-level
class ClassAdded(Change):             class_name: str
class ClassRemoved(Change):           class_name: str
class ClassRenamed(Change):           old_name: str; new_name: str

# Slot-level
class SlotAdded(Change):              slot_name: str
class SlotRemoved(Change):            slot_name: str
class SlotRenamed(Change):            old_name: str; new_name: str
class SlotRetyped(Change):            slot_name: str; old_range_name: str; new_range_name: str
class SlotCardinalityChanged(Change): slot_name: str; old_multivalued: bool; new_multivalued: bool
class SlotIdentifierToggled(Change):  slot_name: str; now_identifier: bool

# Inheritance / mixin
class MixinAdded(Change):             class_name: str; mixin_name: str
class MixinRemoved(Change):           class_name: str; mixin_name: str
class IsAChanged(Change):             class_name: str; old_parent: str | None; new_parent: str | None

# Derivation
class DerivationAdded(Change):        owning_class: str; slot_name: str
class DerivationRemoved(Change):      owning_class: str; slot_name: str
class DerivationEdited(Change):       owning_class: str; slot_name: str; old: DerivationExpr; new: DerivationExpr

# Enums / values
class PermissibleValueAdded(Change):   slot_name: str; value: str
class PermissibleValueRemoved(Change): slot_name: str; value: str

# Patterns
class IdentifierPatternChanged(Change): class_name: str; old: IdentifierPattern; new: IdentifierPattern
class ReferencePatternChanged(Change):  owning_class: str; slot_name: str; old: ReferencePattern; new: ReferencePattern

# Slot overrides
class SlotOverrideChanged(Change):     class_name: str; slot_name: str; old: SlotOverride | None; new: SlotOverride | None

# Constraints
class ConstraintAdded(Change):         constraint_name: str
class ConstraintRemoved(Change):       constraint_name: str
class ConstraintEdited(Change):        constraint_name: str; old: Constraint; new: Constraint

# Descriptions
class DescriptionChanged(Change):      node_kind: NodeKind; node_name: str; old: str | None; new: str | None

# Catch-all
class FieldChanged(Change):            node_kind: NodeKind; node_name: str; field: str; old: Any; new: Any
```

When an SDK / UI tool wants the actual entity, it resolves the name against either `spec_a` or `spec_b`. E.g., `ClassRemoved.class_name` resolves against `spec_a.classes`; `ClassAdded.class_name` resolves against `spec_b.classes`.

`DescriptionChanged` is always emitted by `diff()` regardless of canonicality. Per `spec-versioning.md`, descriptions are runtime by default — the change event fires; just doesn't bust the content hash.

---

## Diff and walk

```python
def diff(spec_a: Spec, spec_b: Spec) -> list[Change]: ...

def walk(change: Change, graph: SpecGraph) -> set[Reference]:
    """Given a single change, return every spec node transitively dependent on it."""

def walk_changes(changes: list[Change], graph: SpecGraph) -> dict[Change, set[Reference]]:
    """Batch walk: returns per-change impact sets. Prevents N callers reimplementing grouping."""
```

Output of `walk` / `walk_changes` is a set of `Reference` values — concrete `(node_kind, node_name)` tuples — that downstream tools surface as impact. Example: `SlotRetyped(slot_name="year", old_range_name="string", new_range_name="integer")` walks to every derivation referencing the `year` slot, every class with a slot whose range transitively references the affected slot, etc.

---

## Walk depth

Unbounded with cycle detection. The graph is finite and acyclic in the normal case; `walk` tracks visited nodes and short-circuits on revisit. `max_depth: int | None = None` is an optional parameter for UI display only — does not affect correctness.

---

## Reference granularity

In-spec references are real Pydantic objects (per `spec-model.md` § "References"). For diff change events and `Reference` outputs from `walk` / `walk_changes`, **names are used** because change events span two spec instances (cross-spec identity is name-based) and `Reference` is a serialization-friendly tuple. Format: `Reference = (node_kind, node_name)`.

For slot references where class context matters (e.g., a slot override on a specific class), the change event carries both `class_name` and `slot_name` as separate fields rather than a tuple — clearer in event payloads.

No version component on `Reference`; revision is implicit via `SpecGraph.spec_content_hash`. knot stores the hash alongside its pipeline-wide extensions and can reconstruct the `SpecGraph` for any historical spec by re-running `compute_spec_reference_graph` on the stored spec.

---

## Pipeline-wide extension

knot extends the `SpecGraph` with pipeline-wide edge sets:

- **Sources** — which sources map to which classes via which slots.
- **ER strategy configs + impl DataContexts** — slot/class refs declared via `cross_references`, `blocking`, `matching`, plus the impl's declared DataContext expressions (per `di-input-contract.md`).
- **Trust configs** — per `(source, slot)` trust scores; reference real Slot refs.
- **Materialization impl DataContexts** — what each materialization impl reads (per `di-input-contract.md`).
- **DqRunner impl DataContexts** — what each custom DQ check reads.
- **Built-in DQ config** — per-class / per-slot configuration (per `dq-design.md`).

Pipeline-wide graph = `SpecGraph` ∪ pipeline-config edges. Pipeline-wide impact analysis walks the union graph from the changed node.

There is no separate "meta-graph" object. Single-dispatch visitor functions (`def references_to(target, knot) -> list[Reference]`) walk the typed entity tree directly — sources, ER strategy configs, materialization configs, DqRunner declarations, etc. — and yield references. Same pattern as walking a SQL AST for symbol resolution.

---

## What's tracked vs. what isn't

**Tracked statically (no runtime data inspection):**

- All spec-internal references (typed slot ranges, mixins, is_a, derivations, constraints, identifier patterns, slot overrides).
- All pipeline-config references (source mappings, ER configs, materialization configs, DqRunner configs).
- All impl-declared DataContext references (per `di-input-contract.md` — knot walks them at registration).

**Not tracked statically:**

- Polymorphic references via discriminator (per `spec-model.md` § "Polymorphic references"). The polymorphic class can identify *anything*; concrete connections are visible only through *consumer-declared* dependencies (e.g., an ER strategy's explicit `cross_references: [Identifier]`).

The principle: any cross-class dependency that knot needs to track must be declared explicitly *somewhere* — either via a typed slot range, or via a config field on a consumer that names the dependency. Polymorphic reference classes themselves stay generic; tracking is consumer-declared.

---

## Serialization

- **JSONB on the spec row** — `SpecGraph.model_dump_json()` stored alongside the spec in `compiled_workflows`. Available for immediate query.
- **Sidecar artifact** — knot may also persist the graph as a standalone artifact (file, object-store blob) for tooling that doesn't want to query the DB. Storage topology is a deployment concern.

`SpecGraph` serializes cleanly via Pydantic's `model_dump_json()`.

---

## Cache invalidation

Cache key = `compute_content_hash(spec)`. Because `compute_spec_reference_graph` is a pure function, caching is trivially correct: same hash → same graph. Callers cache `SpecGraph` keyed on `spec_content_hash`. No TTL required; the graph is invalidated exactly when the spec changes.

---

## API surface

- `compute_spec_reference_graph(spec) -> SpecGraph`
- `diff(spec_a, spec_b) -> list[Change]`
- `walk(change, graph) -> set[Reference]`
- `walk_changes(changes, graph) -> dict[Change, set[Reference]]`
- The `SpecGraph`, `Change`, `Reference` Pydantic models.

Pipeline-wide extensions (sources, ER configs, materialization configs, DqRunners, impl DataContexts) layer on top via separate visitor functions over those typed entities.

---

## Open

- **`walk_changes` merge semantics** — if two changes in the batch both affect the same downstream node, the sets union per-change. Confirm this is what pipeline-wide walk wants, or whether a flat deduplicated set is more useful.
