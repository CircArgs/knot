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

## Current goings-on (READ THIS SECTION FIRST POST-COMPACTION)

### Where we are right now

Just finished:
- **Round 3** (real ER + Neo4j publisher + DqMergeRunner + orchestrator bound-impl invocation) — landed, Nick-reviewed PASS-with-caveats
- **Contract-fix worker** — applied all impl-contract.md corrections (Result types, RunContext, no `ctx.lake_dir` overreach)
- **Drift audit** — completed; report at `/tmp/drift-audit.md`; high-severity items mostly absorbed by contract-fix
- **STATE.md** — being expanded for handoff right now

No background workers in flight as of this writing. Suite at **325 passed, 17 xfailed, 1 xpassed** on a clean stack.

### What the user just asked for (HIGHEST PRIORITY)

The user said:
> "The notebooks better reveal the impls fully and walk through everything from source registration, ontology modelling, DI impls, consumption from lake and materialized sources, drafts, publishing, tracing of downstream effects from changes,... Everything"

**Action**: build `notebooks/02_walkthrough.py` covering all 12 sections listed earlier in this doc. The user wants to put hands on real things — every cell must be live machinery, not status display. Verify it actually runs (`python notebooks/02_walkthrough.py` exit 0) before claiming done.

User's notebook anti-patterns (caught in earlier rounds; do not repeat):
- No git stats, commit counts, line-of-code deltas
- No "what we did" walkthrough framing
- No status pages
- No raw SQL input — drive through SDK
- Cells must use `_`-prefixed locals so they don't collide across cells; cross-cell names use unprefixed return values
- Raw `pyarrow.Table` may not work for cycles in B2 spec → handle gracefully

User's notebook affordance preferences (positive):
- Type SDK expression → see Pydantic AST live
- Type SDK expression → see emitted SQL live
- Run real SQL via DuckDBReader against B2 fixture parquet → see Arrow Table results
- Pick a class → see its slots / derivations / sources
- Pick a `ProtocolKind` → see the lens-aware codegen output
- Edit a Pydantic dict → see `extra="forbid"` rejection live
- Pick a scope → see live `compile()` → WorkflowSpec stages table + content-addressed compile_hash
- Edit contributions / policy → see resolved value live with reasoning

### What's queued

After comprehensive walkthrough notebook, in priority order:
1. **Round 4**: Translator endpoint + correction overlay path
   - `POST /query` endpoint takes ontology expression → sql_gen.emit_sql → DuckDBReader → results
   - Trust-CTE attached when consumer-facing path; correction overlay JOINs `_user_corrections` postgres
   - Translator impl per non-lake target (Cypher for Neo4j; defer Gremlin/SPARQL to future)
   - Correction submission already exists at `POST /corrections`; needs to actually overlay at translator query time
2. **Round 5**: Audit walk-back end-to-end integration test
   - Pick a published canonical fact in Neo4j → walk back via `(:KnotRun)` audit node → compile_hash → spec_revs → contributing source rows
   - Convert several xfail tests in `test_ontology_expansion.py` and `test_env_smoke.py` to passing
3. **Architectural correction (post-Round 5 candidate)**: Orchestrator currently hardcodes DuckDB. Should accept bound QueryReader/Materializer impls via DI so the toy orchestrator can be swapped for Maestro at Netflix port-time. ~half-day slice.

### Open design tensions (none blocking; flagged for awareness)

- **Shape contract drift** between orchestrator (`dict[attr_name, pa.Table]`) and Neo4j publisher (`list[tuple[OntologyClass, pa.Table]]`). Worker resolved Pydantic-`__hash__`-on-OntologyClass missing via duck-typing on `.items()`. No integration test crosses orchestrator → bound publisher with multi-class primary; would explode at runtime. Fix: either add `__hash__` to OntologyClass or commit canonically to one shape.
- **`dq_merge.py` lake path** in production reads via `ctx.query_reader.read()` (post-contract-fix); but the orchestrator's hive-partitioned `per_source_facts/<Class>/source=*/data.parquet` layout vs dq_merge's pre-fix `per_source_facts/<class>.parquet` flat layout — verify they match now. If they don't, dq_merge silently zero-rows on the live pipeline.
- **Spec-loading from where**: knot's `api.py` loads B2 fixture spec at import time. Real deployments need to load from postgres-control or a config path. Open in `staging/spec-loading.md`. Decide before any "real" deployment.
- **`ui-and-integrations.md` per-actor AuthProtocol**: drift audit flagged this as multi-tenant smell. Single-team posture says external users only enter at four narrow surfaces; per-actor identity inside knot's seam doesn't fit. Worth a single-pass cleanup of that staging doc.
- **"reference impls" vocabulary**: drift audit flagged in `knot-as-compiler.md` and 2 other staging docs. Should be "team-owned bindings" or just "bindings" — Pattern 13 calls "reference impls" out as platform-language reflex.

### User mood / pacing signals to honor

- **Tolerance for drift is low**. The deps-management round-trip and the impl-contract clarification both wasted cycles. User said "It's sad we even had to discuss this. Worries me you've gone astray in other ways." The drift audit + contract-fix were the right response. Continue with that pattern: every round, check for drift before claiming done.
- **Wants tangible**, not status. Notebooks must let user manipulate real machinery. Status pages are a smell.
- **Pacing-sensitive**. 5-sentence answers are sometimes too long for them; prefer 2-3 sentences when answering questions, structure for picks.
- **Honest about deferrals**. "Future work" / "TBD" / "v1" — Pattern 9 violation. Either committed or marked explicitly open.
- **Verify on clean stack**. Always `down + up + tests`, never trust stale schema. Workers also can't be trusted to verify on clean stack — orchestrator (you) must verify.
- **Multi-tenant smell rule** is non-negotiable. Top-of-prompt on every dispatch, verbatim.

### Active worker dispatch (none right now)

If you dispatch in a fresh session, remember:
- Workers go silent post-completion (SendMessage often not called); check files + git status + tests instead of waiting on notification
- Reviewer agents (role-playing Nick) write to `/tmp/nick-review-roundN.md`; check that file directly
- For team coordination via TeamCreate, manually patch `~/.claude/teams/<name>/config.json` to remove stale members if SendMessage shutdown hangs

### What to do FIRST after compaction

1. Read this `Current goings-on` section
2. Read `design/staging/impl-contract.md`
3. Verify on clean stack: `down + up + wait-ready + pytest tests/` → 325 pass expected
4. Verify the running marimo: `curl -sI http://localhost:2718/` → 200 OK; if not, restart per "Common workflows" section
5. Build the comprehensive walkthrough notebook (`notebooks/02_walkthrough.py`) — this is what the user explicitly asked for
6. Then Round 4 (translator + correction overlay)
7. Then Round 5 (audit walk-back end-to-end)

---

## Recently landed (contract corrections)

Impl-contract worker completed — all corrections from `design/staging/impl-contract.md` applied:

- All Result types: `Path` → `output_uri: str` (opaque). Six Result types: ERResult, MaterializeResult, DqResult, TranslateResult, ConstraintResult, DerivationResult.
- `QueryReader.read()` return type loosened from `pa.Table` to `Any` with backend-explaining docstring; concrete DuckDBReader still narrows to pa.Table.
- `RunContext` + `BindingInfo` typed Pydantic models added to `src/knot/protocols.py`.
- Orchestrator constructs proper `RunContext` (config/run_id/compile_hash/binding_info/upstream_results/query_reader/materializer); no `ctx.lake_dir` exposure.
- Fixture ER impls (er_movie/person/credit) write through `ctx.materializer.materialize()` in production path; `ctx.lake_dir` fallback retained for plain-ctx unit tests.
- dq_merge.py reads via `ctx.query_reader.read()` in production; materializer writes offenders.
- Tests updated: all `result.table` → `result.output_uri`; all `offenders_table` → `offenders_uri`; etc.

Verified on clean stack: **325 passed, 17 xfailed, 1 xpassed.** No regressions.

## Drift audit findings (from `/tmp/drift-audit.md`)

Drift audit worker completed; report at `/tmp/drift-audit.md`. 4 high, 6 medium, 7 low. Most highs were addressed by the contract-fix worker.

## Known drift / open issues remaining

**Deeper architectural (NOT yet scheduled):**
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

---

## The 17 architectural commitments (full text inline for handoff)

From `design/core-design.md`. Read the canonical doc for full rationale; this is the index.

1. **Knot is a compiler.** No internal queue, scheduler, SQL engine, or HTTP loop touching data. Bound DI interfaces are the boundary; everything that touches data plane crosses through one.
2. **Spec-as-data, with real Pydantic object references in-memory.** Refs are real Python object refs between metaschema entities (not name-strings). Strings live at the persistence boundary only (JSONB write/read).
3. **Content-addressed compile hashes are run identity.** Every workflow trigger = fresh compile reading current revisions of every spec node in the compiled workflow's dependency graph. RFC 8785 JCS canonicalization → sha256 → that's the run identity. Cache keys per stage; orchestrator skips on hits.
4. **Universal DI seam: protocol + DataContexts + pure-data context + impl Config.** Plus protocol declares `disagreement_stance` (DISAGREEMENT_AWARE / RESOLVED / ER_DECISION_LENS / TARGET_DIRECT). Plus DataContext expressions can contain `Config.<field>` typed ConfigRef nodes substituted at compile.
5. **Knot-hosted impl source. Browser-authored. Trusted authors. Single-team.** No tenants. No sandboxing. No restricted Python. No multi-tenant defenses. External users only at four narrow surfaces (per goals.md).
6. **The impl IS the strategy.** One bound impl per (stage, class). No separate "strategy spec" entity. `bound_impls` table makes the binding explicit (PK: stage, class_name).
7. **Multi-valued canonical facts. Trust resolution is query-time. SDK type follows protocol stance.** Per-property multi-valued storage; trust-resolution CTE rewrites pre-execution per slot's `resolution_policy`. Under RESOLVED protocols slots type as `Resolved[T]` (comparison ops work); under DISAGREEMENT_AWARE as `MultiValued[T]` (bare comparison is a type error).
8. **Cross-class pinning.** Relation classes pin parent run hashes at compile.
9. **Source-layer contract. Knot starts at normalize.** Sources are team-owned lake declarations. No `SourceReader` interface; team owns ingestion.
10. **One unified expression tree.** Single Pydantic AST powers slot derivations, constraint bodies, ER signal slots in config, DataContext bodies, Translator queries. Real Pydantic refs throughout. Enums (not Literal strings) for variants. Derivation expression refs walk into compile network the same way `slot.range` does (per `staging/impact-analysis.md`).
11. **Polymorphic-reference principle.** Polymorphic class invisible to static dependency graph until consumer explicitly declares.
12. **Knot delegates execution. Four orthogonal QueryExecutor protocols.** QueryReader (sync SELECT → backend-specific handle, NOT necessarily Arrow), Materializer (async INSERT INTO target SELECT → handle + status), Introspector (column metadata), ViewManager (DDL).
13. **In-flight corrections via dedicated source.** `_user_corrections` postgres staging → `_user_corrections` source migration at next pipeline run; consumer-only translator overlay closes T1→T2 gap.
14. **Two-layer DQ: built-in bundle + DqRunner family.** DqNormalizeRunner (DISAGREEMENT_AWARE), DqResolveRunner (ER decision lens), DqMergeRunner (RESOLVED), DqPublishRunner (target-direct). Each pins its own lens.
15. **Free-form materialization.** Materialization is a bound DI impl. Same protocol pattern. Not per-class.
16. **Failure modes are loud.** `extra="forbid"` everywhere; reference resolution at parse time; DataContext registration validates against spec; Config edits cross-checked; ER conflicts surface for review by default; QueryExecutor errors raise typed exceptions, never swallow.
17. **Programmatic-first authoring; no YAML; no fork of LinkML/SHACL/OWL.** Spec authored via UI/API as Pydantic models. Vocabulary borrows from LinkML; no LinkML library dep, no YAML import/export, no SHACL escape hatch, no OWL DL reasoner.

## All staging docs (one-line each)

`design/staging/`:

- `artifacts.md` — Pipeline run artifacts (impl-side metadata; knot tracks, impl owns storage)
- `auto-generated-sdk.md` — SDK codegen mechanism; lens-aware emission
- `cross-class-pinning.md` — Relation classes pin parent run hashes; topological sort over ER dependency graph
- `datacontext-config-binding.md` — Symbolic `Config.<field>` refs in DataContext expressions, substituted at compile
- `derivation-and-constraints.md` — Derived slots; one expression tree powers forward + backward chain + validation
- `di-input-contract.md` — DataContext + Config + ctx + protocol DI seam shape
- `dq-design.md` — DqRunner protocol family per pipeline stage; built-in bundle
- `er-and-storage.md` — ER protocol semantics; multi-valued canonical fact storage
- `example-modeling-walkthrough.md` — End-to-end Movie/Person/Credit walkthrough
- `impact-analysis.md` — Single-dispatch visitors over typed entity tree
- `impl-contract.md` — **NEW.** What knot owns vs what impls own (the contract that took too many rounds)
- `impl-dependencies.md` — Deps NOT knot's concern; new library = redeploy; `runtime_image_identity` audit affordance only
- `incremental-execution.md` — Graph-level skip via per-stage content-addressed cache keys
- `multi-class-datacontexts.md` — DataContext.primary accepts list[OntologyClass] | DerivedSlot etc.; per-class fan-out at fulfill
- `multi-valued-semantics.md` — Lens-driven defaults via Protocol's `disagreement_stance`
- `ontology-modeling.md` — Subclassing rules; derived properties; SQL gen targets
- `pipeline-stages.md` — Pipeline flow; trigger model (knot is not a scheduler)
- `protocol-result-shapes.md` — Typed knot-controlled return shapes per protocol
- `query-executor.md` — QueryReader/Materializer/Introspector/ViewManager protocols
- `query-language-rationale.md` — Typed Pydantic AST as source-of-truth; Gremlin/Cypher as Translator emit targets only
- `seam-contract-pattern.md` — Universal DI pattern shape
- `spec-loading.md` — **OPEN.** Where does knot load spec from at boot? (current B2 hack; postgres-control vs filesystem path vs Python module)
- `spec-model.md` — Pydantic spec model definitions; OntologyClass/Slot/etc.
- `spec-versioning.md` — Canonical-dump algorithm; CANONICAL_DUMP_VERSION; RFC 8785 JCS
- `sql-generation.md` — sqlglot AST builder; per-policy trust-CTE reductions
- `ui-and-integrations.md` — Marquez UI direction; Netflix port-time integration plan
- (plus 4-6 others)

## The 14 design-thinking patterns (Nick's framework)

From `design/_meta/design-thinking-patterns.md`. Read in full for grounding.

1. Insist on real types over discriminator strings
2. Kill parallel meta-structures when the typed graph IS the graph
3. Interrogate every named entity for whether it earns its place
4. Collapse infrastructural layers when trust posture allows
5. Generalize through the universal pattern; don't over-specialize
6. First ask if the problem actually exists
7. Trust language primitives before adding system-level mechanisms
8. Comparative anchoring against named systems
9. No deferred-version framing (no v0/v1/future-work hedges)
10. Baby-step pacing for new concepts
11. Quick to redirect when an example overshoots
12. Cull claims that don't earn their cost
13. Insist on the single-team / no-tenants user model
14. Name the unresolved meta-questions explicitly

## The 8 personas (dispatch via Agent role-play)

From same doc:

1. **Seam Sharpener** — cuts; resists new mechanisms when existing seams cover
2. **Type Maximalist** — strings vs types; insists type system carry semantics
3. **Trust Posture Interrogator** — what conventional defenses are unnecessary?
4. **Reality Checker** — concrete scenarios; does the problem actually arise?
5. **Comparative Anchorer** — prior art across dbt/DJ/LinkML/SHACL/OWL/Splink/etc.
6. **Commitment Enforcer** — strips v1/future-work hedges; demands explicit commit-or-open
7. **User-Model Anchor** — single-team / no-tenants; rejects multi-team framings
8. **Pacing Critic** — too long/dense; demands baby steps

## Lake path conventions

```
{lake_dir}/
├── per_source_facts/<Class>/source=<X>/data.parquet
├── resolved_facts/<Class>/data.parquet
├── entity_bindings/<Class>/data.parquet                  (SCD2)
├── canonical_id_lineage/data.parquet                     (merge/split events)
├── er_outputs/<class>_scores.parquet                     (impl-owned; Round 3)
├── dq_outputs/merge_<run_id>.parquet                     (impl-owned; Round 3)
└── sources/                                              (team-owned ingestion)
    ├── imdb_movies.csv
    ├── tmdb_movies.csv
    ├── ...
```

(Note Round 3 dq_merge.py uses a different path that doesn't match orchestrator's hive layout — flagged in known drift.)

## Connection / credentials (test infrastructure only)

- **Postgres**: `postgresql://knot:knot@localhost:5432/knot_control` — env var: `KNOT_CONTROL_DSN`
- **Neo4j**: `bolt://localhost:7687`, auth `(neo4j, knottest)` — hardcoded in fixtures
- **Marimo**: http://100.65.247.65:2718 (Tailscale) — bound to 0.0.0.0; `--no-token` (single-team)

## The 4 external user surfaces (per goals.md "Who uses knot")

1. **Read materialized outputs directly** — apps/services hit Neo4j / Iceberg / vector store / parquet exports the team's bound Materialization impls produce
2. **Lake query via knot's built-in API** — knot's own translator endpoint over the lake (no bound impl required; sql_gen + QueryReader)
3. **Materialized-target query via Translator impl** — only when target speaks non-SQL or different schema (Cypher, Gremlin, vector similarity)
4. **Submit corrections via UI** — `_user_corrections` postgres staging; migrates to lake at next pipeline run via dedicated source

## Decisions explicitly REJECTED (with reason)

- **Per-impl `requires` deps tracking** — Lambda/Workers/@task.virtualenv reflex; multi-tenant SaaS pattern. Single-team posture: new library = redeploy.
- **`submitted_by` per-actor attribution columns** — multi-tenant audit reflex. Team uses git for attribution.
- **Lockfile in postgres-control as first-class artifact** — knot doesn't manage Python deps. Period.
- **Knot stores impl filesystem layout / parquet conventions** — impls own outputs.
- **Knot-owned data persistence with embedded Arrow Tables in Result types** — Results are metadata only (`output_uri: str` opaque).
- **Hardcoded `pyarrow.Table` in QueryReader Protocol** — backend-agnostic; bound impl decides handoff format.
- **Gremlin/Cypher as source-of-truth query language** — typed Pydantic AST is the authoring substrate; Gremlin/Cypher only at Translator emit boundary.
- **Browser-author + runtime-spec coordination** as architectural concern — collaboration is impl detail; Google-Docs-style or last-write-wins, not a knot-level seam.
- **Spec edit concurrency** — race resolution is impl detail under single-team posture.
- **Per-impl venv materialization at compile/run** — multi-tenant SaaS shape; single-team uses one runtime image.
- **Auth/CORS/rate-limit/JWT/OAuth on the API surface** — single-team network gates access.
- **Sandboxing of impl source `exec()`** — trusted-author posture per commitment 5.
- **Open-ecosystem interop (RDF/SHACL/OWL/LinkML)** as a goal — non-goal unless the team specifically chooses (per commitment 17).
- **Marketplace / plugin ecosystem / third-party impl distribution** — knot is one team's tool, not a platform.

## Common pitfalls (caught and codified)

- **Workers go silent post-completion.** SendMessage isn't reliable on finish. Always check files + tests + git status, don't trust `task-notification` to come.
- **Conftest schema-shadowing.** `tests/integration/conftest.py` MUST NOT define a no-op `_apply_control_schema`. The session-scoped fixture in `tests/conftest.py` applies the schema; overriding it silently breaks integration tests on a clean stack.
- **Stale stack illusion.** Tests pass on a stack that was already-up because schema persists across runs. ALWAYS verify on a clean stack: `down + up + wait-ready + pytest`.
- **Marimo `_`-prefixed cross-cell.** Variables prefixed with `_` are cell-private in marimo; they can't cross cells. Returns must use unprefixed names.
- **Pydantic v2.11 `instance.model_fields`** is deprecated; use `type(instance).model_fields`.
- **Pydantic models lack `__hash__`** by default — can't use them as dict keys directly. Either add `__hash__`, use `id()`, or switch to `list[tuple]` shape.
- **Multi-tenant smell rule must go on every dispatch** — the only thing that consistently catches the SaaS reflex.
- **Notebook quality is fragile.** Always verify `python notebook.py` exits 0 before claiming done.

## Common workflows (exact commands)

```bash
# Verify suite on clean stack
cd /mnt/main/code/knot
bash scripts/down.sh && bash scripts/up.sh && bash scripts/wait-ready.sh
.venv/bin/pytest tests/ 2>&1 | tail -3
bash scripts/down.sh

# Verify a notebook runs
.venv/bin/python notebooks/01_progress.py 2>&1 | tail -5; echo exit: $?

# Build mkdocs site
cd site && /mnt/main/code/knot/.venv/bin/mkdocs build -d /tmp/knot-build-check 2>&1 | tail -5

# Launch FastAPI
.venv/bin/uvicorn knot.api:app --host 0.0.0.0 --port 8000

# Launch marimo (detached)
nohup setsid .venv/bin/marimo edit notebooks/ \
  --host 0.0.0.0 --port 2718 --headless --no-token \
  --skip-update-check --allow-origins '*' --watch \
  > /tmp/marimo.log 2>&1 < /dev/null &
disown

# Generate fixture data
.venv/bin/python -m tests.fixtures.generator A1
.venv/bin/python -m tests.fixtures.generator B2
.venv/bin/python -m tests.fixtures.generator C2

# Install all deps
.venv/bin/pip install -e '.[runtime,test,notebook,dev]'
```

## Worker dispatch playbook

**Brief structure:**
1. Top-of-prompt **multi-tenant smell rule** (literally; copy verbatim)
2. Required reading (specific file paths, including impl-contract.md, design-thinking-patterns.md)
3. Task description (≤ 1 paragraph)
4. Specific changes (with file paths and code shapes)
5. Tests to add (with names + assertions)
6. Verification command — **MUST verify on clean stack** (down + up + tests + down)
7. Hard rules (single-team, no v0/v1, real types, don't bloat)
8. Output format (bounded length report)

**Subagent type selection:**
- `oh-my-claudecode:executor` — implementation work; full tool access
- `general-purpose` — read-only audit / brainstorm / persona role-play
- Avoid `Plan` for implementation; it can't write

**Parallel vs serial:**
- Independent slices in parallel (Round 1: API + Reader + Materializer)
- Dependent slices serial (Round 2 needs Round 1; Round 3 needs Round 2's orchestrator)
- Within a round, all 3-4 workers fire at once via `run_in_background: true`

**Reviewer pattern:**
- After every round: dispatch a `general-purpose` agent role-playing **Nick**
- Reviewer reads `_meta/design-thinking-patterns.md` first to internalize voice
- Reviewer must run notebook, run tests on clean stack, write verdict to `/tmp/nick-review-roundN.md`
- Verdict is PASS / FAIL with concrete file:line complaints
- FAIL → fix-up dispatch addressing specific complaints; verify; re-review

## xfail test inventory (17 total)

`tests/integration/test_ontology_expansion.py` — 16 xfail-strict tests waiting on:
- knot.compiler dispatch path for ontology mutations
- knot.spec_loader (rename_class, add_class, add_source, add_derivation runtime semantics)
- knot.run_store walk-back queries

`tests/integration/test_env_smoke.py::test_run_full_pipeline_xfail` — waiting on `TestEnv.run_full_pipeline()` which depends on the toy orchestrator + bound impls fully wired (Round 3+ partially landed; not yet TestEnv-callable).

These convert to passing in Round 4 + Round 5 as the missing pieces land.

## Tools available (Claude Code harness)

- `Agent` — dispatch subagent; supports `team_name`, `name`, `run_in_background`
- `Bash`, `Read`, `Write`, `Edit`, `Grep`, `Glob`, `TodoWrite`
- `TeamCreate` / `TeamDelete` / `SendMessage` — team coordination
- `ToolSearch` — load deferred tool schemas
- `Skill` — invoke OMC skills (`/oh-my-claudecode:team` etc.)

## Recovery checklist (after compaction)

1. Read this STATE.md in full
2. Read `design/staging/impl-contract.md`
3. `git pull` (likely already on `main` head)
4. Verify on clean stack: `down + up + wait-ready + pytest tests/ → 325+ passed expected`
5. Verify notebook: `python notebooks/01_progress.py` exit 0
6. Check `/tmp/drift-audit.md` for known issues if file persists; otherwise re-dispatch the audit
7. Check status of in-flight contract-fix worker (it may have completed; check for new commits, look for `output_uri:` in `src/knot/protocols.py`)
8. Continue with remaining rounds (4: translator + correction overlay; 5: audit walk-back end-to-end integration test)
9. Build comprehensive walkthrough notebook (`notebooks/02_walkthrough.py`) per the plan in this doc
10. Maintain Nick-reviewer cadence per round

