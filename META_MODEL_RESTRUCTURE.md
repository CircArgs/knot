# Meta-model restructure plan

knot's spec layer was inherited largely from LinkML. The CKG team's actual
use case (curating a knowledge graph from heterogeneous external + internal
sources, growing entity domains over time) doesn't need most of what LinkML
ships. This doc captures the design decisions made during the restructure
discussion so future-me / subagents / the team can read the rationale, not
just see the diff.

## Posture (load-bearing)

- **knot is tailored for one specific job**: support a curated multi-source
  entity graph for the CKG team. Not a general ontology language. We are
  free to make opinionated cuts that LinkML can't make because LinkML is
  trying to serve many audiences.
- **Interrogate every named entity.** If a spec entity is just a label for
  "a bundle of existing things" or doesn't earn its complexity for knot's
  use case specifically, cut it. This pass culls 8 entities/fields.
- **The Pydantic-shaped meta-model is the target shape.** A recursive
  `TypeExpression` + slot-level constraints is the clean version of
  "what every modern type system already does." The current
  `TypeDefinition + slot.range + slot.multivalued` split is a wart.

## Decisions

### Cut

| Entity / Field | Why |
| --- | --- |
| `TypeDefinition` entity | Zero-information row when only `base` is set; pattern wasn't propagating to slot validation anyway. Subsumed by `TypeExpression`. |
| `slot.range` field | Subsumed by `slot.type` (recursive `TypeExpression`). |
| `slot.multivalued: bool` | Encoded as `Array(...)` in the type expression. |
| `PermissibleValue` entity | Just a `list[str]` on `SlotConstraints`. The entity earned nothing structurally. |
| `SlotOverride` entity | Per-class overrides of inherited slots is the kind of "subtle correctness footgun" that pays in ambiguity. Concrete classes declare their own slots. |
| `UniqueKey` entity | Expressible as a `Constraint`. |
| `DirectRef` / `DiscriminatedRef` / `IdentifierPattern` | Three flavors of "slot references a class." One `ClassRef(target_class)` is enough. |
| `class.definition` (defined classes / DL-style VIEWs) | Migration + impact-analysis complexity that the team doesn't need; expressible as saved queries / lake-layer views. |
| `Struct` in TypeExpression | Non-trivial (compile + validation + storage). Deferred — add later only if a use case forces it. |

### Add

| Concept | Why |
| --- | --- |
| `TypeExpression` (recursive sum) | The clean type-system shape. `Primitive(name)`, `Array(of: TypeExpression)`, `ClassRef(target_class)`. |
| `SlotConstraints` (pattern, min, max, permissible_values: list[str]) | All slot-level constraint data in one place. |
| `Source.trust_score: float = 1.0` | Initial scalar trust for `ARGMAX_TRUST` resolution; team can encode "imdb > wiki" up front. |
| `Source.slot_priors: dict[slot_name, (alpha, beta)]` | Optional per-(source, slot) Beta priors for `POSTERIOR_MEAN` / `LCB`. Encodes structured prior knowledge ("imdb-year is canonical but imdb-rating is sentiment-biased"). Absent priors default to `Beta(1, 1)`. |
| Bootstrap step at publish | Seeds `trust_config` and `trust_posteriors` from declared priors. One-time write at publish. |
| Default canonical_id at ingest = `{source_name}:{source_row_id}` | Debuggable (operator can read the canonical_id and trace it to a source-row without joining); merge survivor is understood as an anchor; lineage table preserves history. |

### Keep

| Concept | Why |
| --- | --- |
| `class.is_a` (single inheritance) | Cross-domain slot reuse (adding `Game` next to `Movie`) is the load-bearing scenario; absent inheritance, the spec author duplicates ~10 slot definitions per new media type. |
| `class.mixins` (composition) | Crosscutting concerns (`Datable`, `Localized`, `Rated`) cleanly composed. **Runtime spec entity** — UI-creatable, diffable. Team applies mixins via the API; not built only at spec-author Python time. |
| `class.abstract` | One-line annotation; trivial cost. Marks type-marker classes (like `MediaItem`) as never-instantiated. |
| `OntologyClass`, `Slot`, `Source`, `Constraint`, `Spec` envelope | The five entities that earn their place. |
| All six correction operations (property / merge / split / add / tombstone / reject_contribution) | Already designed; no change. |

## Fallout / dependent fixes

### Bootstrap step disappears

The current bootstrap publishes a "base spec" containing the 6 standard
primitives as `TypeDefinition`s. Under the new model, primitives are
**language-level** (`Primitive("string")` etc. in `TypeExpression`), not
spec data. The base spec concept goes away. First drafts start empty;
primitives are referenced inline.

This deletes:
- `core/knot/spec/primitives.py` (or repurposes to just `STANDARD_PRIMITIVE_NAMES = [...]` for validation)
- The bootstrap call in `core/knot/api/main.py`'s lifespan
- The bootstrap helper in `core/knot/graph/spec.py:bootstrap_base_spec`
- The bootstrap-related test fixtures

### Demo fix — sources have native identifier slots

The current demo uses `imdb_id` as the identifier slot for `imdb`, `tmdb`,
AND `wiki` sources. This is nonsensical: it presumes ER has already
happened and conflates source-native ids with shared canonical_ids.

The fix: each source has its OWN native identifier slot.
- `imdb` source → identifier_slot = `imdb_id`
- `tmdb` source → identifier_slot = `tmdb_id`
- `wiki` source → identifier_slot = `wiki_slug`

The class `Movie` then has all three identifier slots declared (`imdb_id`,
`tmdb_id`, `wiki_slug`), plus its content slots. Each source ships rows
populating ITS identifier and the content slots. Without ER, three
separate canonical entities (`imdb:tt1375666`, `tmdb:27205`,
`wiki:Inception_(film)`). ER (when wired) calls the merge API to collapse
them.

The demo notebook needs full rewriting to reflect this.

### Postgres mapping under new TypeExpression

- `Primitive("string")` → `TEXT`
- `Primitive("integer")` → `INTEGER`
- `Primitive("float")` → `DOUBLE PRECISION`
- `Primitive("boolean")` → `BOOLEAN`
- `Primitive("datetime")` → `TIMESTAMPTZ`
- `Primitive("date")` → `DATE`
- `Array(Primitive(...))` → corresponding `T[]`
- `ClassRef(target_class)` → `TEXT` (canonical_id reference)
- `Array(ClassRef(...))` → `TEXT[]`

The compile dispatch becomes a single-dispatch on TypeExpression instead
of dispatching on `slot.range is TypeDefinition | OntologyClass` plus
the `slot.multivalued` boolean.

### Migration diff under new shape

Most existing `Change*` records remain valid (AddClass, DropClass, AddSlot,
DropSlot, etc.). `ChangeSlotType` becomes `ChangeSlotTypeExpression`
(prev_type, new_type as full TypeExpressions). `ChangeSlotMultivalued`
goes away (subsumed). New change records for trust priors:
- `ChangeSourceTrustScore`
- `ChangeSourceSlotPrior(source, slot, prev_prior, new_prior)`

## Touch list (rough scope per layer)

| Layer | Files | Scope |
| --- | --- | --- |
| Pydantic meta-model | `core/knot/spec/metaschema.py` | Rewrite Slot, OntologyClass, Source; add TypeExpression hierarchy; delete TypeDefinition, PermissibleValue, SlotOverride, UniqueKey, DirectRef/DiscriminatedRef/IdentifierPattern |
| Serialization | `core/knot/spec/serialization.py` | Handle new TypeExpression subclasses in $kind registry |
| Canonical hash | `core/knot/spec/canonical.py` | Same RUNTIME-field handling; new types just walk via existing recursion |
| Effective slots | `core/knot/spec/effective_slots.py` | No change to algorithm — still walks mixins + own |
| Errors / expressions | `core/knot/spec/errors.py` / `expressions.py` | No change |
| Storage | `core/knot/db/spec_store.py` | Bootstrap removal; rest works via serialization |
| Compile postgres | `core/knot/spec/compile/postgres/_types.py` | Dispatch on TypeExpression |
| Compile postgres | `core/knot/spec/compile/postgres/migration.py` | New ChangeSlotTypeExpression; drop ChangeSlotMultivalued; add ChangeSourceTrustScore / ChangeSourceSlotPrior |
| Compile postgres | other files in `compile/postgres/` | Adjust dispatchers as needed |
| Compile graphql | `core/knot/spec/compile/graphql/` | Adapt schema gen to TypeExpression |
| Row models | `core/knot/api/row_models.py` | Build Pydantic row models from TypeExpression |
| Graph orchestration | `core/knot/graph/spec.py` | Update add_slot/add_class/add_source signatures; remove bootstrap; drop add_type (no more TypeDefinition entity) |
| API routes | `core/knot/api/spec.py` | Update every request shape; drop /types routes; add /sources/{name}/trust_score and /sources/{name}/slot_priors/{slot}/[seed|reset] routes; same for delete cascades |
| Ingest | `core/knot/graph/ingest.py` | Change default canonical_id assignment to `{source_name}:{source_row_id}` |
| Lifespan | `core/knot/api/main.py` | Remove bootstrap call |
| Tests | `core/tests/unit/**` + `core/tests/integration/**` | Rewrite specs in test fixtures; remove primitive-bootstrap tests; add trust-prior tests; expect bootstrap removal in concurrency tests |
| Demo | `notebooks/demo.py` | Full rewrite of the spec dict; sources with native identifier slots; ingest rows per source with their own native ids; canonical_id no longer = imdb_id |
| UI Spec graph | `ui/src/lib/buildGraph.ts` | Render types inline (no TypeNode by default); slots show their type via TypeExpression text |
| UI Forms | `ui/src/components/forms/*.tsx` | TypeForm gone; SlotForm gets TypeExpression composer (Primitive/Array/ClassRef picker); SourceForm gets trust_score + slot_priors fields |
| UI Data graph | (no structural change — data plane unchanged) |
| UI Toolbar | Drop primitive-types toggle (no separate TypeDefinition nodes anymore) |

## Sequencing recommendation

Three sequential executor dispatches, with checkpoints between for design
verification:

1. **Foundation** (~1-2h scope) — metaschema + serialization + canonical
   hash + effective_slots. Locks the type contract. Verify tests in
   the spec layer still pass before moving on.
2. **Backend sweep** (~4-6h scope) — storage, compile (postgres types +
   migration emitters + change records), row_models, graph orchestration,
   API routes (drop /types, add trust-prior routes), ingest default
   canonical_id change, all integration tests rewritten. The biggest chunk;
   high risk of needing course correction. Verify the API is end-to-end
   testable before moving on.
3. **Frontend + demo** (~2-3h scope) — UI forms (TypeExpression composer
   is the hard part), buildGraph + node renderers (types inline, no
   TypeNode), demo notebook rewrite with proper source-native identifier
   slots. Depends on the API contract being stable from step 2.

Between each step: review the diff, verify tests pass, eyeball the API
shape, eyeball the UI (where applicable). Course-correct if needed before
the next dispatch.

## What this is NOT changing

- Data plane (per-class postgres tables, SCD2 bindings, ingest flow):
  no structural change. Just the type of each column may shift if a
  slot's type expression changes.
- Correction operations: all six stay as designed.
- Trust resolution algorithm (ARGMAX_TRUST / POSTERIOR_MEAN / LCB): unchanged.
- Extension framework (dispatcher + events + ER + DQ): unchanged.
- Sibling services (ai/, er/): unchanged.
- UI Query page (Monaco + Apollo): unchanged.
- UI Data graph page: unchanged.

## Open questions resolved

These were debated and decided before this doc:

- **Inheritance cluster**: `is_a` + `mixins` keep, `definition` cut.
- **`abstract`**: keep.
- **Default canonical_id**: `{source_name}:{source_row_id}`.
- **Trust prior granularity**: scalar + optional per-(source, slot) Beta priors.
- **Mixins as runtime entity**: yes (UI-creatable, diffable, API-driven).
- **Struct**: deferred.
