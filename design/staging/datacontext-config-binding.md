# DataContext binding to Config — symbolic refs in expression trees

**Status:** decided. Integrates with commitments 3, 4, 5, 16. Closes the question of how runtime-editable Config knobs surface inside the static DataContext expression tree.

---

## The question

A bound impl declares both:

- A **DataContext** — typed class-level attribute holding an SDK expression that knot walks at registration to know which classes / slots are read.
- A **Config** — impl-defined Pydantic class holding runtime-editable knobs (thresholds, mode flags, slot lists, source allowlists).

Can a DataContext expression reference Config fields, e.g., `Movie.where(Movie.year > config.min_year)`?

The architectural answer is yes — without it, every Config-tunable filter has to live in Python after the read, defeating the runtime-edit story. The non-trivial part is the **mechanism**, given that DataContext is class-level and Config is per-instance / per-binding.

## The mechanism — symbolic Config refs

A bound impl's Config class is referenced via a `Config` symbol that produces typed expression-tree nodes (the same shape `Movie` produces for slot refs):

```python
class MyERConfig(SpecBase):
    min_year: int = 1900
    blocking_radius: float = 0.85
    signal_slots: list[Slot]
    active_sources: list[Source]

class MyERImpl:
    Config: ClassVar[type[SpecBase]] = MyERConfig
    movies: DataContext = Movie.where(Movie.year > Config.min_year)
    candidate_pairs: DataContext = (
        Movie.with_slots(*Config.signal_slots)
             .where(Movie.source.in_(Config.active_sources))
    )
```

Mechanism:

- `Config` is a metaclass-generated namespace mirroring the impl's declared Config class.
- `Config.min_year` is a typed expression-tree reference of type `int`. `Config.signal_slots` is a typed reference of type `list[Slot]`. Type checker treats them as their declared field types.
- At parse / registration time, the spec validator confirms each `Config.<field>` resolves to a real field on the bound `Config` class (loud failure otherwise — commitment 16).
- At **compile time**, knot reads the bound impl's current Config snapshot from postgres-control and substitutes:
  - Primitive fields → literal values
  - Slot / class refs → resolved spec entity references
- The substituted DataContext is part of the compile-hash input. Config edits change the hash; reruns of the same hash see identical Config (commitment 3).
- At **run time**, `ctx.config` is the same Config snapshot the compile baked in. The impl reads it as data (commitment 4); no late-binding, no fresh API hit.

## Where Config hydrates in the lifecycle

| Lifecycle point | What happens to Config |
|---|---|
| **Author edits via API / UI** | New revision written to postgres-control. Existing in-flight runs are unaffected. |
| **Pipeline trigger (fresh compile)** | Knot reads the *current* Config revision per impl binding. Substitutes into DataContexts. Bakes everything into the compile hash. |
| **Run dispatch (orchestrator)** | The compiled `WorkflowSpec` carries the Config snapshot; the orchestrator ships impl source + Config to the runner. |
| **Impl execution** | `ctx.config` is the baked snapshot. Deterministic for the run. |
| **Audit walk-back** | `compiled_workflows.spec` pins the exact Config revision; rerunning the hash replays bit-identically. |

The promise "Config is editable via the API" is satisfied at the **trigger boundary**, not the run boundary. That's what keeps audit walk-back mechanical: every run pins a specific Config revision rather than "whatever config was in postgres at some point during the run."

## Static traceability — impact analysis

When a Config field is itself a spec reference (`signal_slots: list[Slot]`, `active_sources: list[Source]`), it joins the impact-analysis surface alongside the DataContext expression:

- Editing a slot referenced by a Config field triggers impact analysis on every binding whose Config holds that slot.
- The walk is mechanical: knot iterates bindings, walks each binding's Config (Pydantic field-by-field), follows refs into the spec graph.
- Already implied by commitment 4 ("Impact analysis walks every binding's stored declarations alongside spec edges") — this section pins down *what shapes Config can hold* (anything Pydantic-expressible whose refs the spec validator can resolve).

## Why this mechanism is the only one that fits

| Alternative | Why it's wrong |
|---|---|
| **Factory method `def datacontexts(self, cfg) -> dict[str, DataContext]`** | Breaks commitment 4's class-level attribute rule. Static traceability requires invocation with a sentinel config; brittle. |
| **Lazy lambda `DataContext(lambda self: ...)`** | Lambdas defeat static introspection. Can't walk the spec graph at registration without executing the lambda — and the lambda may need a config that doesn't exist yet. |
| **String params `Movie.where(Movie.year > param("min_year"))`** | Stringly-typed; violates the real-types-over-discriminator-strings rule (`_meta/design-thinking-patterns.md` pattern 3). No type checking; field-name typos surface only at registration. |
| **Disallow — filter in Python after the read** | Reads everything from the lake then filters in memory. On large fact tables this is unacceptable; defeats the runtime-edit story for thresholds. |
| **Late-binding at run time (don't bake)** | Two runs with the same compile hash can read different data. Breaks commitment 3 (compile hash is run identity). |

Symbolic Config refs are the only mechanism that keeps:

- Class-level DataContext attributes (commitment 4)
- Real types throughout (`_meta/design-thinking-patterns.md` pattern 3)
- Static traceability at registration (commitment 4, impact analysis)
- Audit-deterministic compiles (commitment 3)
- Loud failure on bad refs (commitment 16)
- Runtime-editable Config (commitment 5)

## Implementation notes

- `Config` namespace is generated per-impl-class via metaclass that mirrors the declared `Config` Pydantic class. Field types are looked up from `Config.model_fields`; expression-node types are emitted accordingly (primitive types map to scalar refs; `list[Slot]` maps to a slot-list ref; etc.).
- The expression-tree extension is a single new node type, e.g., `ConfigRef(field_path: tuple[str, ...], field_type: type)`. Single-dispatch visitors over the expression tree handle it the same way they handle `SlotPath` / `RelationRef`.
- Substitution at compile is a pure transform: walk the tree, replace each `ConfigRef` with the corresponding literal / resolved-entity ref.
- `ConfigRef` does **not** mutate or read Config at registration time — it only carries a typed path. Substitution is the only place Config values enter the tree.

## What's still implementation detail

- The exact symbol name (`Config` vs `cfg` vs `MyConfig`) — ergonomic bikeshed.
- Whether `Config.x.y` (nested field) syntax is supported via `__getattr__` chaining or requires explicit path notation — code-shape choice.
- Per-backend SQL emission for substituted literals — QueryExecutor concern.
- Impact-analysis batching across many bindings sharing a Config-driven filter — perf concern, not architecture.

## Cross-references

- `core-design.md` § 3 (compile hash = run identity) — Config substitution is part of the compile input; edits trigger fresh compiles.
- `core-design.md` § 4 (DI seam) — Config refs in DataContext expressions are now formally part of the seam.
- `core-design.md` § 5 (knot-hosted impl source, runtime-editable Config) — preserved; Config edits land in postgres-control, surface at the next trigger.
- `core-design.md` § 16 (loud failures) — Config-ref resolution failures (typo, missing field, type mismatch) raise at registration.
- `staging/multi-valued-semantics.md` — orthogonal to this; lens stance is per-protocol, Config refs are per-binding.
- `staging/auto-generated-sdk.md` — `Config` namespace generation joins the SDK codegen surface.
- `staging/di-input-contract.md` — DataContext expressions may now contain `ConfigRef` nodes.
- `staging/spec-model.md` — Config is per-impl-binding; this doc owns the field semantics; spec-model owns the ontology side.
