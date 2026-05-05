# 2. The DI seam

Three commitments — 4, 5, and 6 — describe the **single architectural primitive** through which every team-bound stage in knot is customized. ER, materialization, custom DQ, query execution, the translator: all follow the same pattern. The "seven interfaces" framing in earlier docs is loose; the architectural primitive is one DI shape, applied to multiple seams.

The thing that makes this work is **lens semantics**: the same SDK expression resolves to different backing tables depending on which protocol the impl implements. Knot's runtime materializes the right view per stage; impl writers do not choose the lens.

---

## Commitment 4 — Universal DI seam: protocol + DataContexts + pure-data context + impl Config.

Every team-bound stage follows one pattern:

- **Protocol** — what the impl must implement (e.g., `ERProtocol.resolve(ctx, **datacontexts) -> list[Edge]`).
- **DataContexts** — typed class-level attributes on the impl declaring what data it reads. Pydantic-typed; built from SDK expression trees over real ontology refs. Detected by typing, not naming. Multiple per impl. Walked at registration for impact analysis; materialized at runtime via the bound `Materializer`.
- **Context** — a pure-data Pydantic object knot fills in per run: the impl's config, binding info, upstream-job state. **No methods.** Reads as `ctx.config.threshold`, etc.
- **Config** — an impl-defined Pydantic class declaring runtime-editable knobs (thresholds, mode flags, slot lists). Stored in postgres-control; runtime-editable via API; threaded into ctx as data.

### Rationale

Layer 1 establishes that customization is unavoidable: ER algorithms differ per domain, materialization targets differ per consumer, DQ checks blend statistical bundles with ML-trained classifiers. The team needs a way to plug in their thing without forking knot.

The risk is that "their thing" gets a different shape per stage — a separate binding model for ER, a separate one for materialization, another for DQ. Five binding mechanisms is a maintenance liability and a learning tax. The design-thinking pattern is direct (from [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md), pattern 5): "if the universal DI pattern covers the case, use it. Don't invent a new binding shape per stage."

The four pieces above are what every stage needs. Protocol carries the contract. DataContexts carry what the impl reads (so impact analysis is statically traceable without AST scanning or impl execution). Context carries control-plane data the impl needs at runtime, as pure data with no methods. Config carries runtime-editable knobs. Once you have all four, you don't need a fifth thing.

The "no methods on context" decision is not cosmetic. It's what keeps the audit story uniform: spec edits, config edits, binding-info changes, and upstream-job state are all *data*, walked the same way. A method-bearing context object would invite bypassing the declaration channel — "let me just call `ctx.fetch_extra_data()` here" — which would silently extend the impl's footprint past what knot has registered.

### Comparator anchor

| System | Stage-binding shape | Static traceability of impl I/O |
|---|---|---|
| **dbt** | Models are SQL templates with `ref()` macros; tests are YAML; one shape | Per-model lineage parsed from rendered SQL; column-level requires extra. |
| **DataJunction** | Single fat `BaseQueryServiceClient` ABC; subclasses cherry-pick (~10 methods, 30% impl rate) | Subclasses opt out of methods they don't support; defaults raise. |
| **Splink** | Function callbacks per pipeline step (blocking, comparison, etc.) | No declarative declaration of what the callback reads. |
| **Apache Beam** | `DoFn` / `PTransform` with implicit input/output through pipeline graph | Pipeline graph carries lineage; per-DoFn data dependencies inside the function. |
| **knot** | One pattern per stage: protocol + DataContexts + ctx + Config | DataContexts are class-level Pydantic-typed attributes; walked at registration. |

DataJunction is the closest *anti-pattern* — one fat ABC, low impl rate, methods grouped by convenience rather than by orthogonal concern. Knot's split (per [Commitment 12 in 6-execution-delegation.md](6-execution-delegation.md)) is a deliberate response. Beam's `DoFn` model is structurally similar but doesn't separate declared inputs from runtime reads.

### Tradeoffs / honest costs

- **DataContexts are a learning tax.** A team writing their first ER impl has to understand that `candidates: ClassVar[DataContext[Movie]] = DataContext(primary=Movie, filter=...)` is a declaration knot inspects, not just a Python attribute. The pattern is well-precedented (SQLAlchemy ORM declarative models, Pydantic-FastAPI dependency injection) but still has a ramp.
- **The cross-check between Config and DataContexts is real machinery.** Any spec entity referenced by config must be reachable through one of the impl's declared DataContexts (per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Impl config"). The compile fails on mismatch. This catches silent breakage but adds a check the team has to debug when it fires.
- **No generic / parameterized impls.** One Python class with binding-time-resolved DataContexts, reusable across multiple bindings, is **not** a knot mechanism. Code reuse is via Python inheritance or library helpers; one concrete impl class per binding. This is decided ([`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided"); the cost is duplication when two bindings differ only in parameterization.
- **No method-bearing context.** A team accustomed to "just give me a context with handy methods" will need to acclimate. The audit story is what justifies the constraint; the constraint is real.

### Lens semantics for DataContexts (load-bearing)

The same SDK expression — `Movie.year > 1900` — resolves to **different backing tables depending on the protocol the impl implements**. The protocol also determines the slot type the SDK exposes and whether knot attaches a trust-resolution CTE:

| Protocol | `disagreement_stance` | Backing lens | Slot type | Bare `Movie.year > 1900` |
|---|---|---|---|---|
| `ERProtocol` (resolve) | `DISAGREEMENT_AWARE` | `per_source_facts/Movie` | `MultiValued[T]` | Type error — must spell reduction |
| `DqRunner` (custom DQ) | `DISAGREEMENT_AWARE` | Per declared DataContext | `MultiValued[T]` | Type error — must spell reduction |
| Materialization impl (publish) | `RESOLVED` | `resolved_facts/Movie` + derivations | `Resolved[T]` | Compiles; trust-CTE attached |
| `Translator` (consumer query) | `RESOLVED` | `resolved_facts/Movie` + overlay | `Resolved[T]` | Compiles; trust-CTE + overlay |

**The impl writer does not choose the lens or the slot type.** The protocol determines both. Under `RESOLVED`, bare `Movie.year > 1900` compiles and knot attaches the trust-resolution CTE (per the slot's `resolution_policy`). Under `DISAGREEMENT_AWARE`, it is a mypy type error — the impl must spell its reduction (`from_source(...)`, `all_()`, `winner()`). The canonical per-protocol assignment is in [`design/staging/multi-valued-semantics.md`](../../../design/staging/multi-valued-semantics.md).

This is established in [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md) §"Reuse across contexts — lens semantics" and [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Cross-class traversals and the pinning lens". For relation classes, the lens additionally applies cross-class pinning ([Commitment 8 in 3-data-and-resolution.md](3-data-and-resolution.md)) automatically: `Credit.movie.*` reads Movie data as of the pinned Movie run hash without the impl writer thinking about historical revisions.

??? details "Deep-dive: a worked impl"

    From [`design/staging/seam-contract-pattern.md`](../../../design/staging/seam-contract-pattern.md) and [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md):

    ```python
    from typing import ClassVar
    from knot.ontology import Movie, Credit, Identifier
    from knot.di import ERProtocol, DataContext, Edge

    class MovieResolver(ERProtocol):
        # DataContexts: class-level, typed, walked at registration.
        candidates: ClassVar[DataContext[Movie]] = DataContext(
            primary=Movie,
            include=[
                Movie.identifiers,
                Movie.credits.where(Credit.role.in_(["director"])),
            ],
            filter=(Movie.year > 1900),
        )
        cross_refs: ClassVar[DataContext[Identifier]] = DataContext(
            primary=Identifier,
            filter=(Identifier.entity_class == Movie),
        )

        # Config: impl-defined Pydantic class, runtime-editable.
        class Config(BaseModel):
            blocking: list[Slot]              # real Slot refs
            matching: list[Slot]
            cross_references: list[OntologyClass] = []
            threshold: float = 0.85

        def resolve(self, ctx, candidates, cross_refs) -> list[Edge]:
            # ctx: pure data — config + binding info + upstream state
            # candidates / cross_refs: pre-materialized rows per declared DataContexts
            ...
    ```

    What knot does at registration ([`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md)):

    1. Inspect the class. Collect every attribute whose type is (or contains) `DataContext`. No instantiation.
    2. Walk each. Single-dispatch visitor over the SDK expression tree yields every `OntologyClass` / `Slot` reference.
    3. Validate. Every reference must resolve in the current spec. Dangling refs → registration error.
    4. Store. The union of walked references becomes the impl's declared inputs.
    5. Cross-check against the impl's config. Config-referenced spec entities must be a subset of the union of declared inputs.
    6. Hash into compile. The impl's declared inputs are part of the pinned-revision payload.

    The runtime fulfillment, per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided":

    - DataContext resolution handoff is a **table / view name in the lake**. Knot materializes a view at a stable name keyed to `(run, impl, DataContext)`, hands the impl the name. The impl reads from that name using its own engine — no SQL strings handed across the seam, no in-memory bytes shipped from knot.
    - For relation-class workflows, the lens automatically applies cross-class pinning. `Credit.movie.identifiers` resolves through Movie's pinned run hash; the impl writer just writes plain ontology expressions.

### Multi-class DataContext primary

`DataContext.primary` accepts `OntologyClass | list[OntologyClass] | DerivedSlot | list[DerivedSlot]`. A list fans out at fulfill to N typed views — one per class (node view) or one per derived slot (edge view) — handed to the impl as a dict. Single-class primary is unchanged. The whole-graph case is `DataContext(primary=spec.classes)`: spec additions flow into the next compile automatically, no `Graph` symbol, no `ClassSet` algebraic wrapper. This is one `MultiPrimary` expression-tree extension walked by the same single-dispatch visitors as everything else (commitment 10). See [`design/staging/multi-class-datacontexts.md`](../../../design/staging/multi-class-datacontexts.md).

### Config references in DataContext expressions

DataContext expressions can reference an impl's Config fields via `Config.<field>` symbolic refs — typed `ConfigRef` nodes in the same expression tree as `SlotPath` and `RelationRef`. Substitution happens at compile: knot reads the bound impl's current Config snapshot from postgres-control and replaces each `ConfigRef` with the corresponding literal or resolved spec entity ref before emitting SQL. The substituted DataContext is part of the compile-hash input, so audit determinism holds — every run pins an exact Config revision, and rerunning the same hash replays identically. This enables runtime-editable thresholds and filter values (edit Config via API; next compile picks up the change) without losing static traceability: `Config.<field>` refs are walked at registration by the same single-dispatch visitor that walks slot refs, and typos raise immediately (commitment 16). See [`design/staging/datacontext-config-binding.md`](../../../design/staging/datacontext-config-binding.md) for the full mechanism.

### Cross-links

- [`design/staging/seam-contract-pattern.md`](../../../design/staging/seam-contract-pattern.md) — protocol + context shape.
- [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) — DataContext + Config mechanics, cross-class pinning lens.
- [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md) — SDK and lens semantics across contexts.
- [`design/core-design.md`](../../../design/core-design.md) commitment 4.
- [`design/staging/datacontext-config-binding.md`](../../../design/staging/datacontext-config-binding.md) — Config symbolic refs, substitution at compile, audit determinism.
- [`design/staging/multi-class-datacontexts.md`](../../../design/staging/multi-class-datacontexts.md) — list-valued primary, multi-class fan-out, derived-edge views, Neo4j reference shape.

---

## Commitment 5 — Knot-hosted impl source. Browser-authored. Trusted authors. Single-team.

Bound impls are Python source the team submits to knot via API. Knot stores the source as the canonical artifact, generates the SDK pinned to the current spec hash, loads + introspects the class at registration, validates DataContext refs against the spec, and saves with a content hash of the bytes.

There is **no separate Python service to deploy.** Iteration is browser → save → validate → live for next compile. At workflow runtime, knot ships the source to the orchestrator's runner.

### Rationale

This is a facet of the **reflective ontology compiler** classification: the publish gate validates each spec or impl edit against the runtime that interprets it. CI cannot do this — the runtime state (registered impls, current trust policy, current bound configs) is what the validation needs to check against.

Layer 1 ([Constraint 1 in constraints-and-posture.md](../1-why/constraints-and-posture.md#constraint-1--single-team-no-tenants)) establishes that the team that operates knot also owns every impl, and external users only enter at three narrow surfaces (read published outputs, query via translator, submit corrections). Trust posture is single-team. Hostile-author scenarios are out of scope.

Once the trust posture is fixed, the defensive infrastructure that would otherwise be required (sandboxing, restricted Python, formal-contract enforcement at the impl boundary, deploy pipelines for each impl) all dissolves. Browser-save-and-go is feasible because the team that authored the impl is the team that operates knot.

The design-thinking pattern (from [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md), pattern 4): "explicitly question the trust posture before defaulting to defensive infrastructure. A consolidated single-team trust model dissolves whole categories of complexity."

### Comparator anchor

| System | Impl distribution model | Iteration cycle |
|---|---|---|
| **dbt** | Python files in a repo; `dbt deps` for packages | Edit → push → CI → re-run. |
| **Airflow** | DAGs as Python files in a watched directory | Edit → place in DAGs folder → wait for parser. |
| **Kubeflow Pipelines** | Components packaged + pushed to registry | CI → push → reference by name. |
| **Custom orchestrators with plugin systems** | Python entrypoints, loaded at startup | Restart required. |
| **knot** | Browser → POST source → validate → live for next compile | Edit → save → validate → live. No deploy step. |

The closest neighbor is dbt's `dbt deps` flow, but dbt assumes the file is in a repo and synced via deploy. Knot's "knot stores the source" model is unusual; it's deliberate, and it depends on the trust posture.

### Tradeoffs / honest costs

- **Trust posture is non-negotiable.** A deployment that ever needs multi-tenant safety has to layer sandboxing in via the dispatcher (per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md)). The architecture allows it, but it isn't shipped. Team that grows into hostile-author scenarios has work to do.
- **Knot stores the source.** Postgres-control holds the canonical impl artifact. Backups, migration, and disaster recovery have to account for this. A team that prefers "code lives in a repo" needs to bridge — the bridge is straightforward (push to repo on save), but it isn't automatic.
- **No CI integration by default.** A team that wants pre-merge testing of impls has to build it. Knot's registration-time validation catches structural mistakes (broken refs, config/DataContext mismatch); behavioral testing of the impl logic is the team's responsibility.
- **Re-registration is content-addressed.** Same bytes → same impl revision (deduped). Different bytes → new revision → new compile hash for any workflow using the impl. A spurious whitespace change re-registers; the team needs to be aware of that. Probably fine; flagging for honesty.

??? details "Deep-dive: registration flow"

    From [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided":

    ```mermaid
    flowchart TD
        author[Team author] -->|edits in browser| ui[Knot UI]
        ui -->|POST source| api[Knot API]
        api --> sdkgen[Generate SDK at current spec hash]
        sdkgen --> load[Load impl class — no resolve call]
        load --> introspect[Introspect class attrs]
        introspect --> walk[Walk DataContext expression trees]
        walk --> validate{All refs resolve?}
        validate -->|no| err[Registration error — return 400]
        validate -->|yes| crosscheck[Cross-check Config-referenced slots ⊆ DataContext-reachable slots]
        crosscheck --> hash[Hash impl bytes — content address]
        hash --> store[(postgres-control)]
        store --> live[Live for next compile]
    ```

    At workflow runtime ([`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md)):

    - Knot ships the source to the orchestrator's runner (Spark UDF / PyExecutor / equivalent, per the bound dispatcher).
    - The runner loads and runs it.
    - Knot's process itself doesn't execute impl logic — it only inspects the source for registration validation.

    Trust posture (per [`design/core-design.md`](../../../design/core-design.md) commitment 5):

    - Spec / impl authors are members of the operating team.
    - No sandbox, no restricted Python, no curated import list.
    - External users only enter at three narrow surfaces (read published outputs, query via translator, submit corrections via UI) — none involve writing Python or editing the spec.

    SDK pinning per registration ([`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md)):

    - Each registration captures the current published spec hash.
    - On every spec republish, knot re-validates every registered impl's stored DataContext refs against the new spec.
    - Compatible registrations advance their pin (or stay).
    - Incompatible registrations are flagged "needs new registration against the new spec"; the publish gate refuses any workflow that would use a broken impl.

### Cross-links

- [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided" — knot-hosted source, registration flow, SDK pinning.
- [`design/goals.md`](../../../design/goals.md) §"Who uses knot" — single-team trust posture.
- [`1-why/constraints-and-posture.md`](../1-why/constraints-and-posture.md#constraint-1--single-team-no-tenants) — single-team / no-tenants posture.

---

## Commitment 6 — The impl IS the strategy. There is no separate "strategy spec" entity.

For ER (or any per-class stage), there's exactly one bound impl per (stage, class). The impl plus its postgres-control config IS what humans call "the strategy." There is **no separate strategy node**, no parallel registry. The impl name is a tracking label with no semantic role.

Switching strategies = swapping impls. Re-registering an impl with new bytes produces a new content hash and a new compile hash. Strategy iteration via config edit (runtime); strategy iteration via algorithm change requires re-registration.

### Rationale

The design-thinking pattern is direct (from [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md), pattern 3): "interrogate every named entity for whether it earns its place." The "ER strategy" was originally going to be its own spec node alongside source / class / DQ check. The cut: "no 'strategy' — an impl has a name assigned for tracking aid so people can easily see, but other than that there isn't a strategy, the impl is the strategy."

Mechanically, what humans call "the strategy" is `(impl + config)`. The impl carries the algorithm; the config carries the runtime-editable knobs (thresholds, blocking-key slot lists, transitivity rule, mode). No third thing is required. Adding a separate `Strategy` spec node would create a parallel registry that contains nothing the impl + config don't already contain.

The "no competing impls" decision (per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Impl identity is registration-bytes-content-addressed") closes the loop: per (stage, class), exactly one impl is bound. There's no scenario where two ER impls compete for Movie. Strategy iteration is "swap the binding"; algorithm comparison is a separate concern handled outside knot's identity model.

### Comparator anchor

| System | Strategy / algorithm representation | "Switching" cost |
|---|---|---|
| **Splink** | Settings dict + linker function pair | Edit settings, re-run; no separate registry. |
| **dbt** | Models are the unit; "strategy" not a concept | N/A. |
| **Apache Atlas** | Algorithm metadata as separate type | Sidecar registry. |
| **Splink-on-knot** (hypothetical wrapper) | One ER impl class wrapping Splink + a Config | Edit Config (runtime); change impl bytes (re-registration). |
| **knot** | Impl + Config; no Strategy entity | Strategy iteration is config edit (runtime) or impl re-registration. |

Splink (which knot considers a legitimate ER impl candidate per [why-existing-systems.md](../1-why/why-existing-systems.md#splink--the-right-shape-for-the-er-slice-only)) does not have a separate strategy entity either — settings + linker function is the unit. Knot's pattern matches this discipline at the framework level.

### Tradeoffs / honest costs

- **No A/B strategy comparison built in.** A team that wants to compare two ER algorithms for Movie has to do it outside knot's binding model — register one, run, capture results; switch the binding, run again. Knot doesn't support running both simultaneously and comparing outputs. This is a deliberate constraint; commitment 6 closes the door on competing impls.
- **The impl name is a tracking label.** It has no semantic role. A team that expects to filter by strategy name in audit queries needs to know that the *binding* (which impl was active at compile time) is what audit walks, not the impl's friendly name.
- **Iteration via algorithm change is registration-coupled.** A small change to scoring code is a re-registration, a new content hash, a new compile hash for any subsequent run. This is the same property that makes audit walk-back keepable; the cost is that "tweak a scoring constant" produces a new pinned impl revision in audit.

??? details "Deep-dive: how strategy iteration plays out"

    Two iteration modes, per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md):

    **Config edit (runtime, no redeploy).**

    ```
    POST /impls/<id>/config
    Content-Type: application/json
    {"threshold": 0.9, "blocking": ["title_normalized", "year"], ...}
    ```

    - Stored in postgres-control.
    - Cross-validated against the impl's DataContext-reachable slots; mismatch → compile error.
    - Bumps the workflow compile hash for any workflow using the impl.
    - No re-registration. No new impl revision.

    **Algorithm change (re-registration).**

    ```
    POST /impls/<id>/source
    Content-Type: application/x-python
    <new bytes>
    ```

    - Loads + introspects + walks DataContexts → validates against current spec hash.
    - Hashes the bytes → content-addressed impl revision.
    - If bytes are identical to a prior registration, deduplicated.
    - Bumps the workflow compile hash for any workflow using the impl.
    - The prior impl revision remains in the audit chain for any prior runs that pinned it.

    Audit walk-back: any pipeline_run → compiled_workflow.hash → pinned_revisions → the specific impl revision active at compile time. The impl's name is a label; the revision hash is the identity.

### Cross-links

- [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Impl config" + §"Decided".
- [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md) §"Configuration".
- [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md) pattern 3 — "the impl is the strategy."

---

## Open tensions on this page

- **Generic / parameterized impls are out.** Decided per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided": one impl class per binding; reuse via Python inheritance, not knot-side parameterization. If a team ends up writing 20 nearly-identical materialization impls that differ only in target table, the duplication will be visible. The architectural answer is "use Python inheritance"; whether the ergonomics hold up at scale is something the team will learn by doing.
- **DataContext-reachable cross-check is conservative.** A config that references a slot the DataContexts don't reach fails compile. A team that has a legitimate reason to read a slot conditionally (only when a config value is set) has to declare that slot on a DataContext, even when the config is off. The conservatism is the price of static traceability; for the audit promise it's worth paying.
