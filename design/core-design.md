# Knot — core design

The architectural commitments that make knot **knot**, not implementation choices that could change without changing what knot is. Anything below is load-bearing; remove or invert any of it and knot becomes a different system.

**Classification: a reflective ontology compiler.** Spec edits validate against the running system that interprets them. The validator and the interpreter are the same runtime. CI is not in this loop because CI does not have the runtime state (registered impls, trust policy, source watermarks). See `goals.md` § "What knot is."

Read `goals.md` first.

---

## 1. Knot is a compiler.

Knot reads a typed Pydantic spec, emits a content-addressed `WorkflowSpec`, and dispatches it to an external orchestrator. **Knot does not execute work.** It has no internal queue, no internal scheduler, no internal SQL engine, no internal HTTP loop that touches data. The boundary is the bound DI interfaces; everything that touches data plane crosses through one.

Implication: dropping the compile/execute split breaks knot. Adding a "knot worker" that picks up jobs would violate this. Adding inline SQL execution in API handlers would violate this.

## 2. Spec-as-data, with real Pydantic object references in-memory.

The spec is Pydantic models. Between metaschema entities (classes, slots, types, constraints, derivation expressions, configs), references are **real Python object references** — not name-string lookups. `Slot.range` holds an `OntologyClass | TypeDefinition` directly; `OntologyClass.slots` holds `list[Slot]` of real objects.

Where strings live: the **persistence boundary**. JSONB writes flatten refs to names; reads rehydrate. The API / UI / postgres layer owns this round-trip.

Implication: codegen, impact analysis, and SDK use natural Python attribute access (`slot.range.name`). No `spec.slots[name]` lookup detour. Type-checker friendly; statically introspectable.

## 3. Content-addressed compile hashes are run identity.

Every workflow trigger is a fresh compile. The compiler reads the *current* revisions of every spec node in the compiled workflow's dependency graph, emits a `WorkflowSpec` with revision IDs baked in, hashes the canonical form (RFC 8785 JCS), and stores it. **The hash is the run's identity.**

Reruns reference the hash, not the trigger. Identical input → identical hash → deduplicated storage. Spec edits between triggers produce a different hash naturally.

Implication: every contributing spec node, every bound impl revision, every config revision is *pinned* into the compile. Audit walk-back is mechanical.

Per-stage cache keys extend this to skip-at-runtime: at compile, knot computes a content-addressed cache key per stage from its full input set (spec revisions, impl source hash, config revision, source watermarks, pinned parent runs). The orchestrator skips stages whose cache key matches a prior successful run and propagates "unchanged" forward through the toposort. knot emits the skip plan in the dispatched `WorkflowSpec`; the orchestrator acts on it. See `staging/incremental-execution.md`.

## 4. Universal DI seam: protocol + DataContexts + pure-data context + impl Config.

Every team-bound stage follows one pattern:

- **Protocol** — what the impl must implement (e.g., `ERProtocol.resolve(ctx, **datacontexts) -> list[Edge]`).
- **DataContexts** — typed class-level attributes on the impl declaring what data it reads. Pydantic-typed; built from SDK expression trees over real ontology refs. Detected by typing, not naming. Multiple per impl. Walked at registration for impact analysis; materialized at runtime via the bound `Materializer`.
- **Context** — a pure-data Pydantic object knot fills in per run: the impl's config, binding info, upstream-job state. **No methods.** Reads as `ctx.config.threshold`, etc.
- **Config** — an impl-defined Pydantic class declaring runtime-editable knobs (thresholds, mode flags, slot lists). Stored in postgres-control; runtime-editable via API; threaded into ctx as data.
- Each Protocol carries a `disagreement_stance` (`DISAGREEMENT_AWARE` or `RESOLVED`) that drives which SDK slot type the type generator emits — `MultiValued[T]` or `Resolved[T]` — and whether knot attaches a trust-resolution CTE. See `staging/multi-valued-semantics.md`.
- DataContext expressions may contain `Config.<field>` symbolic refs — typed `ConfigRef` nodes in the expression tree, substituted from the bound impl's Config snapshot at compile time. See `staging/datacontext-config-binding.md`.
- `DataContext.primary` accepts `OntologyClass | list[OntologyClass] | DerivedSlot | list[DerivedSlot]`, fanning out to N typed views at fulfill (one per class or derived slot). See `staging/multi-class-datacontexts.md`.

Implication: any spec entity referenced by config or DataContext is statically traceable. Impact analysis walks every binding's stored declarations alongside spec edges. There is no special "third channel"; control-plane data flows through ctx as fields.

## 5. Knot-hosted impl source. Browser-authored. Trusted authors. Single-team.

Bound impls are Python source the team submits to knot via API. Knot stores the source as the canonical artifact, generates the SDK pinned to the current spec hash, loads + introspects the class at registration, validates DataContext refs against the spec, and saves with a content hash of the bytes.

There is **no separate Python service to deploy.** Iteration is browser → save → validate → live for next compile. At workflow runtime, knot ships the source to the orchestrator's runner.

**Trust posture: knot has no tenants.** The team that operates knot also owns every impl, every spec edit, and everything outside knot's seams that impls touch (lake infra, graph stores, model files, secrets). Spec / impl authors are members of the operating team — not external contributors. **No sandbox. No restricted Python. No multi-tenant defenses.** External users only enter at three narrow surfaces (per `goals.md` § "Who uses knot"): reading published outputs, querying via the translator, submitting corrections through the UI — none of which involve writing Python or editing the spec.

Implication: hostile-author scenarios are out of scope. Comparative arguments framed as "every team would reinvent X" don't apply (there's no every-team). Iteration cycles are dominated by spec edits and config edits, not Python deploys.

## 6. The impl IS the strategy. There is no separate "strategy spec" entity.

For ER (or any per-class stage), there's exactly one bound impl per (stage, class). The impl plus its postgres-control config IS what humans call "the strategy." There is **no separate strategy node**, no parallel registry. The impl name is a tracking label with no semantic role.

Implication: switching strategies = swapping impls. Re-registering an impl with new bytes produces a new content hash and a new compile hash. Strategy iteration via config edit (runtime); strategy iteration via algorithm change requires re-registration.

## 7. Multi-valued canonical facts. Trust resolution is query-time. SDK type follows protocol stance.

At the canonical layer every property is **implicitly multi-valued** — one contribution per source. `Movie.year` for a canonical entity is a set: `[{source, value, asserted_at}, ...]`. The merge stage does **not** write a "winner" column. Default-value selection happens at query time via a trust-resolution CTE knot rewrites pre-execution, using the `resolution_policy` declared on each `Slot` (default: `ARGMAX_TRUST`).

Which SDK type a slot reference receives depends on the protocol's `disagreement_stance`. Under `RESOLVED` protocols (Materializer, Translator, ConstraintEvaluator, DerivationEvaluator), slots are `Resolved[T]` — comparison operators work, bare `Movie.year > 1900` compiles, knot attaches the trust-resolution CTE. Under `DISAGREEMENT_AWARE` protocols (ERProtocol, DqRunner), slots are `MultiValued[T]` — bare comparison is a type error; callers spell their reduction explicitly (`from_source(...)`, `all_()`, `winner()`). Escape hatches on `Resolved[T]` (`from_source`, `all_`, `contributions`) remain available. Full protocol-by-protocol assignment and the `ResolutionPolicy` enum are in `staging/multi-valued-semantics.md`.

Implication: trust adjustments don't require re-running merge. Lineage lives in the data; no separate `AuditChain` table. Audit walk-back is queryable as data. ER and DQ cannot silently compare trust-winners where they should compare source bags.

## 8. Cross-class pinning. Relation classes pin parent run hashes at compile.

A class whose slots' ranges reference other classes (a "relation class") pins its parents' specific run hashes into its compiled spec. Reads parent canonical_ids "as of" those runs, not "current." Knot's runtime applies the temporal lens automatically; the impl writes plain ontology expressions.

Implication: audit walk-back crosses class boundaries deterministically. No "what state were Movie's canonical_ids in when Credit ran?" is ever ambiguous. Strategic ER dependencies (declared in config) extend this to non-structural cases.

## 9. Source-layer contract. Knot starts at normalize.

Sources are team-owned lake declarations: a URI, a typed Pydantic spec for the columns, an identifier slot, optional watermark, a mapping to one or more ontology classes (homogeneous or discriminator-routed with explicit wildcard drop). **Team owns getting data into the location**; knot owns reading from there onward.

Implication: no `SourceReader` interface. No "knot pulls from external systems." No knot-managed credentials for ingestion. The compiled workflow's first task per source is `normalize:<source>`.

## 10. One unified expression tree.

A single Pydantic expression tree (`RelationRef`, `FilteredRelation`, `RelationProject`, `RelationCount`, `RelationAggregate`, `RelationAny`, `RelationAll`, `RelationFirst`, `ScalarDerivation`, `FormatDerivation`, plus `Compare` / `BoolOp` / `SlotPath` / `Literal_` for predicates) powers:

- Slot derivations
- Constraint bodies (cross-row, cross-class)
- ER signal slots (in config)
- DataContext bodies on bound impls
- Translator backward-chain queries

**One canonical form. One SQL generator. One impact-analysis visitor.** Real Pydantic refs throughout. Enums (not Literal strings) for same-shape variants. The terminator type determines the SQL pattern; no per-kind wrapper classes. Derivation expression refs (slot.range chains inside derivation rules) participate in compile-network traversal the same way structural `slot.range` edges do. See `staging/impact-analysis.md`.

## 11. Polymorphic-reference principle.

A polymorphic / discriminator-style class (e.g., `Identifier` with `entity_class` + `entity_src_key`) is invisible to knot's static dependency graph until a *consumer* explicitly declares the dependency. The polymorphic class itself stays generic; consumers (ER configs, DataContexts) name the specific class connections.

Principle: **to use a polymorphic class as a dependency, the consumer must fully specify the relationship.** This makes impact analysis tractable without runtime data inspection.

## 12. Knot delegates execution. Four orthogonal QueryExecutor protocols.

Lake-side execution goes through bound impls of `QueryReader` (sync SELECT → Arrow), `Materializer` (async INSERT INTO target SELECT → handle + status), `Introspector` (column metadata), `ViewManager` (DDL). Typed Pydantic inputs throughout. Errors raise. One impl per protocol per deployment; multi-backend handled internally if needed. No HTTP boundary.

Implication: knot core is dialect-agnostic; bound impls translate. Knot's own machinery (built-in DQ checks, structural validation, materialized views fulfilling DataContexts) runs through the same QueryExecutor. Nothing reinvented.

## 13. In-flight corrections via dedicated source.

User corrections (and additions) live in postgres-control briefly, then migrate into the lake at the next pipeline run via a dedicated `_user_corrections` source. From migration onward they're regular lake data treated as a high-trust source by the trust model. The translator overlays postgres on top of lake at query time **only** for consumer-facing reads — closing the T1→T2 gap so consumers see fresh values immediately. **Pipeline impls never see this overlay.** DataContext views are pure lake reads at the pinned moment. Reproducible by construction.

## 14. Two-layer DQ: built-in bundle + custom DqRunner.

Knot ships a configurable bundle of common DQ check types (freshness, drift, cluster-size outliers, cross-source agreement, cycle detection, null-rate trends, source-coverage drop). Each is a typed Pydantic check definition with sensible defaults; runtime-editable per deployment / per class. Knot generates SQL; the bound `QueryReader` runs it.

Custom domain-specific or ML-based checks bind via the `DqRunner` protocol — same DI pattern, declared DataContexts, impl-defined Config. Failures from both surface in a uniform `(rule_id, class_name, slot_name, offending_pk, detail, severity)` shape. Built-ins are opt-out per check; custom impls compose alongside or replace.

## 15. Free-form materialization.

A materialization is just a bound DI impl. Same protocol + DataContext + Config pattern. The impl declares whatever data it needs (single class, multi-class joined, denormalized, filtered), publishes to whatever target however it wants (Neo4j via S3 + LOAD CSV; Iceberg; vector store; CSV exports). One impl can target multiple downstream systems. **Not per-class.**

Knot's role: provide the data through QueryExecutor; let the impl publish.

## 16. Failure modes are loud.

- Pydantic `extra="forbid"` everywhere; unknown fields raise at parse.
- Reference resolution at parse time; dangling refs raise.
- DataContext registration validates against spec; broken refs fail registration.
- Config edits cross-checked against the impl's DataContext-reachable slots; mismatches fail compile.
- ER post-processing: conflicts and splits surface for review by default; auto-merge / auto-split is opt-in per impl.
- Validation queries return offender rows; built-in DQ checks emit failures in the uniform shape; severity gates publish.
- QueryExecutor errors raise typed exceptions, never swallow.

## 17. Programmatic-first authoring; no YAML; no fork of LinkML/SHACL/OWL.

Spec is authored via UI/API as Pydantic models. The vocabulary borrows from LinkML (class / slot / range / mixin / `permissible_values` / `is_a` / `multivalued`) because it's good ontology terminology — knot is **not** LinkML-compatible, has no LinkML library dependency, no YAML import/export, no SHACL escape hatch, no OWL DL reasoner. Cross-row and cross-class constraints use the same expression-tree machinery as derivations; knot's SQL generator emits validation queries.

JSON Schema export falls out of Pydantic for free.

---

## What follows from these commitments

- The canonical seven-or-eight "interfaces" framing in earlier docs is loose: it's a naming convention, not the architectural primitive. The primitive is the universal DI seam (item 4).
- Reproducibility comes from content-addressing (item 3) + cross-class pinning (item 8) + SCD2 bindings + canonical-id lineage. Replay of a hash is deterministic modulo the impl's own determinism.
- Impact analysis is single-dispatch visitor functions over the typed entity tree. There is no separate "meta-graph" object; the typed graph IS the graph (item 2 + 4).
- Most production iteration is config edits (runtime, no redeploy); algorithmic changes require re-registration (which is itself a runtime API call). Iteration friction is small (item 5).
- Knot is engineered for the **single-team data-platform use case**: one team operates the deployment, owns every impl, owns the ontology spec, and owns the infrastructure outside knot's seams. Trusted authors. Full Python power available. **No tenants.** External users only enter at three narrow surfaces (read published outputs, query via translator, submit corrections via UI) per `goals.md` § "Who uses knot."
