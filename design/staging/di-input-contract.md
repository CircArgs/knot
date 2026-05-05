# DI input contract — declarative data needs for static tracing

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How knot traces what a bound DI implementation touches, **without scanning Python source and without running the impl**.

## What knot cares about

The contract for any pipeline-stage DI is bounded:

> "Here is the data you declared; give me what your protocol promises."

Internal logic — blocking, scoring, cascading, retry, caching, whatever — is the impl's concern. Knot does not care how an impl arrives at its output. Knot cares about two surfaces:

1. **What the impl reads.** Surfaced via declared data contexts on the impl class. Used for impact analysis, registration validation, pipeline-health detection.
2. **What the impl returns.** Defined by the protocol the impl implements (e.g., `ERProtocol.resolve` returns `list[Edge]`).

## Problem

For impact analysis ("if I rename `Movie.year`, what breaks?") and registration-time validation ("does this impl reference any classes / slots that don't exist?"), knot needs to know which spec entities each impl touches.

Two non-options:

- **AST scan of impl source.** Fragile, requires Python source on disk, breaks on conditional / computed references.
- **Run the impl with a mock context.** Worse — fragile, slow, may have side effects, can't enumerate branches.

The clean answer is **declarative**: each impl exposes its data needs as static class-level attributes that knot reads at registration without execution.

## Protocol-level `disagreement_stance`

Every Protocol class carries a `disagreement_stance: ProtocolKind` field (`DISAGREEMENT_AWARE` or `RESOLVED`). The SDK type generator reads it at codegen time and emits different slot types for the same ontology slot — `MultiValued[T]` under `DISAGREEMENT_AWARE`, `Resolved[T]` under `RESOLVED`. The stance also determines whether knot attaches a trust-resolution CTE when fulfilling DataContext views. Per-protocol assignment and full semantics are in `staging/multi-valued-semantics.md`; the field is a property of the Protocol class, not of individual impl bindings.

## The pattern

Each impl class declares **arbitrarily many class-level attributes typed as `DataContext`**. Each `DataContext` carries an SDK expression tree describing one read. Shape can be simple (a single class) or complex (denormalized multi-class join with filters and aggregations).

Knot detects declared contexts **by typing**, not by attribute name — any attribute whose annotated type is `DataContext[...]` (or a Pydantic model containing one) participates. Knot walks each at registration, gathers spec references, and stores them as the impl's declared inputs.

```python
from typing import ClassVar
from knot.ontology import Movie, Credit, Identifier
from knot.di import ERProtocol, DataContext, Edge

class MovieResolver(ERProtocol):
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

    def resolve(self, ctx, candidates, cross_refs) -> list[Edge]:
        # impl owns blocking, scoring, cascade, retry — none of knot's business
        ...
```

Knot fetches each declared context per the protocol's backing-table lens (ER reads `per_source_facts`; Merge reads `per_source_facts + entity_bindings`; Materialize reads `resolved_facts + derivations`; etc.) and hands the rows to `resolve()` as named arguments matching the attribute names.

### One context, many contexts — same machinery

Simple impls might declare a single context. Complex impls declare several — different shapes for different parts of their work, or just to make the dependency declaration legible. Knot doesn't care which; it walks whatever's declared.

The earlier "single `input_data` attribute" framing was wrong — it implied one declaration per impl. Reality: one *class*, one *protocol*, **N typed DataContext attributes**.

## What knot does at registration

1. **Inspect the class.** Read class-level annotations; collect every attribute whose type is (or contains) `DataContext`. No instantiation, no method call.
2. **Walk each.** Single-dispatch visitor over the SDK expression tree yields every `OntologyClass` / `Slot` reference.
3. **Validate.** Every reference must resolve in the current spec. Dangling refs → registration error.
4. **Store.** The union of walked references becomes the impl's declared inputs in knot's tracking, alongside `impl_id`, the protocol it implements, and the strategy/stage spec it binds to.
5. **Cross-check against the impl's config.** Config-referenced spec entities (e.g., `blocking`, `matching`, `cross_references` slot/class refs for ER) must be a subset of the union of declared inputs. Mismatch → compile error. (See "Impl config" below.)
6. **Hash into compile.** When a workflow compiles for a class, the bound impl's declared inputs are part of the pinned-revision payload (per `compiled-workflow-hashing.md`). An impl change = re-register = (possibly) different declarations = different compile hash.

## What this gives us

- **Static traceability.** "What references `Movie.year`?" walks back through spec edges, pipeline-config edges (impl configs, materialization configs, etc.), AND impl DataContext declarations — all uniformly, all single-dispatch over real Pydantic refs.
- **No AST scanning.** Python source is not part of knot's trust boundary.
- **No impl execution.** Validation is structural; impls are not run during registration or impact analysis.
- **Pipeline-health enforcement.** Spec edits that would orphan a declared impl reference fail the publish gate. Upstream changes that affect a downstream impl can be flagged before deploy.

## What knot does NOT enforce

- **What the impl does internally.** Block, score, cache, parallelize, cascade — all the impl's business.
- **Performance.** How the impl uses the data is opaque to knot.
- **Per-row vs pairwise.** ER's pairwise nature, merge's per-canonical-id nature, etc., are protocol-level; knot doesn't constrain how impls construct outputs from inputs.

## Impl config

Each impl binding has an associated **config** — pure-data behavior knobs attached to the binding, stored by knot, **runtime-editable** (no re-registration). Examples: thresholds, blocking-key slot lists, transitivity rule, mode (full vs incremental). Knot threads the config into the impl's runtime context as data fields (see "Context is pure data" in Decided), so impls read `ctx.config.threshold`, `ctx.config.blocking`, etc.

There is **no separate "strategy" entity** in knot. Descriptively, `config + impl = strategy`; mechanically, there's just an impl with associated config. A config edit changes the workflow's compile hash (config is part of the pinned-revision payload) but doesn't trigger re-registration.

Config can carry real Pydantic refs to spec entities — e.g., `blocking: [Movie.year, Movie.runtime]` (real `Slot` refs, per `spec-model.md` § "References"). For impact analysis, knot walks the binding → its config → referenced spec entities, alongside walking the impl's DataContexts → referenced spec entities. Both contribute to the impl's traceable footprint.

**Cross-check at compile.** Any spec entity referenced by config must be reachable through one of the impl's declared DataContexts. Otherwise a runtime config edit references data the impl doesn't fetch — silent breakage. Knot validates `config-referenced slots ⊆ DataContext-reachable slots` at compile and fails the compile on mismatch. This preserves runtime-edit ergonomics for the parameter values (thresholds, etc.) while catching cases where config and code drift.

## Cross-class traversals and the pinning lens

When a DataContext on a relation class traverses across a class boundary — e.g., `CreditResolver`'s context including `Credit.movie.identifiers` — knot's fetch resolver applies the pinning lens automatically per the relation class's compiled spec.

Per `cross-class-pinning.md`, a relation-class workflow's compiled spec carries `pinned_parent_runs: {Movie: hash_X, Person: hash_Y}`. When the resolver encounters `Credit.movie.*` in any DataContext, it reads Movie data **as of `hash_X`** rather than current Movie data. Same for `Credit.person.*` against `hash_Y`. The SCD2 `entity_bindings` and `canonical_id_lineage` layers (per `er-and-storage.md`) handle any in-between merges/splits transparently.

The impl writer never thinks about historical revisions. They write plain ontology expressions; knot's runtime serves the right data through the lens. Same posture as SCD2 bindings and corrections overlay being invisible to impls — the impl queries; knot serves.

For non-relation classes (Movie, Person, Identifier — classes whose slots don't reference other ontology classes), no pinning applies; current canonical state is read directly.

## Naming

`DataContext` is a placeholder. The shape may end up as `Stage(LensName)` per protocol (`ERReadContext`, `MergeReadContext`, etc.), or stay generic with a single `DataContext` and lens determined by the bound protocol — to be decided. The signaling mechanism (typed class attribute, real Pydantic refs, walked at registration) does not depend on the name.

## Decided

- **One impl class per binding.** Generic / parameterized impls (one Python class with binding-time-resolved DataContexts, reusable across multiple bindings) are **not** a knot mechanism. Code reuse is handled by ordinary Python primitives — class inheritance or library helpers. Each concrete impl class declares its DataContexts as class-level attributes; one binding per concrete class. Keeps the registration model simple: read attributes, walk, validate, store.

- **Context is pure data; no method-bearing seam object.** When an impl runs, it receives a pure-data `Context` object (Pydantic-shaped per protocol) that knot fills in per run. The context carries: the impl's config fields (e.g., `ctx.config.threshold`), binding info, and any **job-to-job state** that upstream stages emitted to be available downstream. No method machinery on the context. Anything postgres-control / control-plane the impl needs is a context field, not a separate declaration channel. **Implication: there is no "third channel" of declarations.** DataContexts are the only declarative input shape on the impl class; control-plane data and upstream-job state both flow through the runtime context as pure data.

  For impact analysis: spec-derived context fields (config that references slots, etc.) trace through the **binding + config graph** — both are themselves declared spec nodes with real refs. Job-to-job state ties to the workflow DAG, not to DataContext. No additional tracing surface needed.

- **DataContext resolution handoff: table / view name in the lake.** A DataContext is the *contract* (what the impl wants). When knot fulfills it for a run, knot materializes a **view (or temp table) in the lake at a stable name keyed to run + impl + DataContext**, and hands the impl the name. The impl reads from that name using its own engine (Spark, Trino, DuckDB, whatever) — no knot-side query execution; no SQL strings handed across the seam; no in-memory bytes (pyarrow / DataFrame) shipped from knot. Knot's responsibility is DDL (create view, drop view) and lifecycle (cleanup tied to run retention). Knot already has lake DDL capability via the materialization stage; no new infrastructure beyond that.

- **Impls are knot-hosted source, not externally-deployed packages.** Teams author impl Python code via the UI (or POST source to the API). Knot stores the source as the canonical impl artifact — there is no separate "Python service" to deploy or restart. Registration = submit source to knot; knot generates the SDK pinned to the current published spec hash, loads the source, introspects the class, walks DataContext references, validates against the spec, and saves. The next workflow run automatically picks up the new impl revision (compile hash changes accordingly). Iteration is fast: edit in browser → save → validation runs → impl is live for the next compile.

  At workflow runtime, knot ships the source to the orchestrator (Spark UDF / PyExecutor / equivalent, per the deployment's bound dispatcher). The orchestrator loads and runs it. Knot's process itself doesn't execute impl logic — it only inspects the source for registration validation (AST + class introspection, no `resolve()` call).

  **Trust posture:** impl authors are trusted. No sandboxing, no restricted Python, no curated import list. Authors have full Python access; they're members of the team running knot. This matches the trust posture for spec authoring (also full power) and for any other knot-hosted artifact. Hostile-author scenarios (multi-tenant SaaS) are out of scope; if a deployment ever needs them, sandboxing can be layered in via the dispatcher without changing the contract.

- **Impl identity is registration-bytes-content-addressed.** Per (stage, class) knot binds **one** impl — there is no "competing impls" scenario to disambiguate. The impl name is a tracking label; behaviorally, the impl IS the strategy (with its config). Re-registration with the same bytes produces the same impl revision (deduped); re-registration with different bytes produces a new revision. Workflow compile hashes pin the impl revision active at compile, so audit walk-back can resolve which version of the code produced any given output. Same content-addressing pattern as everything else in knot.

- **In-flight corrections do not overlay DataContext views.** User corrections (and additions) live as their own dedicated source — `_user_corrections`. The overlay machinery is purely a **gap-closer for the T1 → T2 window** (between user assertion and the next pipeline run picking it up): translator overlays postgres on top of lake at query time so consumer-facing reads see fresh values immediately. Pipeline-stage impls **never** see this overlay. At T2, the `_user_corrections` source's normalize step migrates pending corrections into `per_source_facts/<Class>/source=_user_corrections/` as regular lake data; from then on, pipeline impls see the corrections naturally as contributions from that source through normal DataContext fulfillment. Trust model treats `_user_corrections` as a high-trust source, so corrections naturally win during merge. Same pattern for user additions (a parallel "user additions" source). DataContext views are pure lake reads at the pinned moment — no overlay logic, no special-case branches, fully reproducible.

- **SDK pinned per registration.** Ontology modeling is a runtime activity — users edit the spec via UI/API while knot is running. Spec edits republish, get a new hash (per `compiled-workflow-hashing.md`), and the SDK that knot exposes (whether as a generated `knot.ontology` module with `Movie` / `Slot` classes, or via plain string references — implementation choice) regenerates with each new spec hash. **Impl registration is itself a runtime event**: a team registers a Python impl module against knot's API. At that moment knot generates the SDK at the current spec hash, makes it available for the impl's import, loads the impl, introspects its class attributes, and **captures the pinned spec hash with the registration**. That impl's view of the ontology is frozen at that hash; future spec edits don't silently shift it. On every spec republish, knot re-validates every registered impl's stored DataContext refs against the new spec — compatible registrations can advance their pin (or stay); incompatible registrations are flagged "needs new registration against the new spec," and the publish gate refuses any workflow that would use a broken impl. Module-vs-string is an implementation choice; the pinning principle is the same either way.

## Open

(none currently — surface new items as they emerge.)

## ConfigRef nodes in DataContext expressions

DataContext expressions may contain `ConfigRef` nodes alongside `SlotPath` and `RelationRef` nodes. A `ConfigRef` carries a typed path into the bound impl's Config class (`field_path: tuple[str, ...]`, `field_type: type`) and is emitted by the `Config.<field>` SDK namespace. It participates in the same single-dispatch expression-tree walk as all other nodes. Substitution at compile — replacing each `ConfigRef` with the corresponding literal or resolved spec entity ref from the bound Config snapshot — is the only point at which Config values enter the expression tree. `ConfigRef` does not read or mutate Config at registration time. Full mechanism: `staging/datacontext-config-binding.md`.

## Multi-class primary

`DataContext.primary` accepts `OntologyClass | list[OntologyClass] | DerivedSlot | list[DerivedSlot]`. A list-valued primary fans out at fulfill to N typed views — one per class or derived slot — handed to the impl as a dict (`{cls: view, ...}` or `{slot: edge_view, ...}`). Single-class primary is unchanged. See `staging/multi-class-datacontexts.md`.

## Cross-references

- `seam-contract-pattern.md` — the protocol+context shape this extends.
- `auto-generated-sdk.md` — the impl-facing SDK classes that DataContext expressions use; lens semantics across contexts.
- `er-and-storage.md` — ER impl shape, config, and the post-processing layer around impl edges.
- `compiled-workflow-hashing.md` — pinned-revision payload includes impl-declared inputs.
- `cross-class-pinning.md` — `pinned_parent_runs` for relation-class workflows.
- `spec-model.md` § "References" — real Pydantic object refs throughout.
