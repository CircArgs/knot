# CLAUDE.md — knot project context

You are working on **knot**, a knowledge-graph + ontology platform for **one team** (no tenants). This dir holds the consolidated design. Read this file first, then read the design docs in the order below.

## Status: DESIGN. No code in this dir yet.

This dir is the canonical design location. The development environment (docker-compose, scripts, server stub, integration harness) lives at `/home/nick/code/knot/` if implementation begins. The design at `/home/nick/code/knot/design/` is currently kept in sync with this dir.

## Read first (in order)

1. **`design/goals.md`** — what knot is for; goals; non-goals; the "Who uses knot" section that defines the single-team / no-tenants posture
2. **`design/core-design.md`** — the **17 architectural commitments** that make knot knot. Each is load-bearing
3. **`design/_meta/design-thinking-patterns.md`** — the user's design-thinking patterns + 8 personas for design-review brainstorming. **Essential before any design work** — reflects how the user actually wants to confront problems

After those, read by topic:

- **Architectural anchors:** `design/knot-as-compiler.md`, `design/source-layer-contract.md`, `design/compiled-workflow-hashing.md`
- **Spec layer:** `design/staging/spec-model.md`, `design/staging/derivation-and-constraints.md`, `design/staging/sql-generation.md`, `design/staging/spec-versioning.md`, `design/staging/impact-analysis.md`
- **Pipeline + DI:** `design/staging/pipeline-stages.md`, `design/staging/seam-contract-pattern.md`, `design/staging/di-input-contract.md`, `design/staging/auto-generated-sdk.md`, `design/staging/query-executor.md`, `design/staging/dq-design.md`
- **Specific topics:** `design/staging/er-and-storage.md`, `design/staging/cross-class-pinning.md`, `design/reification-strategy.md`, `design/trust-and-merge.md`
- **Examples:** `design/staging/example-modeling-walkthrough.md`
- **Misc:** `design/staging/ontology-modeling.md`, `design/staging/artifacts.md`, `design/_handoff-2026-04-29.md` (historical handoff)

## Current state

- 17 architectural commitments locked in `core-design.md`.
- 6 substantive open design items + 1 cleanup pass:
  1. **Trust-CTE enforcement** (Type 1: mechanism choice) — convention rewrite vs type-enforced wrapper vs materialized view via `ViewManager`
  2. **Cross-class pinning retention contract** (Type 2: boundary) — parent-class data lifetime gated on referencing compile hashes
  3. **Spec graph target network boundary** (Type 2: boundary) — what's "in scope" for a compile hash
  4. **Browser-author + runtime-spec coordination** (Type 3: coordination) — impl compatibility under spec churn; multi-engineer team workflow
  5. **Spec edit concurrency** (Type 3: coordination) — concurrent draft edits, simultaneous publishes, racing triggers
  6. **DataContext parameterization by Config** (Type 1: mechanism) — for blocking patterns where DataContexts depend on config
  7. **Doc inconsistencies cleanup** (Type 4) — `OntologyClass` `frozen` contradiction, RUNTIME field gaps, stale "ER strategy as separate entity" wording

We are working through these top-down: top-level pass on each first, then dive layer by layer in order. **Currently mid-discussion on item 1 (trust-CTE enforcement).** See the recent conversation context.

## Posture

- **Single-team tool, no tenants.** The team that operates knot also owns every impl, every spec edit, and the lake/graph-store infrastructure. External users only enter at three narrow surfaces (read published outputs, query via translator, submit corrections via UI). See `goals.md` § "Who uses knot."
- **Trusted authors.** No sandboxing. Full Python power for impls. Defensive multi-tenant infrastructure does NOT apply unless explicitly chosen.
- **Knot is a compiler that delegates execution.** No internal SQL engine, no internal queue, no internal scheduler. Bound DI impls do all execution.

## Conventions (apply proactively)

The patterns doc (`design/_meta/design-thinking-patterns.md`) is authoritative. Highlights:

- **Real Pydantic types over discriminator strings.** Class-based discrimination + real enums. Strings are for data, not structural shape.
- **Don't build parallel meta-structures.** Walk the typed entity tree directly via single-dispatch.
- **Interrogate every named entity.** "Is this an actual thing or just a label for a bundle of existing things?" The "ER strategy" that turned out to be just `impl + config` is the canonical example.
- **No v0/v1/future-work framing.** Hard rule. Either commit to a design or explicitly mark it open with the question stated.
- **Baby-step + ELI5 pacing.** One self-contained step per turn, wait for confirmation, then add the next. Long structured walkthroughs are a smell.
- **Free-form generalization over over-specialized framings.** If the universal DI pattern (protocol + DataContexts + Config + pure-data ctx) covers a case, use it.
- **First ask if the problem is real.** Walk through a concrete scenario from the running example before designing. Hypothetical edge cases shouldn't drive design.
- **Trust language primitives.** Python inheritance, decorators, type system handle most reuse needs without new system-level abstractions.
- **Comparative anchoring.** For any architectural commitment, name 2-3 comparators (dbt, DJ, LinkML, SHACL, OWL, Splink, Atlas, RDF, Neo4j) and explicitly position. Are we reinventing? If yes, do we earn the cost?
- **Cull claims that don't earn their cost.** Don't preserve existing decisions out of inertia.

## Subagent personas for design review

When design review surfaces concerns, dispatch persona subagents that approach solutions the way the user would. Eight personas in `design/_meta/design-thinking-patterns.md`: Seam Sharpener, Type Maximalist, Trust Posture Interrogator, Reality Checker, Comparative Anchorer, Commitment Enforcer, User-Model Anchor, Pacing Critic. Each has a stance + sample subagent prompt opening + use-when guidance. Use 2-3 in parallel for the same concern.

## Auto-memory

The memory dir for this project is at `~/.claude/projects/-mnt-main-code-knot/memory/`. Key file: `feedback_design_thinking_style.md` — the same patterns as in `design/_meta/design-thinking-patterns.md`, condensed for proactive application.

## What NOT to do

- Don't write production code unless the user explicitly asks for a slice. Default mode is design.
- Don't reach for sandboxing, multi-tenant defenses, or formal contract enforcement — trust posture is single-team.
- Don't use "v0/v1/future work" framings as deferrals.
- Don't build parallel meta-structures when the typed entity tree carries the data.
- Don't introduce named entities without interrogating whether they earn their place.
- Don't propose long structured walkthroughs when a baby-step ELI5 will do.
