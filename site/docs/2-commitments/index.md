# Architectural commitments

Layer 1 established the problem: a single team building a multi-source knowledge graph needs deterministic audit walk-back through pinned spec revisions, content-addressed runs, ER lineage, and trust state. None of the existing tooling combinations covers that surface — and the combination is what's hard.

This layer is the answer: **17 architectural commitments** that, taken together, make knot what it is. Remove or invert any of them and the result is a different system that solves a different problem.

---

## What "commitment" means here

Each commitment is load-bearing. Three tests:

1. **Grounded in a Layer 1 hard problem.** Every commitment exists because something in [`1-why/the-hard-problems.md`](../1-why/the-hard-problems.md) demanded it. Nothing is gratuitous.
2. **Comparator-anchored.** For each one, the page names 1-3 systems that solve an adjacent problem and explicitly positions knot's choice. Reinvention is defended where it happens; adoption is preferred where the comparator already covers the slice.
3. **Honest about cost.** No commitment is free. Each page names what's awkward, what's lost, what the team gives up.

If a commitment doesn't survive those three tests, it is removed. The list below is what's left.

---

## How the 17 are grouped

The 17 commitments from [`design/core-design.md`](../../../design/core-design.md) split thematically into six pages. The grouping follows the argument, not the spec text — three commitments about how the spec is identified live together; three commitments about how data flows live together; etc.

| Page | Commitments | Theme |
|---|---|---|
| [1. Foundation](1-foundation.md) | 1, 2, 3, 17 | Compiler discipline, spec-as-data with real refs, content-addressed run identity, programmatic-first authoring. The "what kind of system is knot" page. |
| [2. The DI seam](2-the-di-seam.md) | 4, 5, 6 | The universal protocol + DataContexts + pure-data context + Config pattern. Knot-hosted impls. The impl IS the strategy. **Lens semantics for DataContexts** — the protocol determines which backing table fulfills the read. |
| [3. Data and resolution](3-data-and-resolution.md) | 7, 8, 13 | Multi-valued canonical facts with query-time trust resolution; cross-class pinning; in-flight corrections via dedicated source. |
| [4. Quality and publish](4-quality-and-publish.md) | 14, 15, 16 | Two-layer DQ; free-form materialization; loud failure modes. |
| [5. Pipeline and sources](5-pipeline-and-sources.md) | 9, 10, 11 | Source-layer contract; one unified expression tree; polymorphic-reference principle. |
| [6. Execution delegation](6-execution-delegation.md) | 12 | Knot delegates execution; four orthogonal QueryExecutor protocols. The seam through which knot's own machinery runs. |

---

## The reflective ontology compiler classification

Knot is a **reflective ontology compiler**. "Reflective" in the PL-theory sense: the compiler validates each spec edit against the running system that interprets the spec. The validator is the same runtime — current registered impls, current trust policy, current source watermarks, current bound configs all participate in the publish gate. CI cannot do this work because CI does not have the runtime state.

Three commitments are facets of this single property:

- **Commitment 3 (content-addressed runs)** — the run's identity *is* the canonical hash of the pinned spec + impls + configs. Audit walks the hash, not a sidecar.
- **Commitment 5 (knot-hosted impls)** — bound impls are submitted to knot, validated against the current spec hash at registration, and stored as the canonical artifact. The publish gate has the runtime state to validate against.
- **Commitment 16 (loud failures)** — the publish gate enforces structural validity atomically: Pydantic parse, reference resolution, DataContext cross-checks, impact preview. There is no "deploy-and-find-out" mode.

The classification is referenced at the relevant commitment pages; it is not its own page.

---

## How to read

The pages are independent — read in any order. Each page leads with its commitments' summaries, follows with rationale grounded in Layer 1, names comparators, lists honest tradeoffs, and ends with a collapsible deep-dive (Pydantic shapes, SQL patterns, mermaid diagrams) for the technical reader who wants to drill in. Cross-links to the canonical [`design/`](../../../design/) docs are at the bottom of each section.

Open tensions between commitments — places where two commitments pull against each other — are flagged at the bottom of the relevant page rather than hidden.
