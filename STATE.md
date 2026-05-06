# knot — current state (for compaction handoff)

A thorough snapshot of where the project is. Read this first after a compaction or fresh session.

---

## What knot is

A reflective ontology compiler for **one team** (no tenants). Spec edits validate against the running system that interprets them. Knot owns spec, compile, audit walk-back; bound DI impls own ER, materialization, query execution, translation. Compile-only: orchestrator (Maestro/Airflow/toy) dispatches.

External users enter at four narrow surfaces (per `design/goals.md` "Who uses knot"):
1. Read materialized outputs directly
2. Lake query via knot's built-in API
3. Materialized-target query via Translator impl (only when target speaks non-SQL/different schema)
4. Submit corrections via UI

## Repo

- **Local**: `/mnt/main/code/knot/`
- **Remote**: https://github.com/CircArgs/knot (private)
- **Notebook server**: marimo at http://100.65.247.65:2718 (Tailscale; `--watch` mode; PID 2861548 last known)

```
/mnt/main/code/knot/
├── design/                    # canonical design docs (commitments, staging)
│   ├── goals.md
│   ├── core-design.md          # 17 commitments
│   └── staging/                # 30+ design docs; all decisions enshrined here
├── src/knot/                   # implementation
│   ├── metaschema.py           # Pydantic spec models, expression tree
│   ├── canonical.py            # RFC 8785 JCS dump + content_hash; cycle-safe (v2)
│   ├── protocols.py            # Protocol family + Result shapes
│   ├── walk.py                 # single-dispatch over expression tree
│   ├── impact.py               # impact analysis
│   ├── registration.py         # DataContext validation at registration
│   ├── codegen.py              # SDK codegen (lens-aware)
│   ├── sql_gen.py              # sqlglot AST builder; emit_sql/forward/backward/CTE
│   ├── compiler.py             # spec + impls + configs + watermarks → WorkflowSpec
│   ├── workflow_spec.py        # WorkflowSpec / StageSpec Pydantic shapes
│   ├── orchestrator.py         # toy in-process orchestrator; dispatches stages
│   ├── api.py + api_models.py  # FastAPI surface
│   ├── control_schema.sql      # postgres-control schema
│   ├── control_db.py           # apply_schema helper
│   └── lake/
│       ├── duckdb_reader.py        # implements QueryReader Protocol
│       └── duckdb_materializer.py  # implements Materializer Protocol
├── tests/
│   ├── conftest.py             # session autouse: applies control schema
│   ├── test_smoke.py           # postgres + neo4j + apoc reachable
│   ├── test_env.py             # TestEnv helper class (xfail-strict harness)
│   ├── unit/                   # 10+ test files
│   ├── integration/            # API, orchestrator, neo4j_publisher, env_smoke
│   │   └── conftest.py         # NOTE: DO NOT add no-op _apply_control_schema overrides
│   └── fixtures/               # 9-tier matrix (A1, B2, C2 implemented)
│       ├── README.md           # 9-tier matrix
│       ├── EDGE-CASES.md       # 17 categories of seeded edge cases
│       ├── generator.py        # deterministic synth data generator
│       ├── tier_configs.py
│       ├── A1/  B2/  C2/       # implemented; specs + sources/ + impls/
│       │   ├── spec.py
│       │   ├── sources/        # CSVs with seeded edge cases
│       │   └── impls/          # er_movie/person/credit, neo4j_publisher,
│       │                       # iceberg_publisher, dq_merge — REAL bodies
│       └── A2/A3/B1/B3/C1/C3/  # template stubs
├── notebooks/
│   ├── 00_state_of_play.py     # broad project overview
│   ├── 01_progress.py          # interactive sandbox (8 sections)
│   └── layouts/                # marimo slides
├── docker-compose.yml          # postgres:16 + neo4j:5-community (apoc); tmpfs
├── scripts/                    # up.sh / down.sh / wait-ready.sh
├── pyproject.toml              # base: pydantic; runtime: sqlglot, pyiceberg,
│                               #   duckdb, jcs, fastapi, uvicorn, rapidfuzz
├── site/                       # MkDocs Material — Layer 1 + 2 of write-up
│   ├── mkdocs.yml
│   └── docs/
└── .venv/                      # Python 3.14
```

## Suite state

**Last verified on clean stack: 325 passed, 17 xfailed, 1 xpassed.**

- 17 xfailed are integration tests in `test_ontology_expansion.py` + `test_env_smoke.py` waiting on knot core machinery that's still missing (compiler-driven impl_name on `dq:*` stages, full WorkflowSpec dispatch, in-flight correction overlay, end-to-end audit walk-back).
- 1 xpassed: a previously-strict-xfail that landed during Round 3.

## Layers / rounds completed

| Round | Slice | Outcome |
|---|---|---|
| Day 1 (pre-rounds) | metaschema + canonical + control schema + protocols + walk/impact/registration + codegen + sql_gen + compiler + cycle fix | 325 pass baseline |
| Round 1 | FastAPI surface + DuckDB QueryReader + DuckDB Materializer | +27 tests |
| Round 2 | Toy orchestrator (normalize + merge end-to-end against B2) + 6 Nick fix-ups | +7 tests |
| Round 3 | Real ER bodies (rapidfuzz) + Neo4j publisher (UNWIND/MERGE) + DqMergeRunner + orchestrator bound-impl invocation | +29 tests |

Rounds remaining (per the Nick-defined cadence: build → verify clean stack → commit + push → extend notebook → Nick-reviewer grade → next):

- **Round 4**: Translator endpoint + correction overlay path
- **Round 5**: Audit walk-back end-to-end integration test

## Hard-won posture (multi-tenant smell rule)

Every dispatch brief now starts with:

> Multi-tenant patterns are an automatic finding-discard. Single-team posture per `goals.md` "Who uses knot" + `core-design.md` commitment 5. If your reasoning slips into platform / multi-tenant / hostile-author / Lambda / Workers / supply-chain / per-tenant-isolation defenses, scrap that line.

Caught and scrapped during Days 1–3:
- Per-impl `requires` deps tracking (Lambda layers reflex)
- `submitted_by` per-actor attribution columns
- Hardcoded `pyarrow.Table` in QueryReader Protocol (still drifting in places — see open items)
- `Path` fields in Result types (partial fix — see open items)
- Orchestrator reaching for `ctx.lake_dir` raw filesystem paths

## Impl contract (just enshrined — see `design/staging/impl-contract.md`)

The doc that took too many rounds to land. Summary:

- **Knot owns**: I/O contract tables at the lake (per_source_facts, resolved_facts, entity_bindings, canonical_id_lineage); control plane in postgres (compiled_workflows, pipeline_runs, bound_impls, impl_revision, impl_config, _user_corrections, _user_er_decisions); DataContext fulfillment; result metadata storage; compile hash.
- **Knot does NOT own**: in-memory data shape (the bound `QueryReader` decides — Arrow Table for DuckDB, Iceberg snapshot ref for S3+Trino, view name for Spark); filesystem layout for impl outputs; Python deps; auth/sandboxing.
- **Result objects**: metadata only — `output_uri: str` (opaque) + `column_map`. NOT embedded data. Knot stores; downstream stages reference via `ctx.upstream_results`.
- **`ctx`**: typed `RunContext` exposing `config`, `run_id`, `compile_hash`, `binding_info`, `upstream_results`, `query_reader`, `materializer`. Never `ctx.lake_dir`, raw paths, postgres connection.
- **Impl writers**: declare Config + DataContext attrs; method receives ctx + DC views (whatever shape the bound QueryReader returns); write through `ctx.materializer`; return typed Result.

## In-flight when this doc was written

Two background workers running:

1. **Impl-contract correction worker** — applying the corrections from `impl-contract.md`:
   - ERResult/DqResult/etc. `Path` → `output_uri: str`
   - `QueryReader.read() -> pa.Table` → `Any` (backend-specific)
   - Define `RunContext` typed Pydantic
   - Orchestrator stops reaching for `ctx.lake_dir`
   - Fixture impls write through `ctx.materializer`

2. **Drift audit worker** — completed; report at `/tmp/drift-audit.md`. Surfaced 4 high, 6 medium, 7 low severity drift items.

## Known drift / open issues (from drift audit + Nick reviewer)

**Will be fixed by in-flight contract-fix worker:**
- `protocols.py:267` `QueryReader.read() -> pa.Table` hardcoded
- `protocols.py:137,145,152` Result types still use `Path` (3 of 6)
- Orchestrator reaches for `ctx.lake_dir`
- Fixture impls write directly to filesystem via `ctx.lake_dir`

**NOT yet scheduled (deeper architectural):**
- `orchestrator.py` imports concrete `DuckDBMaterializer` / `DuckDBReader` directly. Should accept bound impls via DI; orchestrator must be lake-backend-agnostic.
- `_materialize_datacontexts` returns `dict[str, pa.Table]` — knot core hardcoding Arrow as DC view shape.
- `dq_merge.py` reads parquet via `per_source_facts/<class>.lower().parquet` flat layout vs orchestrator's hive-partitioned `per_source_facts/<Class>/source=*/data.parquet`. Silent zero-rows on full pipeline path.
- Shape contract drift: orchestrator passes `dict[attr_name, pa.Table]`; neo4j publisher accepts `list[(OntologyClass, pa.Table)]` via duck-type. No integration test crosses orchestrator → bound publisher with multi-class primary.

**Single-team posture leaks (medium):**
- `design/staging/ui-and-integrations.md:35-37` — proposes per-actor `AuthProtocol`. External-users-write-spec is not knot's posture.
- `design/knot-as-compiler.md:74` + 2 staging docs use "reference impls" vocabulary (Pattern 13 calls this out).

**Pattern 9 violations (low — v0/v1/future-work hedges):**
- `src/knot/canonical.py:13` "(v2)" parenthetical
- `src/knot/api.py:4,63,353` three "TODO: future slice"/"for now"
- `design/staging/sql-generation.md:18` "roadmap"
- `design/staging/spec-model.md:323` "Rejected for now"
- `design/staging/er-and-storage.md:98` "tbd"
- `design/staging/example-modeling-walkthrough.md:136` "for now"
- `design/_handoff-2026-04-29.md:82`

## Notebooks

- `00_state_of_play.py` — broad overview, 17 commitments table, layer status, fixture matrix, B2 samples
- `01_progress.py` — interactive sandbox (8 sections):
  1. Spec class explorer (B2 dropdown)
  2. Expression-tree builder (type SDK expression → Pydantic AST)
  3. Canonical-dump preview (live JCS bytes + sha256 hash)
  4. SDK → SQL → DuckDB live execution against B2 fixtures (real Arrow Table results)
  5. Pydantic validation playground
  6. Trust resolution simulator (parallel-Python, labeled as such; §6b shows real `sql_gen.emit_trust_resolved_cte` output)
  7. SDK codegen output viewer (lens-aware: same spec, different shape per ProtocolKind)
  8. Live `compile()` → WorkflowSpec with stages table + content-addressed compile_hash

## Comprehensive walkthrough notebook (planned, NOT YET BUILT)

User asked for a notebook that walks through everything end-to-end. Planned `notebooks/02_walkthrough.py` covering:

1. Source registration (declare via Pydantic, validate)
2. Ontology modelling (classes, slots, derivations; live canonical hash + JCS bytes)
3. Spec edit + impact tracing (rename a slot, see `affected_workflows()`)
4. Draft impl (paste source, hit register endpoint, DataContext validation accepts/rejects)
5. Bind impl (write `bound_impls` row)
6. Compile (live WorkflowSpec with stages + cache_keys + pinned_parent_runs)
7. Dispatch via toy orchestrator (live pipeline_runs status transitions; parquet lands; Cypher runs)
8. Lake consumption (read resolved_facts via ctx.query_reader)
9. Materialized-target consumption (live Cypher against Neo4j)
10. Audit walk-back (Neo4j fact → KnotRun.compile_hash → spec_revs → contributing source rows)
11. Multi-revision drafts (switch binding; see new compile_hash; old runs still walk back)
12. Correction submission + overlay (when Round 4 lands)

Build it after the contract-fix worker lands (sections 6–10 use the Result types being corrected).

## Workflow / cadence (Nick-established)

Per round:
1. Dispatch impl worker(s) (independent slices in parallel)
2. Wait for completion (workers go silent post-finish — pattern; check files + tests instead of relying on SendMessage)
3. **Verify on a CLEAN stack** (`down + up + wait-ready + pytest`) — don't trust stale schema
4. Commit + push (each round its own commit)
5. Extend notebook with new tangible affordances (no git stats, no commit counts, no walkthrough; user wants to PUT HANDS ON things)
6. Verify notebook runs (`python notebooks/01_progress.py` exit 0)
7. Dispatch "Nick reviewer" persona to grade — they pretend to be Nick, do real testing on clean stack, write verdict to `/tmp/nick-review-roundN.md`
8. If PASS → next round. If FAIL → fix-up dispatch addressing specific complaints; re-verify; re-review.

## Multi-tenant smell rule (top-of-brief on every dispatch)

```
Multi-tenant patterns are an automatic finding-discard. Single-team posture
per goals.md "Who uses knot" + core-design.md commitment 5 ("Trust posture:
knot has no tenants"). Knot is a tool for ONE team. If your reasoning slips
into platform / multi-tenant / hostile-author / Lambda / Workers /
@task.virtualenv / supply-chain / per-tenant-isolation defenses, scrap that
line.
```

This rule MUST go on every dispatch — it's the only thing that consistently catches the multi-tenant SaaS reflex agents otherwise smuggle in.

## Test infrastructure

- `bash scripts/up.sh` — postgres:16 + neo4j:5-community (apoc) via docker-compose with tmpfs (ephemeral)
- `bash scripts/wait-ready.sh` — polls healthcheck for both
- `bash scripts/down.sh` — `docker-compose down -v`
- Test fixtures (in `tests/conftest.py`):
  - `_apply_control_schema` (session autouse) — applies `control_schema.sql` once per session
  - `pg_conn`, `neo4j_driver`, `postgres_dsn`, `neo4j_uri` standard fixtures
- **Critical**: `tests/integration/conftest.py` MUST NOT define a no-op `_apply_control_schema` override (it shadows the schema-applier). Round 2 fix-up removed this; do not re-introduce.
- Tests pass on clean stack only after `apply_schema` has run via the autouse fixture.

## Verifying everything works

```bash
cd /mnt/main/code/knot
bash scripts/down.sh && bash scripts/up.sh && bash scripts/wait-ready.sh
.venv/bin/pytest tests/                                      # 325 passed expected
.venv/bin/python notebooks/01_progress.py                    # exit 0
bash scripts/down.sh
```

## Pending decisions

None blocking. The architectural decisions are all enshrined:
- Multi-valued semantics (lens-driven defaults; `disagreement_stance` per Protocol)
- DataContext-Config binding (symbolic ConfigRef, substituted at compile)
- Multi-class DataContexts (`primary` accepts class lists; per-class fan-out)
- Graph-level incremental (per-stage cache keys; no row-level delta API)
- Cross-class pinning retention (never delete `compiled_workflows`)
- Trigger model (knot is not a scheduler; API call → toposort → orchestrator)
- Derivation refs walk into compile network like slot.range
- Translator only for non-lake targets; built-in lake query path
- DqRunner protocol family (Normalize/Resolve/Merge/Publish per stage)
- Query language: typed Pydantic AST (rejected Gremlin/Cypher as source-of-truth; they earn place as Translator emit targets only)
- Borrowed-from-Gremlin AST nodes: Within, Between, Matches, RecursiveTraversal (LANDED)
- Library deps: not knot's concern; new library = redeploy; `runtime_image_identity` audit affordance only
- Impl contract: knot owns I/O contract tables; impls own outputs; Results are metadata; ctx is typed `RunContext`

All listed at `design/staging/*.md` with full rationale + cross-references.
