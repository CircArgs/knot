---
title: Knot architecture v1
status: note
tags: [architecture, decisions, knot-v1, canonical]
project: knot
created_at: 2026-04-27T23:30:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# Knot architecture v1

This is the canonical architecture for knot v1. It supersedes the
original AI-drafted design spec and collapses the prior decision-task
backlog into a single coherent picture. Everything here is settled
unless explicitly listed under "Open design items."

## What knot is

Knot is the control plane for a knowledge graph + ontology platform.
It owns the canonical ontology, the lineage of every fact, the
materialization pipeline that produces graph data in the lake, and the
APIs that consumers (humans, services, analytics) use to read or
correct it. It does not execute jobs, host the graph, or run analytic
queries — those happen elsewhere. Knot specifies what to do, validates
specs before dispatch, and tracks what happened.

## The architectural through-line

**Every variability axis is a protocol + plugin.** Where deployments
differ — orchestrator, ER strategy, identity provider, authorization,
query language — knot defines a protocol contract and ships default
in-tree implementations. Production deployments inject Netflix-internal
implementations (Maestro, internal IDP, etc.) without touching knot
core.

This collapses what would otherwise be a long list of "pick one of N"
decisions into a single uniform plug pattern lifted from DataJunction,
applied throughout instead of in isolated places.

## What knot owns (not pluggable)

These are knot's actual responsibilities — implemented in the core,
not delegated to plugins:

1. **Ontology authoring + versioning.** LinkML schemas exposed via
   FastAPI. Full revisions kept; semver-style version pointer.
   Schema-level data quality (types, cardinality, required fields)
   comes for free via LinkML → SHACL.
2. **Lineage** at two granularities:
   - **Table-level** (postgres metagraph): which ontology node depends
     on which. Stored in `node_dependencies`. The node graph IS the
     lineage at this granularity (DJ pattern).
   - **Per-property / per-relation** (lake provenance columns): every
     materialized fact carries its source(s), so the resolved value
     and its alternatives are always recoverable.
3. **Materialization compiler.** Ontology + spec + target →
   target-specific output. Knot does not parse SQL by hand — sqlglot's
   AST is the only AST when SQL is the output format. The target's
   `compile()` method governs the output shape: SQL + Spark job spec
   for `LakeMaterializationTarget`; Cypher LOAD for
   `Neo4jMaterializationTarget`; vectorize-and-upsert for vector
   targets, etc. The orchestrator executes the job spec; the spec
   format is target-specific.
4. **Query translator.** GraphQL / Cypher / SPARQL → SQL. The endpoint
   returns SQL, not query results — knot is a schema-aware translator
   service for analytics consumers, not a federated query executor.
   The consumer runs the returned SQL against whatever warehouse hits
   the lake. The translator API picks the target based on query shape
   (graph traversal → graph store target if registered; analytics →
   lake; similarity → vector store). Single-target deployments route
   everything to the lone registered target. Multi-target deployments
   use the target's `serves(query)` predicate to pick.
5. **Static pre-flight validator.** No spec reaches the orchestrator
   without passing here. Knot has the only end-to-end view (ontology +
   lineage + plugins + auth) so this is the only place validation can
   run cheaply. Specifics in the validator section below.
6. **Run tracking.** `runs` and `run_events` tables fed by polling
   `Orchestrator.status(run_id)`. Fixes the DJ gap where
   `AvailabilityState.is_available()` is a stub.
7. **Sources registry.** Every upstream input that ER consumes is a
   registered source with a current `trust_score`.
8. **Corrections subsystem.** UI-submitted property fixes, relation
   edits, entity merges. Stored in postgres. Each correction records
   the user, timestamp, target, payload, and which source value it
   overrode.
9. **Trust scoring algorithm.** Knot-owned, postgres-resident.
   Sources lose trust when corrections contradict them; the
   corrections-source has fixed/configurable high trust.
10. **Corrections-as-source pipeline.** User corrections are packaged
    as a coherent high-trust source that ER consumes alongside upstream
    sources. Keeps ER pure ("merge these N sources by trust score") and
    closes the human-in-the-loop feedback loop.
11. **The protocols themselves** (the catalogue below).
12. **The API surface** (read/write ontology, submit specs, query
    runs, query lineage, translate analytics queries, submit
    corrections, query trust scores, manage sources).

## Pipeline stages (sources → graph)

Every knot pipeline is some subset of these six canonical stages in
DAG form. Substages within a stage are implementation details, not
pipeline-level concerns.

1. **Ingest** — pull a source's raw data, land it in lake staging.
   Source-specific (one Ingest stage per registered source). Corrections
   flow in here as a regular source.
   - **In:** external system. **Out:** staging tables (raw shape).
2. **Normalize** — map raw source fields to ontology types. SHACL
   validation runs here (free via LinkML). Per-source; no cross-source
   merging yet.
   - **In:** staging tables + LinkML schema. **Out:** ontology-shaped
     per-source records.
3. **Resolve** — entity resolution. Block + match + cluster as one
   logical stage; substages are `ERStrategy` internals.
   - **In:** all normalized per-source records for an ontology node.
     **Out:** entity clusters.
4. **Merge** — trust-aware reconciliation. For each cluster, pick
   winning property/relation values using `trust_score`. Emit per-source
   layer + resolved layer. Provenance preserved on every fact.
   - **In:** clusters + trust scores. **Out:** resolved entities/
     relations with full provenance.
5. **Validate** — post-resolution data quality (SQL checks beyond
   SHACL — statistical, freshness, cross-entity invariants). Hard checks
   fail the pipeline; soft checks warn.
   - **In:** resolved layer. **Out:** pass/fail + per-check report.
6. **Publish** — atomic write of per-source + resolved layers to the
   lake, plus version pointer flip in postgres so consumers see the new
   revision.
   - **In:** validated resolved layer. **Out:** lake tables + new
     revision pointer.

The stage names also become the run-tracking unit: each stage is a
separately-tracked run with its own status, watermark, and event log.

## Internal database access

Knot's control-plane DB is postgres (already in compose). Access stack:

| Layer | Tool |
|---|---|
| Driver | **asyncpg** directly |
| Connection pool | `asyncpg.create_pool(...)` |
| Queries | Plain SQL strings: `await pool.fetch(sql, *params)` |
| Result mapping | Pydantic models via `MyModel(**dict(row))` |
| Migrations | **Plain numbered SQL files** + small Python runner |

**No SQLAlchemy. No alembic.** Knot is SQL-native end-to-end: the
materialization compiler emits SQL, the static validator parses SQL,
analytics consumers receive SQL, and internal queries are written in
SQL. There is no benefit to hiding control-plane queries behind an
ORM or expression language layer when SQL is already the contract.

**Migration layout:**
```
server/migrations/
  001_ontology_nodes.sql
  002_ontology_node_revisions.sql
  003_node_dependencies.sql
  ...
```

The runner (~50 lines, in `server/db/migrate.py`):
- Maintains a `schema_migrations(version int primary key, applied_at
  timestamptz)` table.
- On `knot migrate`, walks the directory, applies any file whose
  integer prefix is higher than `max(version)`, in order, each in a
  transaction.
- **Forward-only.** No `down()` files. To revert, write a new
  migration that undoes. (Pre-release: change schema freely; once
  shipped, forward-only matches the modern norm.)
- File naming `NNN_short_slug.sql` — integer prefix sorts the order.

**Implications:**
- Schema diffs are reviewed as plain SQL. No alembic-generated noise.
- Data migrations (when a schema change requires reshuffling existing
  rows) live in the same SQL file as the schema change.
- The runner is the only place that reads files in this directory; the
  rest of knot reads from the running DB.
- Tests use the same migrations against an ephemeral postgres (the
  same docker-compose service or a tmp instance).

## What knot does NOT own (out of scope)

These are explicitly delegated. Pre-release policy means no aliases,
shims, or "we might add this later" fallbacks — they are not knot's
problem.

- **SQL parsing / AST framework.** Use sqlglot. Hand-rolled ANTLR-style
  parsers are 3500+ lines of work knot does not do.
- **Lake writing / physical storage adapter.** The orchestrator's Spark
  job writes to the lake. Knot emits SQL + job metadata; what the job
  does inside execution is the orchestrator's domain.
- **Graph database integration.** Whatever consumes the lake-stored
  graph (Neptune / Cassandra / JanusGraph / nothing) is downstream.
  Knot's responsibility ends at the materialized lake dataset.
- **Analytic query execution.** The translator API returns SQL.
  Consumers run it themselves. Knot is not a query gateway in the
  result-streaming sense.
- **OpenLineage event emission as the lineage model.** Knot's
  per-fact provenance is data-resident metadata written into the
  lake rows themselves, structurally finer than OpenLineage's
  per-dataset/per-run grain. If a downstream catalog (DataHub /
  Marquez / Atlan) needs OpenLineage, expose it via a thin export
  adapter — knot does not adopt OpenLineage as the internal lineage
  model.
- **Multi-tenancy.** Single graph. One KG domain.
- **Job execution, retries, scheduling, resource allocation.**
  Orchestrator's job.
- **SQLAlchemy / ORM / alembic.** Knot uses asyncpg directly + plain
  SQL files for migrations. SQL is the contract end-to-end; no ORM
  layer.

## The protocol catalogue

Six protocol families. Each has default in-tree implementations for
prototype/dev work; production swaps in deployer-specific plugins via
the DJ-style ABC + env var + `__subclasses__()` pattern.

1. **`Orchestrator`** — `submit(spec) -> run_id`,
   `status(run_id) -> RunStatus`, `cancel(run_id)`. Default impl: a
   simple postgres-backed job queue. Production: Maestro (or whatever
   the deployer plugs in).
2. **`ERStrategy`** — `match(records, sources, trust_scores) -> matches`,
   `merge(matches) -> entities`, `emit() -> dataset`. Default impls:
   exact-match, deterministic-block-then-match, others as ER research
   surfaces them. ER is fundamentally a pluggable algorithm space.
3. **`IdentityProvider`** — `whoami(request) -> Principal`. Avoids the
   DJ asymmetry where authn is hardcoded but authz has a real ABC.
4. **`AuthorizationService`** — `can(principal, action, entity) -> bool`.
   Defaults to allow-all for dev; production plugs in IDP-aware policy.
5. **`QueryTranslator[Lang]`** — one per supported analytic query
   language: `GraphQLTranslator`, `CypherTranslator`, `SparqlTranslator`.
   `translate(query, ontology, materialization_map) -> SQL`. Translation
   is schema-aware, so the translator needs full read access to knot's
   ontology and materialization mapping.
6. **`MaterializationTarget`** — `output_schema(ontology_class) -> Schema`,
   `compile(class, resolved_data, deps) -> JobSpec`,
   `serves(query) -> bool` (does this target match a query?). Default
   in-tree impl: `LakeMaterializationTarget` (writes to the lake's
   per-source + resolved layers per the Lake schema pattern).
   Production deployers can register `Neo4jMaterializationTarget`,
   `NeptuneMaterializationTarget`, `VectorMaterializationTarget`, etc.
   See `benefits.md` § 7 for the framing — knot's dispatch pattern
   extends to consumers, not just upstream sources.

Notably **NOT** in the protocol catalogue:
- `LakeWriter` — covered by orchestrator (Spark job writes wherever).
- `DataQualityEngine` — schema-level DQ is SHACL via LinkML; ad-hoc DQ
  is just SQL checks the orchestrator runs.
- `RunReporter` — covered by `Orchestrator.status(run_id)`.

## Static pre-flight validation

Every spec submission goes through this gate before any orchestrator
dispatch. The validator runs in milliseconds; the orchestrator never
sees a spec that has a chance of failing for these reasons.

Checks (all static — no data touched):

- **SQL parses** — sqlglot accepts every emitted query.
- **Type compatibility** — LinkML types ↔ SQL types ↔ declared output
  shape align. SELECT projection types match the output node's
  declared schema.
- **DAG completeness** — every referenced upstream node exists; no
  dangling references, no forward references, no cycles. Cycle check
  here is the lineage-survey takeaway: reject cycles at write time
  rather than detect them at traversal time.
- **Plugin satisfiability** — referenced ER strategies, orchestrator
  capabilities, translator languages exist on the deployer's plugged
  implementations.
- **Authorization satisfiability** — caller has permission for every
  node + action the spec touches.
- **Ontology conformance** — declared outputs match a node's LinkML
  shape; SHACL constraints are at least declarable given projected
  types.
- **DQ-check SQL** — DQ checks themselves parse and reference real
  columns.

Validator errors are returned as structured records (path + check name
+ remediation hint) so the UI can highlight specific fields.

## Postgres schema (control plane only)

Postgres backs OLTP for the control plane. It does not store any
core graph data — that lives in the lake.

### Versioning + audit (locked)

```sql
-- Umbrella node: one row per logical ontology element
-- (data shape, pipeline, source, etc. — all uniform LinkML instances).
CREATE TABLE ontology_nodes (
  id                  UUID PRIMARY KEY,
  name                TEXT NOT NULL UNIQUE,        -- e.g. 'Movie', 'CastMembership', 'BuildMovieGraph'
  kind                TEXT NOT NULL,               -- 'data' | 'pipeline' | 'source' | 'er_strategy' | 'dq_check' | 'trust_policy' | 'materialization_target'
  current_version     TEXT,                        -- vMAJOR.MINOR pointer; null until first revision
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  archived_at         TIMESTAMPTZ                  -- soft-delete at the umbrella level only
);

-- Full-snapshot revisions, append-only.
CREATE TABLE ontology_node_revisions (
  id                  UUID PRIMARY KEY,
  node_id             UUID NOT NULL REFERENCES ontology_nodes(id),
  version             TEXT NOT NULL,               -- vMAJOR.MINOR
  content_hash        TEXT NOT NULL,               -- sha256 of canonicalized payload (dedup)
  payload             JSONB NOT NULL,              -- linkml-runtime instance, serialized
  is_breaking         BOOLEAN NOT NULL,            -- COMPUTED at write time via structural diff
  parent_revision_id  UUID REFERENCES ontology_node_revisions(id),
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by          TEXT NOT NULL,               -- user id (TEXT until users table lands)
  UNIQUE (node_id, version)
);

ALTER TABLE ontology_nodes
  ADD CONSTRAINT fk_current_revision
  FOREIGN KEY (current_version, id)
  REFERENCES ontology_node_revisions(version, node_id);  -- composite to enforce match

-- Strong forensic audit, append-only via trigger.
CREATE TABLE history (
  id            UUID PRIMARY KEY,
  entity_type   TEXT NOT NULL,                     -- 'ontology_node' | 'correction' | 'source' | …
  entity_id     UUID NOT NULL,
  activity_type TEXT NOT NULL,                     -- 'created' | 'updated' | 'archived' | …
  user_id       TEXT NOT NULL,                     -- promote to FK when users table lands
  request_id    TEXT,                              -- correlation id from the API request
  source_ip     INET,
  trace_id      TEXT,                              -- OTLP / W3C tracecontext
  pre           JSONB,
  post          JSONB,
  details       JSONB,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Append-only enforced via trigger that rejects UPDATE / DELETE.
```

**Versioning policy**:
- **Version IDs**: `vMAJOR.MINOR` strings (DJ-compatible, human-meaningful semver).
- **`is_breaking`**: computed at write time via structural diff (LinkML
  schema deltas) — additive (new optional slots, new classes,
  annotations) → minor; subtractive or type-changing (removed slots,
  type changes, required-ness, narrowing enums) → major. The 5-field
  heuristic from DJ is replaced with a real diff.
- **Content hash**: re-submitting an identical payload returns the
  existing revision (no new row).
- **Parent pointer**: enables diff/blame queries.
- **"Current"**: explicit `current_version` pointer on the umbrella
  row — supports a future "draft revision" concept without schema
  changes.
- **Append-only revisions**: revisions never get updated or deleted;
  `archived_at` only on the umbrella row.
- **No rollback API**: to revert, submit the prior payload as a new
  revision (forward-only history).
- **No automatic GC**: keep all revisions until scale demands otherwise.

### Lifecycle invariants

Five patterns recur across every node kind (data, source, pipeline,
er_strategy, dq_check, trust_policy, materialization_target). They
are knot-wide, not per-kind.

1. **Drafts permissive, publishes strict.** Drafts may be invalid,
   reference unpublished nodes, or contain stale plugin refs.
   Publishing runs the full static validator in strict mode and
   refuses anything that doesn't fully resolve. Late binding lives
   only at the draft layer.
2. **`is_breaking` is computed, not declared.** Structural LinkML
   diff against the parent revision determines additive vs breaking.
   The author cannot override.
3. **Pre-flight gates dispatch.** A failed pre-flight does not write
   a `runs` row, does not consume orchestrator queue depth, does not
   spend Spark. The validator is centralized because only knot has
   the full ontology + lineage + plugin + auth view.
4. **Knot owns run state; orchestrator owns task state.** The
   orchestrator retries tasks; knot retries stages and runs; Publish
   is the only stage where a postgres transaction (not lake state)
   determines published-ness.
5. **Lake rows carry the revision IDs of every version-tracked input
   active at materialization.** `source_revision_id`,
   `ontology_revision_id`, `er_strategy_revision_id` (when
   applicable), and existing `correction_id` are columns on the
   per-source layer. This makes "what exact configuration produced
   this fact" answerable for any past run.

### Cross-cutting policy defaults

These were identified as opens during lifecycle design. Defaulted as
follows; override case-by-case when implementing.

| # | Policy | Default |
|---|--------|---------|
| A | Initial trust value on first source publish | Configurable per source kind via the source LinkML; default 0.5 on a 0–1 scale. |
| B | Trust continuity across breaking source revisions | Reset to initial trust on breaking revision (data shape materially changed). Minor revisions retain trust. |
| C | Quarantining relation rows during entity-key remap | A breaking entity-key publish requires coordinated drafts of every dependent relation. On co-publish, relation per-source rows are regenerated; rows that cannot remap are excluded from the resolved layer and marked `quarantined=true` in per-source. |
| D | Lineage propagation / backfill on upstream change | **Opt-in.** Publishing a new revision does not auto-rebuild downstreams; owners must author new downstream revisions to consume the change. |
| E | Per-cluster lineage columns | Per-source layer carries `source_revision_id`, `ontology_revision_id`, `er_strategy_revision_id` (when applicable), plus existing `correction_id`. |
| F | Soft-DQ warning vs hard-fail threshold | Per-DQ-check configurable in its LinkML node; default warn-only. Hard-fail thresholds are opt-in. |
| G | Same-pipeline overlapping runs | Reject (409 Conflict). Per-pipeline advisory lock; one run per pipeline at a time. Cross-pipeline parallelism is unrestricted. |

### Judgments — evaluative feedback on outcomes

Distinct from `corrections` (which fix specific facts), **judgments**
are evaluative-on-outcomes: "this ER cluster was a bad merge", "this
DQ check is too strict", "this materialization run produced obviously
wrong output." Judgments are knot's primary high-trust signal for
**retraining and tuning** — closing the human-in-loop feedback loop
on outputs the way corrections close it on inputs.

| Concept | Corrections | Judgments |
|---|---|---|
| What it fixes | A specific fact (this title is wrong) | An outcome (this cluster is wrong) |
| Target | Entity property / relation property | ER cluster / DQ run / materialization run / strategy revision |
| Verdict | Replacement value | `good` / `bad` / `ambiguous` |
| Use as input to ER | Yes — corrections-source pipeline | Yes — high-trust training/tuning signal for `ERStrategy` |
| Use elsewhere | High-trust source for that property | Inputs for DQ rule tuning, strategy A/B evaluation, alerting |

**Schema (locked):**

```sql
CREATE TABLE judgments (
  id            UUID PRIMARY KEY,
  target_kind   TEXT NOT NULL,        -- 'er_cluster' | 'dq_check_run' |
                                      -- 'materialization_run' |
                                      -- 'er_strategy_revision' | …
  target_id     TEXT NOT NULL,        -- opaque per kind (UUID, run_id, etc.)
  verdict       TEXT NOT NULL CHECK (verdict IN ('good', 'bad', 'ambiguous')),
  reason        TEXT,                 -- free-form rationale
  user_id       TEXT NOT NULL,
  request_id    TEXT,
  source_ip     INET,
  trace_id      TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX judgments_target_idx ON judgments(target_kind, target_id);
CREATE INDEX judgments_user_idx ON judgments(user_id, created_at);
```

Append-only via the same trigger pattern as `history`. Every judgment
is its own row; revisions of a judgment require a new row (no UPDATE).

**API surface:**
- `POST /judgments` — submit one (body: `target_kind`, `target_id`,
  `verdict`, `reason?`).
- `GET /judgments?target_kind=…&target_id=…` — list judgments on a
  target (for the UI's "what others said about this" view).
- `GET /judgments/by-user/{user_id}` — audit / leaderboard.

**UI surfaces (new):**
- **Entities page**: per-resolved-entity row, a thumbs-up / thumbs-down
  control. Verdict targets `er_cluster`, `target_id = entity.uuid`.
- **Pipeline run detail**: judge the whole run (`materialization_run`).
- **Future ER-runs page**: per-cluster judgment workflow for the curator
  / ER engineer personas.

**ER strategies and DQ rules read judgments** as input at next run:
- `ERStrategy.merge()` can downweight cluster shapes that have a
  history of `bad` judgments.
- `DataQualityCheck` can include "this rule has been judged
  too-strict N times in the last week" as a signal in its threshold
  computation.

This makes the architectural through-line: **knot owns every signal
the platform produces, both factual and evaluative.** Sources, ER,
DQ, materialization — all evaluable, all retrainable.

### Deployment & CI

Knot ships through standard CI/CD with environment promotion gated on
tests:

- **CI runs on every PR**: unit tests + integration tests (the
  Docker-stack e2e suite) must pass. Lint + typecheck gate too.
- **Promotion path**: `Dev → QA → Prod` (or `Dev → Prod` for orgs
  without a QA tier). A green CI build on `main` is what makes a
  Dev deploy possible; a green Dev deployment plus the integration
  e2e suite passing against Dev is what makes a QA promotion
  possible; same gate again from QA to Prod.
- **Tests are the gate, not a sidecar.** A failing unit or
  integration test blocks the promotion until fixed — no override.
  The integration suite already exists and passes (Docker stack
  exercises knot → scheduler → parquet lake e2e); it stays the
  promotion gate.
- **Migrations run as part of the deploy step**, not as a separate
  manual ops action. Deploy = pull image + run `knot-migrate` +
  start service. Migrations are forward-only per pre-release policy.
- **Identity/authz/orchestrator/ER/DQ/translator implementations**
  are env-gated via `KNOT_*` variables (e.g. `KNOT_IDENTITY_PROVIDER`,
  `KNOT_ORCHESTRATOR`, `KNOT_AUTHZ_SERVICE`). Dev typically uses
  `header` + `allow_all`; Prod swaps to deployer-internal plugins.
- **Ontology + source state is data, not code.** Promotion through
  environments doesn't carry control-plane data (the
  `ontology_nodes` / `sources` / etc. rows) — each environment has
  its own postgres. Bootstrap notebooks (or the equivalent
  per-environment seed scripts) populate Dev; Prod is populated by
  the actual upstream sources.

### Other control-plane tables (descriptive sketch — not yet locked)

- `node_dependencies` — table-level lineage edges between ontology
  nodes.
- `sources` — runtime state for `kind='source'` nodes: 1:1 with
  `ontology_nodes.id` for the LinkML definition; this table holds
  current `trust_score`, last-refresh timestamp, etc.
- `corrections` — user submissions: user, timestamp, kind
  (`property | relation | merge`), target_entity_id, target_field,
  payload, overridden_source_id.
- `judgments` — evaluative feedback on outcomes (locked above).
- `runs` — orchestrator run records: id, node_id, started_at,
  finished_at, status, orchestrator_run_id, spec_hash.
- `run_events` — per-run event log (started, stage_completed, failed,
  etc.) for the runs detail view.
- `watermarks` — per-source / per-stage progress markers (last-seen
  partition, last-ingested timestamp, etc.).
- `stage_configs` — pipeline-stage configuration when needed.

## Lake schema pattern (the actual graph data)

Per ontology node, two materialized objects:

1. **Per-source layer** — one row per `(entity, property/relation,
   source)`. Carries `materialized_at`, `source_id`, `correction_id`
   (nullable), `trust_at_materialization`, and the value itself.
   Preserves provenance natively.
2. **Resolved layer** — derived view over the per-source layer. Window
   function picks the highest-trust value per `(entity, property)`
   tuple at materialization time.

The API joins both for read-side responses (resolved + alternatives in
one shot). This means the materialization pipeline writes both layers,
not just the resolved view — extra storage cost in exchange for
constant-time provenance reads.

## API surface

Endpoint families (REST; specific shapes are an open design item):

- **Ontology** — CRUD on nodes + revisions, list, search, current vs
  historical revisions.
- **Materialization** — submit a spec, get a run_id; list runs, get
  status of a run, cancel a run.
- **Trigger** — `POST /pipelines/{name}/runs` (single pipeline),
  `POST /trigger` or `POST /runs?pipelines=…` (all/tagged subset);
  optional webhook callback URL. Lets external orchestrators (Maestro,
  Argo, anything) call knot end-to-end as a job step.
- **Lineage** — get upstream/downstream of a node; column-level lineage
  blob; lineage graph render.
- **Per-fact reads** — `GET /node/{id}` returns resolved values +
  alternatives + sources + trust scores. Per-relation analogue.
- **Corrections** — submit a correction (property/relation/merge);
  list corrections; get correction history for a node.
- **Sources** — list, get current trust score, get correction history.
- **Translation** — `POST /translate/{lang}` with a query in
  GraphQL/Cypher/SPARQL, returns SQL.

GraphQL or REST or both for the core API is part of the open API
surface design item.

## Trust scoring + corrections feedback loop

```
upstream sources + corrections-as-source  →  ER  →  graph (lake)
                          ↑                              │
                          └─── UI corrections ←──────────┘
```

The corrections-as-source pipeline packages user corrections as a
coherent first-class source that ER consumes. ER stays pure ("merge
these N sources by trust score"); humans-in-the-loop is one of those N
sources, with the highest trust by default.

Trust scores are knot-owned (algorithm + postgres-resident state):
- Sources lose trust when corrections contradict them.
- Sources gain trust over time without contradictions.
- The corrections-source has fixed/configurable high trust.
- The exact formula (decay rate, per-property granularity, weighting)
  is an open design item.

## Reconciliation timing

**Materialize-time, not query-time.** The resolved layer is computed
during materialization, not on every read. UX promise: "your
correction is queued; the next pipeline run reflects it." Acceptable
for v1 because:
- Reads are cheap (no per-request reconciliation cost).
- Corrections-source pipeline can be triggered explicitly when a batch
  of corrections accumulates, so latency is bounded by pipeline
  cadence, not by each individual fix.
- Query-time reconciliation can be added later if a use case needs
  it; the per-source layer always has the data.

## Open design items

These are the only things still open after this architecture settles.

1. **Lineage propagation policy on ontology change.** When an upstream
   node's schema changes, what happens to downstream materializations?
   Options: mark stale; auto-invalidate; auto-rebuild; block downstream
   reads until rebuild. Real UX trade-off.
2. **Consumer versioning model.** When knot publishes a new revision,
   do consumers see it immediately or pin to revisions? Live-read vs
   pinned-snapshot.
3. **API surface details.** REST shape, GraphQL schema, error model,
   pagination, etc. Design work, not a decision.
4. **Trust scoring algorithm.** Decay rate, per-property vs per-source
   granularity, weighting of corrections, how a contradicting
   correction translates to a trust delta.
5. **Reconciliation timing edge cases** — settled at materialize-time
   for v1; revisit if a real query-time use case surfaces.

## What this supersedes

Per pre-release policy (forward-only, no deprecation):

- `design-spec-v0-DRAFT` — superseded by this doc. Was an AI sketch,
  never read or validated. Delete.
- `review-design-spec-v0` task — moot; this doc is the review output.
- `split-requirements-from-aspirations` task — this doc IS the split.
- `research-datajunction-patterns` task — superseded by the four DJ
  surveys (DI, lineage, materialization, schema versioning).
- `decide-graph-store` — out of scope (downstream consumer).
- `decide-dq-engine` — DI pattern + SHACL via LinkML.
- `decide-multi-tenancy` — single graph, decided.

`draft-sample-linkml-schema` survives — concrete v1 work.

## Concrete next step

The first vertical slice: `ontology_nodes` table → load a LinkML
schema into a versioned record → `GET /node/{id}` returns it. Proves
the canonical claim "LinkML is the source of truth, exposed through
FastAPI" end-to-end. Everything else builds out from there.
