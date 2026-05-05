# UI and external-system integration notes

**Status:** captured-for-later. Not in scope for the current design phase. Anchored here so the directions don't get lost when implementation begins.

---

## UI direction — copy Marquez

The eventual knot UI should adopt **Marquez's layout and functionality** as the baseline reference.

Marquez ([github.com/MarquezProject/marquez](https://github.com/MarquezProject/marquez)) is the OpenLineage reference UI. What's worth copying:

- **Lineage graph view** — interactive node + edge graph for jobs and datasets with zoom / pan / filter
- **Job run history** — chronological view of pipeline runs with status, duration, links to logs
- **Dataset view** — schema, recent runs that produced it, recent runs that consumed it, freshness
- **Search across the graph** — by name, tag, owner
- **Sidebar / breadcrumb navigation** structure that surfaces parent / child relationships

For knot, the equivalent surfaces are:
- The **ontology graph** (classes, slots, types, constraints, relationships) — visual + searchable
- The **pipeline-run graph** (compiled workflows, runs, status, artifacts produced) — same shape as Marquez's job/dataset graph
- The **canonical-entity view** — for any canonical_id, show the contributing source rows, ER decision, current trust state, in-flight corrections, audit chain
- The **spec-edit workflow** — draft / publish gate / impact preview / publish history

Knot's UI is for the *operating team*, not external consumers. External consumers hit materialized targets directly (Neo4j, Iceberg, etc.) per the single-team posture in `goals.md`.

---

## Netflix integration — port-time

When knot is brought into Netflix, two things change:

1. **UI components swap to Netflix-internal.** Whatever component library Netflix uses internally replaces the open-source UI components (Tailwind / Radix / etc.) chosen for the OSS-friendly initial build. The structure (Marquez-inspired layout) and behavior remain the same; only the styling primitive layer swaps.

2. **Auth becomes a bound DI impl.** Same DI pattern as everything else — knot defines an `AuthProtocol` (or similar), Netflix-internal binding implements it against Netflix's auth backend. Knot core does not know about specific auth providers; the impl owns the auth backend specifics. This matches the "knot delegates execution; impls own outside-the-seam infrastructure" posture (`core-design.md` commitments 1, 5, 12).

The auth seam isn't a knot architectural commitment yet because the design hasn't surfaced it as a load-bearing concern. When implementation begins, an `AuthProtocol` will be defined alongside the existing protocols. Trust posture (single team, no tenants) means the auth surface is narrow: who can author the spec, who can register impls, who can edit configs, who can submit corrections, who can read published outputs. Each is a permission check against a binding-defined identity.

---

## Why this is captured but deferred

The design phase is establishing the architectural primitives. UI and Netflix-internal integration are downstream of those primitives — the primitives should not be shaped by UI assumptions or Netflix-internal-component assumptions. Capturing the directions here so they're not re-derived later, but no design work happens against them now.

When implementation begins, this doc seeds the UI and auth design conversations.
