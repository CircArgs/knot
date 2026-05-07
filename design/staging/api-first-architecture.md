# API-first architecture — post-pivot

**Status:** authoritative for the new direction. Supersedes parts of
`core-design.md` (notably commitments 1, 4, 5, 9, 12, 15) which were
written for the pre-pivot lake-side / DI model.

**Date locked:** 2026-05-07.

---

## The pivot

Everything happens through the API. Postgres is the source of truth for
both spec AND data. There are no bound DI impls executing externally —
knot owns ingestion through publication through query.

**What dies** (vs. the original 17 commitments):
- "Knot is a compiler that delegates" — knot IS the runtime.
- DI seam (Protocol + DataContexts + Config + ctx) — no impls to inject.
- QueryExecutor family — postgres IS the engine.
- Source-layer contract (lake landing) — sources push to API.
- Knot-hosted browser-authored impl source — irrelevant; no impls.
- Toy-maestro / orchestrator submodule — deleted.

**What survives:**
- Spec-as-Pydantic with real refs (commitment 2).
- Content-addressing for audit (commitment 3).
- Multi-valued canonical with query-time trust resolution (commitment 7).
- Audit walk-back (goal 2).
- One unified expression tree (commitment 10).
- Failure modes are loud (commitment 16).
- Programmatic-first authoring; no YAML (commitment 17).

**Trust posture flips:**
- Spec authorship trust posture (single-team trusted authors) was about
  IMPLs — those are gone. The API surface itself is **hostile-by-default**:
  every request body is validated; identifiers from the spec are quoted
  via `psycopg.sql.Identifier`; types come from a fixed whitelist.

---

## Two routers

- `/spec/*` — **modelling**: draft lifecycle, mutations
  (types/slots/classes/sources), publish gate, published reads.
- `/graph/*` — **data plane**: ingest, reads, queries, corrections (planned).

Sources are spec entities (declared via `/spec/drafts/{id}/sources` in the
draft → publish flow). The `/graph/ingest/{source_name}` URL is rows-level
data ingest, not source CRUD.

---

## Storage shape

Two postgres schemas, one database:

- `public` — control plane: `spec_revisions` today; future `trust_config`,
  metadata, etc.
- `knot_data` — data plane: per-class fact tables created/altered by
  `db.migration` at publish time.

Per `OntologyClass C` → `knot_data.<lowercase_name>`:

| Column | Type | Source |
|---|---|---|
| `_canonical_id` | TEXT NOT NULL | initially identifier-slot value; ER refines |
| `_source` | TEXT NOT NULL | source name (FK by name to published spec) |
| `_source_row_id` | TEXT NOT NULL | string-coerced identifier-slot value |
| `_ingest_at` | TIMESTAMPTZ NOT NULL DEFAULT now() | DB default |
| `_spec_revision` | INTEGER NOT NULL REFERENCES public.spec_revisions(revision) | revision at ingest |
| `<slot_name>` × N | per slot.range, multivalued → `T[]` | one column per stored slot |

- PK `(_source, _source_row_id)`.
- Multi-valued canonical (commitment 7) lives as **multiple rows per
  `_canonical_id`**, one per source — not multi-value-per-cell.
- Derived slots are skipped (query-time projections, not stored).

---

## Centralization rules (enforced)

- **All postgres I/O lives under `knot/db/`.** No `import psycopg`
  outside.
- **All SQL strings live under `knot/db/`.** No SQL keywords outside.
- **All identifier interpolation** uses `psycopg.sql.Identifier`; types
  come from a fixed whitelist.
- `knot/ontology/` — pure: typed entities + canonical hash + Pydantic row
  model builder. Zero SQL, zero psycopg.
- `knot/api/` — thin: request shape + validation + orchestration; calls
  into `db.*` for persistence.
- `knot/config.py` — single source of truth for env-driven config
  (`get_dsn()` today; future env knobs land here).

```
src/knot/
├── config.py
├── api/         (main, spec, graph)
├── ontology/    (metaschema, canonical, row_models)
└── db/          (connect, apply_schema, control_schema.sql,
                  spec_store, migration, graph_store, sql_gen)
```

---

## Validation posture

Hostile-by-default at the API surface:

- **Fixed-shape requests:** Pydantic at the route layer (FastAPI native).
- **Per-source row payloads:** dynamically-built Pydantic models from the
  spec (`ontology.row_models.build_row_model(source)`) — `extra="forbid"`,
  types from `slot.range`, `pattern` / `ge` / `le` / `Literal[...]` /
  `list[T]` enforced. 422 on failure with FastAPI-shaped error detail
  (`loc=("body","rows",i,...)`).

---

## Migration & revisions

`knot` manages migrations on per-class tables. The previously-published
spec **IS** the previously-applied schema state by construction (single
source of truth: `spec_revisions`), so the diff input is always two
Pydantic typed trees, not "spec vs unknown DB."

- **Publish flow:** spec-graph gate → atomic flag flip → migration apply,
  all in one transaction.
- **Today:** first-publish CREATE TABLE IF NOT EXISTS only.
- **Next slice:** diff visitor walks `(prev_spec, candidate_spec)` via
  single-dispatch over the typed entity tree, emits typed Change events
  (AddSlot, DropSlot, AddClass, DropClass, RenameSlot, ChangeRange,
  ChangeMultivalued, ChangeRequired), then emits DDL per Change.
- **ALTER paths must be additive-where-possible** to preserve historical
  revisions' projection views (see consumer stability below): rename =
  new column + backfill + view alias on old name; drop = view-aliased
  NULL on old revision's projection; type widen = cast.

---

## Consumer stability across spec changes

The presupposition: spec churn must not break downstream consumption.

- **Revision pinning.** Consumers pin to a specific `spec_revision` like
  a git commit hash: `/graph/...?as_of=N` for explicit; default is
  current with the revision returned in an ETag/header so the client
  always knows what it got.
- **Per-revision projection.** Each row's `_spec_revision` lets us
  project the same row through any historical revision's view, as long
  as ALTER paths preserved old shapes.
- **Breaking changes are gated.** Truly breaking changes (drop class /
  drop required slot / type-narrow) become a publish-gate decision: the
  gate enumerates affected pinned revisions and surfaces "consumers
  pinned to revision K won't have a valid projection" before publishing.

---

## Resolution (multi-source disagreement)

Commitment 7 survives: multi-valued canonical, query-time trust resolution.

- `Slot.resolution_policy` already declares the algorithm (ARGMAX_TRUST,
  MEDIAN_NUMERIC, UNIQUE_OR_FAIL, MODE, WEIGHTED_VOTE, LATEST_WATERMARK).
- **New persistence:** per-source trust scores + per-slot policy
  overrides in `public.trust_config` (or similar; table shape TBD) —
  CRUD on `/spec/*` (config is spec-adjacent metadata) or its own
  router.
- **Query-time view:** `db/resolve.py` (planned) builds the per-class
  trust-resolved CTE from the current spec + trust config; the API
  endpoint orchestrates and returns rows.
- **"Custom resolver"** under API-only collapses to "named built-in
  algorithm + config" — no DI impl surface, no plugin contract; Pattern
  5 (universal pattern) shaped as config-driven.

---

## GraphQL (planned, deferred)

- `strawberry.experimental.pydantic` converts Pydantic models to GraphQL
  types directly — `ontology/row_models.py` becomes the unified source of
  truth for **both** REST validation and GraphQL input types.
- Output types (`type Movie { imdb_id: ID!, ... }`) come from the same
  ontology→types projection extended with Strawberry decorators.
- **Defer until read endpoints settle.** Reads/queries are the natural
  GraphQL fit; bulk ingest stays REST (GraphQL mutations have per-row
  overhead). Wiring strawberry before the read shape is decided is
  premature.

---

## What knot is NOT (updated)

- Not an orchestrator (not even a toy one — toy_maestro is deleted).
- Not a SQL engine — postgres IS the engine; knot only emits SQL.
- Not multi-tenant SaaS — one team, one deployment, one database.
- Not authoring Python impls — there are no impls.
- Not LinkML / SHACL / OWL — borrows vocabulary; not compatible.
