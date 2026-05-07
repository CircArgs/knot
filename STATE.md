# knot — full state handoff

Comprehensive snapshot of the `heavily-involved` branch.  Read this first
in a fresh session.  Everything that's been built, everything that's been
designed, every lever that may pivot.

**Branch:** `heavily-involved` at `75efc09` (pushed to `origin`).
**Repo:** `/mnt/main/code/knot/` · GitHub `CircArgs/knot`.

---

## Top-of-mind: pivot levers

You've signaled these are potentially up for change.  Treat as **open**, not
committed:

1. **Bindings on Spec or external?**
   - You leaned: fold `Binding(stage, class_name, impl_name, config, data_contexts)`
     onto `Spec` so there's ONE draft and ONE publish gate covering ontology
     + bindings together.
   - Tradeoff: changing one threshold rewrites the whole spec_revisions row
     (cheap; JSONB).  But content_hash conflates ontology + tuning concerns.

2. **DataContexts: class-level vs postgres-stored map**
   - Today (in the demo impls): `ClassVar[DataContext]` attribute on the
     impl class body.
   - You proposed: lift them out into a postgres-stored
     `dict[str, Expression]` keyed by table name, alongside Config.  Each
     expression produces one materialized table that knot serves to the
     impl by attribute-name kwarg.
   - Wins: kills the `exec()`-source-into-fresh-namespace pattern; impls
     become deployed code referenced by `impl_name` opaque label.

3. **Impl source: knot-hosted vs deployed**
   - Today (per `core-design.md` commitment 5): "Knot-hosted impl source.
     Browser-authored.  Trusted authors."  POST /impls/.../revisions
     stores Python bytes in postgres; orchestrator `exec()`s into a fresh
     namespace.
   - You proposed dropping this.  Impls are deployed code; only the
     **runtime DI surface** (Config + DataContexts) is API-mutable.
     Algorithmic changes go through whatever review/CI the team has.
   - Affects commitment 5, `impl-contract.md`, `di-input-contract.md`,
     `core-design.md` § 4.

4. **"Spec" naming**
   - Status quo.  You questioned: should it be "Pipeline"?
   - Pushback I gave: Pipeline implies execution flow; that's
     `WorkflowSpec` (compile output).  "Project" is dbt-shaped.  No name
     is load-bearing.

5. **Publish gate completeness**
   - Today: steps 1+2 only (Pydantic shape + reference resolution across
     spec graph).
   - Goal: steps 3+4 (DataContext cross-checks per registered impl,
     impact preview against bindings) once the modeling router lands.

6. **Spec mutability — drafts**
   - Today: implemented.  `POST /spec/drafts` creates branched-from-published
     drafts; `POST /spec/drafts/{id}/{entity}` mutates; `POST /spec/drafts/{id}/publish`
     runs gate + atomically promotes.
   - You confirmed this is the right model: every spec mutation lives on
     a draft.  No auto-publish.

---

## What knot is

A reflective ontology compiler for **one team** (commitment 5: trusted
authors, no tenants).  Knot owns the **spec** (typed Pydantic ontology
declaration) and **compile** (Spec → WorkflowSpec).  The team owns the
**impls** (bound DI implementations: ER, materialization, DQ, etc.) and
**execution** (Maestro / Airflow / our toy mimic dispatches the
WorkflowSpec).

Two graphs — different shapes, different lifecycles:

| | Spec graph | Workflow graph |
|---|---|---|
| What | Declarative ontology + bindings | Operational DAG of stages |
| Nodes | OntologyClass, Slot, TypeDefinition, Source, Constraint, Binding | StageSpec(kind, class_name, source_name, …) |
| Edges | slot.range → class; binding.cross_references → class | Intra-class stage order + inter-class dependency |
| Identity | `spec_revisions.revision` (postgres) | `compile_hash = sha256(canonical_dump)` (immortal) |
| Mutability | Authored via API; revisioned; draft → publish | Immutable; emitted fresh by compile |

`compile()` is the bridge — pure function, no execution, no I/O.

External users enter at four narrow surfaces (per `goals.md` "Who uses knot"):
1. Read materialized outputs directly (their app reads Neo4j/Iceberg/etc.)
2. Lake query via knot's built-in API (sql_gen + QueryReader)
3. Materialized-target query via Translator impl (Cypher/Gremlin/SPARQL)
4. Submit corrections via UI

---

## Repo layout (post-restructure)

```
knot/                              ← repo root
├── src/
│   ├── knot/                      ← core library (publishable)
│   │   ├── __init__.py
│   │   ├── metaschema.py          spec graph models + SDK affordances
│   │   ├── canonical.py           canonical_dump + compute_content_hash (RFC 8785 JCS, cycle-safe)
│   │   ├── sql_gen.py             single-dispatch expression-tree → sqlglot AST
│   │   ├── spec_store.py          full-fidelity serialize, two-pass UID-based rehydration, draft lifecycle
│   │   ├── control_db.py          apply_schema(dsn)
│   │   ├── control_schema.sql     just spec_revisions table (modeling tables not yet present)
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── main.py            FastAPI app + bootstrap (apply_schema + seed B2 if no published rev)
│   │   │   └── spec.py            /spec/* router — drafts, publish, published reads
│   │   └── orchestrator/
│   │       ├── __init__.py
│   │       └── toy_maestro/
│   │           ├── __init__.py
│   │           ├── workflow_models.py    Pydantic shapes matching Maestro JSON
│   │           ├── storage.py            postgres tables: maestro_workflows, _runs, _step_runs
│   │           ├── dispatcher.py         in-process synchronous DAG runner
│   │           ├── executors/sql_step.py executes sparksql/trino sub_types via DuckDB
│   │           └── app.py                FastAPI app on /maestro/api/v3/*
│   │
│   ├── knot_demo_a1/              demo deployment A1 (single source, one class)
│   ├── knot_demo_b2/              demo deployment B2 (3 classes, 19 slots, 8 sources, 6 impls)
│   │   ├── __init__.py
│   │   ├── spec.py                ontology + Source declarations
│   │   └── impls/
│   │       ├── er_movie.py / er_person.py / er_credit.py    (rapidfuzz ER)
│   │       ├── neo4j_publisher.py / iceberg_publisher.py    (materializers)
│   │       └── dq_merge.py        (UNIQUE_OR_FAIL DQ runner)
│   └── knot_demo_c2/              demo deployment C2 (richer; Movie/Series/Episode/Game/Person/Credit)
│
├── data/
│   ├── A1/sources/imdb_movies.csv
│   ├── B2/
│   │   ├── sources/   8 CSVs (imdb/tmdb/wikidata × movies/persons/credits)
│   │   ├── expected_facts.yaml
│   │   └── edge_cases.yaml
│   ├── C2/sources/    25 CSVs
│   └── A2/A3/B1/B3/C1/C3/   stub READMEs (template tiers)
│
├── tests/
│   ├── conftest.py                postgres_dsn, neo4j_driver, _apply_control_schema autouse
│   ├── test_smoke.py              postgres + neo4j + apoc reachability
│   ├── test_env.py                TestEnv harness (mostly NotImplementedError today; rebuild target)
│   └── integration/
│       └── test_toy_maestro.py    8 tests (workflow register/start/poll/multi-step)
│
├── design/                        canonical + staging design docs
│   ├── README.md
│   ├── goals.md                   17-section vision
│   ├── core-design.md             17 architectural commitments (the canon)
│   ├── knot-as-compiler.md        ← read first; bridges spec graph and workflow graph
│   ├── compiled-workflow-hashing.md
│   ├── source-layer-contract.md
│   ├── trust-and-merge.md
│   ├── reification-strategy.md
│   ├── _meta/
│   │   ├── code-style-preferences.md
│   │   └── design-thinking-patterns.md   14 patterns + 8 personas
│   └── staging/                   ~30 design docs (read on demand)
│
├── tools/
│   ├── generator.py               parameterized synth-data generator for fixture tiers
│   └── tier_configs.py
│
├── docs/
│   ├── EDGE-CASES.md              17 categories of seeded edge cases
│   └── data-tier-README.md        9-tier matrix overview
│
├── scripts/
│   ├── up.sh                      docker-compose up postgres + neo4j
│   ├── down.sh
│   └── wait-ready.sh
│
├── docker-compose.yml             postgres:16 + neo4j:5-community (apoc), tmpfs (ephemeral)
├── pyproject.toml                 declares 4 packages: knot + knot_demo_a1/b2/c2
├── CLAUDE.md                      project conventions
└── STATE.md                       this file
```

---

## Demo deployment shapes

### B2 (the workhorse — main demo, full impls)

- 3 classes: **Movie**, **Person**, **Credit**.  Credit references Movie + Person via slot.range (relation class; pins parent runs).
- 19 slots total: 15 stored + 4 derived (Movie.director / actors / writers / producers from Credit.role).
- 8 sources: imdb/tmdb/wikidata × movies/persons/credits (3 + 2 + 3 = 8 — Person has only imdb + tmdb).
- 6 impls under `src/knot_demo_b2/impls/`:
  - ER × 3: er_movie, er_person, er_credit (rapidfuzz title/name fuzz + year tolerance + identifier-cross-ref signals)
  - Materializers × 2: neo4j_publisher (UNWIND/MERGE), iceberg_publisher (Spark-like)
  - DQ runner: dq_merge (CrossSourceAgreementCheck for UNIQUE_OR_FAIL slots)
- Identifier slot for both Movie and Person is named `imdb_id` — these are **different Slot objects** (UID-based serializer/deserializer handles this; same-name Slot collision was a real bug we fixed).
- Edge-case categories seeded (see `data/B2/edge_cases.yaml`): cross-source disagreement, normalization conflicts, ER cascading, role-case variants, missing-coverage, etc.
- Expected post-pipeline state in `data/B2/expected_facts.yaml`.

### C2 (richer; not yet wired end-to-end)

Movie + Series + Episode + Game + Person + Credit + Studio + Award + Identifier + Country.  Tests the multi-class polymorphic identifier pattern (`Identifier(class_slot=entity_class, key_slot=entity_src_key)`) and richer ER (Series-level + cross-target identifier refs).

### A1 (minimal smoke)

One class (Movie), one source (imdb_movies), one impl (iceberg_publisher).  Sanity baseline.

---

## What's built — per component

### `src/knot/metaschema.py` (~700 lines)

The full spec graph as typed Pydantic models with **real Python object refs** (commitment 2).

| Section | Contents |
|---|---|
| 1. SpecBase | `extra="forbid"`, `arbitrary_types_allowed=True`, `frozen=False` (in-place mutation during pass-2 rehydration) |
| 2. Enums | `ResolutionPolicy` (ARGMAX_TRUST, MODE, WEIGHTED_VOTE, MEDIAN_NUMERIC, LATEST_WATERMARK, UNIQUE_OR_FAIL), `Severity`, `CompareOp`, `BoolOpKind`, `AggFunc`, `GroupByMode`, `ReferenceKind` |
| 3. Leaf | `TypeDefinition`, `PermissibleValue` |
| 4. Expression tree | `Literal_`, `SlotPath`, `Compare`, `BoolExpr`, `Within`, `Between`, `Matches`, `RelationRef`, `FilteredRelation`, `RelationProject`, `RelationCount`, `RelationAggregate`, `RelationAny`, `RelationAll`, `RelationFirst`, `RecursiveTraversal`, `ScalarDerivation`, `FormatDerivation`.  All inherit `_BoolComposable` mixin for `&`/`|`/`~` operators. |
| 5. Slot | Operator overloads (`__gt__`, `__lt__`, `__eq__`, `__hash__`) + SDK methods (`.in_()`, `.within()`, `.between()`, `.matches()`, `.starts_with()`, `.ends_with()`, `.is_null()`, `.is_not_null()`, `.from_source()`).  Operators emit Compare/Within/Between/Matches nodes with `_sentinel_class` as `from_class` placeholder. |
| 6. Reference patterns | `DirectRef`, `DiscriminatedRef`, `IdentifierPattern`, `UniqueKey` |
| 7. OntologyClass | `__getattr__(item)` resolves slot names via `_class_chain()` (self + is_a + mixins, BFS).  `__hash__` by id. |
| 8. Constraint | name + primary class + body (expression tree) + severity + message |
| 9. Source | name + entity_class + identifier_slot + description |
| 10. Spec | id, version, classes, slots, types, sources, constraints, prefixes, default_range |
| 11. model_rebuild | resolves forward refs at module bottom |

The **SDK** is the metaschema entities themselves.  No two-class generation
per `auto-generated-sdk.md` — that's reserved for protocol-kind-aware lens
emission later.  `Movie.year > 1900` → `Compare(op=GT, left=SlotPath(slots=[year]), right=Literal_(value=1900))`.

`_sentinel_class = OntologyClass(name="__sentinel__")` is the placeholder
for Slot operator overloads' `from_class`.  Replaced at fulfill time by
the surrounding DataContext.primary or Constraint.primary.  Never stored
in a published spec.

### `src/knot/canonical.py` (~200 lines)

`CANONICAL_DUMP_VERSION = 2`.  RFC 8785 JCS via `jcs` library.

- `_RUNTIME_FIELDS` frozenset: `description` on display-bearing classes (OntologyClass, Slot, SlotOverride, PermissibleValue, TypeDefinition, Constraint, Source); Spec envelope authoring metadata (created_at, last_modified, author, revision_id, display_label).  Stripped from canonical bytes.
- Cycle handling: named SpecBase nodes tracked by `id()`; first visit emits full body, later visits emit `{"$ref": <name>}`.  Note: this scheme has a name-collision bug for HASH stability when two same-named entities exist (e.g. Movie.imdb_id vs Person.imdb_id) — first emits full, second also emits full (different ids), then later refs to either resolve to the FIRST.  For canonical hashing this still produces deterministic bytes per traversal order, so the hash is stable.  But it's brittle.  See spec_store for the fix.
- `compute_content_hash(spec)` → bare 64-char hex.

### `src/knot/sql_gen.py` (~370 lines)

Single-dispatch visitor `to_sqlglot(node)` over the expression tree.

| Public | What |
|---|---|
| `emit_sql(node, dialect=)` | render as SQL (DuckDB / Trino / Spark) |
| `emit_validation_query(constraint, dialect=)` | uniform `(rule_id, class_name, slot_name, offending_pk, detail)` SELECT.  Empty result = pass. |
| `emit_trust_resolved_cte(cls, dialect=)` | `WITH __trust_resolved__<Class> AS (...)` per per-slot ResolutionPolicy |
| `UnsupportedDerivationError` | raised by `RecursiveTraversal` handler — recursive CTEs rejected per `sql-generation.md` expressivity bound |

Per-policy reductions: `ARGMAX_TRUST` (Trino/DuckDB: argmax; Spark: max_by), `MODE`, `WEIGHTED_VOTE` (argmax over sum), `MEDIAN_NUMERIC` (percentile_cont 0.5 within group), `LATEST_WATERMARK` (argmax by asserted_at), `UNIQUE_OR_FAIL` (CASE … COUNT DISTINCT … ERROR(…)).

### `src/knot/spec_store.py` (~600 lines)

Persistence + rehydration with a UID-based cycle scheme.

**Serialization** (`spec_to_dict(spec) → dict`):
- `_SerCtx` per-call counter assigns a unique `$uid: int` to each named SpecBase node (TypeDefinition, Slot, OntologyClass, Source, Constraint).
- First visit emits full body with `$kind` + `$uid` + fields.
- Repeat visits emit `{"$ref": <uid>, "$kind": <class>}`.
- Inline-only nodes (expression tree) always emit fully.
- Names collide-safely because `$ref` keys on UID, not name.

**Two-pass rehydration** (`spec_from_dict(d) → Spec`):
- Pass 1 (`_pass1_build`): walks JSON tree recursively, creates name-only placeholder objects keyed by uid for TypeDefinition / Slot / OntologyClass.  Source + Constraint placeholders deferred to pass 2 (cross-ref-rich).
- Pass 2 (`_resolve`): walks again, resolves `$ref` via uid index.  Inline definitions with `$uid` patch the existing placeholder (preserves identity).  Non-uid nodes (expression tree) construct fresh.
- Result: `spec.sources[0].entity_class is spec.classes[0]` after rehydrate.  Hash invariant under round-trip.

**Draft lifecycle**:
- `seed_from_fixture(conn)` — imports `knot_demo_b2.spec`, writes as published rev 1.  Idempotent if already seeded.
- `get_published(conn) -> Spec | None`
- `get_revision(conn, rev) -> Spec`
- `list_drafts(conn) -> list[dict]` / `list_published(conn) -> list[dict]`
- `create_draft(conn, parent_revision=None, label=None) -> int` — branches from currently-published or specified parent.
- `update_draft(conn, draft_id, spec)` — UPDATE in place.  Raises `DraftAlreadyPublishedError` if the row is published.
- `publish_draft(conn, draft_id) -> int` — runs `publish_gate(spec)` then atomically flips published flags via single UPDATE statement using `SET published = (revision = $1)` with `WHERE revision = $1 OR published = TRUE`.
- `discard_draft(conn, draft_id)` — DELETE only on unpublished rows.

**Publish gate** (`publish_gate(candidate: Spec) → None | raises PublishGateError`):
- Step 1 (Pydantic shape): implicit at construction; redundant recheck via `model_dump → model_validate` doesn't work on cyclic graph, so we just type-check `isinstance(candidate, Spec)`.
- Step 2 (reference resolution): every `Slot.range`, every `OntologyClass.slots[i]`, every `Source.entity_class`, every `Source.identifier_slot` (must be on `entity_class.slots`), every `Constraint.primary` must be `is`-identical to an entity on `spec.classes` / `spec.slots` / `spec.types`.  Aggregates errors across the whole spec, raises one PublishGateError with all messages.
- Steps 3 + 4 (DataContext cross-checks, impact preview): land with the modeling router; same `publish_gate` function gains them.

### `src/knot/control_schema.sql` (~30 lines)

Just the spec router scope:

```sql
CREATE TABLE spec_revisions (
    revision         SERIAL PK,
    spec             JSONB NOT NULL,
    content_hash     CHAR(64) NOT NULL,
    published        BOOLEAN NOT NULL DEFAULT FALSE,
    parent_revision  INT REFERENCES spec_revisions(revision),
    label            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at     TIMESTAMPTZ
);

-- Exactly one row at a time may have published=TRUE.
CREATE UNIQUE INDEX spec_revisions_one_published
    ON spec_revisions (published) WHERE published = TRUE;
```

Modeling-router tables (`compiled_workflows`, `pipeline_runs`, `bound_impls`, `impl_revision`, `impl_config`, `_user_corrections`, `_user_er_decisions`) **do not exist** on this branch.  They land with the modeling router.

Toy Maestro has its own tables: `maestro_workflows`, `maestro_workflow_runs`, `maestro_step_runs` (in the same `knot_control` database; not in the spec router schema file).

### `src/knot/api/spec.py` (~470 lines)

APIRouter mounted at `/spec`.

| Endpoint | What |
|---|---|
| `GET /spec/published` | full active spec (cycle-safe JSON via spec_to_dict) |
| `GET /spec/published/classes` | summaries (name, slot_names, is_a, mixins, abstract) |
| `GET /spec/published/classes/{name}` | one class summary |
| `GET /spec/published/slots` | summaries (name, range_kind, range_name, identifier, required, multivalued, resolution_policy) |
| `GET /spec/published/types` | summaries |
| `GET /spec/published/sources` | summaries |
| `GET /spec/revisions` | published revisions newest-first |
| `GET /spec/revisions/{n}` | full spec at revision n |
| `GET /spec/drafts` | draft summaries |
| `POST /spec/drafts` | create (parent_revision, label both optional) |
| `GET /spec/drafts/{id}` | full draft spec |
| `DELETE /spec/drafts/{id}` | discard (404 if published) |
| `POST /spec/drafts/{id}/types` | add TypeDefinition |
| `POST /spec/drafts/{id}/slots` | add Slot (range_kind: "type"\|"class"\|null) |
| `POST /spec/drafts/{id}/classes` | add OntologyClass (slot_names refs existing) |
| `PATCH /spec/drafts/{id}/classes/{name}` | update slot_names / is_a / mixins / abstract / description |
| `POST /spec/drafts/{id}/sources` | add Source (entity_class_name, identifier_slot_name) |
| `POST /spec/drafts/{id}/publish` | run gate; on pass, atomically promote |

Every mutation request is `extra="forbid"`.  Mutations return `MutationResponse(draft_revision, content_hash, spec_summary)`.  Publish returns `PublishResponse(revision, content_hash, published_at)`.

`set_dsn(dsn)` injection point so `main.py` can override at app construction.

### `src/knot/api/main.py`

```python
from knot.api import spec as spec_router_mod
from knot.control_db import apply_schema
from knot import spec_store

DSN = os.environ.get("KNOT_CONTROL_DSN", "postgresql://knot:knot@localhost:5432/knot_control")

def _bootstrap():
    apply_schema(DSN)
    with psycopg.connect(DSN, autocommit=True) as conn:
        if spec_store.get_published(conn) is None:
            spec_store.seed_from_fixture(conn)

_bootstrap()
spec_router_mod.set_dsn(DSN)

app = FastAPI(title="knot")
app.include_router(spec_router_mod.router)
```

Run: `uvicorn knot.api.main:app --host 0.0.0.0 --port 8000`.  Swagger at `/docs`.

### `src/knot/orchestrator/toy_maestro/` (~600 lines)

Standalone FastAPI service mimicking Netflix Maestro for lake-query workflows.  **Not** wired into knot.compile() — that's modeling router scope.

| File | What |
|---|---|
| `workflow_models.py` | Pydantic `Workflow`, `Step`, `WorkflowStatus` enum, `StepStatus` enum, `RunInstance`, `RunCtx`.  Status enums verbatim from Maestro Java enums (`CREATED`, `IN_PROGRESS`, `TIMED_OUT`, `STOPPED`, `FAILED`, `SUCCEEDED` for workflow; 22-value set for step). |
| `storage.py` | postgres tables `maestro_workflows`, `_workflow_runs`, `_step_runs` |
| `dispatcher.py` | `ToyMaestro.register_workflow / start_run (synchronous BFS topological DAG, fail-fast) / get_run / step_executor` |
| `executors/sql_step.py` | `sparksql_step` and `trino_step` sub_types — execute via DuckDB; capture row count + duration; `{param}` substitution from `run_params` |
| `app.py` | FastAPI app at `/maestro/api/v3/workflows/*` matching Maestro's URL structure |

Run on a separate port: `uvicorn knot.orchestrator.toy_maestro.app:app --port 8001`.

What it implements: workflow registration, run trigger (synchronous), run/step status, two SQL sub_types, multi-step topological DAG, fail-fast halt, run_params substitution, full Maestro status vocabulary, postgres persistence.

What it explicitly skips: foreach/while/subworkflow/template steps, retry policy, time/signal triggers, tag permits, pause/stop actions, Notebook step, async execution.  None of that is hedged — all openly skipped.

Live: `GET http://localhost:8001/maestro/health` → `{"status": "ok"}`.

### `src/knot_demo_b2/`

The fixture deployment we keep returning to.  Imports `knot.metaschema` to declare the ontology + sources; imports `knot.protocols` for impls (which **doesn't exist on this branch yet** — modeling router scope).

`spec.py`:
- 4 TypeDefinitions (`string`, `integer`, `float`, `datetime`)
- 19 Slots (3 named `imdb_id` actually live: Movie's, Person's, plus various source-specific identifier slots)
- 3 OntologyClasses (Movie, Person, Credit; Credit has slot.range references to Movie + Person making it a relation class)
- 4 derived slots on Movie via `RelationProject` (director, actors, writers, producers — all from Credit filtered by role)
- 8 Sources at module scope, also listed on `spec.sources`

`impls/` — broken until `knot.protocols` lands.  These define the **rebuild target** for the modeling router (what shape the protocols must support).

### `tools/`

`generator.py` — parameterized synth-data generator producing CSVs + edge_cases.yaml + expected_facts.yaml for each tier.  Reads `tier_configs.py`.

Run: `python -m tools.generator B2` to regenerate fixtures (won't break since the CSVs are deterministic).

---

## Live verification checkpoints

```bash
cd /mnt/main/code/knot

# Stack
bash scripts/down.sh && bash scripts/up.sh && bash scripts/wait-ready.sh

# All tests
.venv/bin/pytest tests/ -q
# Expected: 11 passed (3 smoke + 8 toy_maestro)

# Round-trip + hash invariant
.venv/bin/python -c "
from knot_demo_b2 import spec as b2
from knot.canonical import compute_content_hash
from knot.spec_store import spec_to_dict, spec_from_dict
rt = spec_from_dict(spec_to_dict(b2.spec))
print('invariant:', compute_content_hash(b2.spec) == compute_content_hash(rt))
print('identity:', next(s for s in rt.sources if s.name=='imdb_movies').entity_class is next(c for c in rt.classes if c.name=='Movie'))
"
# Expected: invariant: True; identity: True

# Spec API live
.venv/bin/uvicorn knot.api.main:app --port 8000 &
sleep 4
curl -s http://localhost:8000/spec/published/sources | python3 -c "import sys,json; print(len(json.load(sys.stdin)))"
# Expected: 8

# Toy Maestro live
.venv/bin/uvicorn knot.orchestrator.toy_maestro.app:app --port 8001 &
sleep 3
curl -s http://localhost:8001/maestro/health
# Expected: {"status":"ok"}
```

---

## The 17 architectural commitments (canonical reference)

From `design/core-design.md`.  Single source of truth.  Several are
candidates for amendment given your pivot signals (especially 5).

1. **Knot is a compiler.** No internal queue, scheduler, SQL engine, or HTTP loop touching data.  Bound DI interfaces are the boundary.
2. **Spec-as-data, with real Pydantic object references in-memory.** Refs are real Python object refs, not name-strings.  Strings live at the persistence boundary only.
3. **Content-addressed compile hashes are run identity.** Every workflow trigger = fresh compile; sha256 of canonical_dump.  Cache keys per stage.
4. **Universal DI seam: protocol + DataContexts + pure-data context + impl Config.** Plus protocol declares `disagreement_stance` (DISAGREEMENT_AWARE / RESOLVED / ER_DECISION_LENS / TARGET_DIRECT).
5. **Knot-hosted impl source. Browser-authored. Trusted authors. Single-team.** ← **YOU SIGNALED THIS MAY CHANGE**: impls become deployed code; only Config + DataContexts are runtime-mutable.
6. **The impl IS the strategy.** One bound impl per (stage, class).  No separate "strategy spec" entity.
7. **Multi-valued canonical facts. Trust resolution is query-time. SDK type follows protocol stance.** `Resolved[T]` under RESOLVED protocols; `MultiValued[T]` under DISAGREEMENT_AWARE.
8. **Cross-class pinning.** Relation classes pin parent run hashes at compile.
9. **Source-layer contract. Knot starts at normalize.** Sources are team-owned lake declarations.
10. **One unified expression tree.** Single Pydantic AST powers slot derivations, constraints, ER signals, DataContexts, Translator queries.
11. **Polymorphic-reference principle.** Polymorphic class invisible to static graph until consumer declares.
12. **Knot delegates execution. Four orthogonal QueryExecutor protocols.** QueryReader (sync SELECT → backend handle), Materializer (async INSERT INTO target SELECT), Introspector (column metadata), ViewManager (DDL).
13. **In-flight corrections via dedicated source.** `_user_corrections` postgres staging → migrated to lake at next pipeline run.  Consumer-only translator overlay closes T1→T2 gap.
14. **Two-layer DQ: built-in bundle + DqRunner family.** Normalize/Resolve/Merge/Publish DQ runners.
15. **Free-form materialization.** Materialization is a bound DI impl; not per-class.
16. **Failure modes are loud.** `extra="forbid"` everywhere; reference resolution at parse time; ER conflicts surface for review.
17. **Programmatic-first authoring; no YAML; no fork of LinkML/SHACL/OWL.** Spec authored via UI/API as Pydantic models.

---

## The 14 design-thinking patterns

From `design/_meta/design-thinking-patterns.md`.

1. Insist on real types over discriminator strings
2. Kill parallel meta-structures when the typed graph IS the graph
3. Interrogate every named entity for whether it earns its place
4. Collapse infrastructural layers when trust posture allows
5. Generalize through the universal pattern; don't over-specialize
6. First ask if the problem actually exists
7. Trust language primitives before adding system-level mechanisms
8. Comparative anchoring against named systems (dbt, Bazel, Terraform, Maestro)
9. **No deferred-version framing** — no v0/v1/future-work hedges; either committed or marked openly open
10. Baby-step pacing for new concepts
11. Quick to redirect when an example overshoots
12. Cull claims that don't earn their cost
13. **Insist on the single-team / no-tenants user model**
14. Name the unresolved meta-questions explicitly

## The 8 personas (dispatch via Agent role-play)

1. **Seam Sharpener** — cuts; resists new mechanisms when existing seams cover
2. **Type Maximalist** — strings vs types; insists type system carry semantics
3. **Trust Posture Interrogator** — what conventional defenses are unnecessary?
4. **Reality Checker** — concrete scenarios; does the problem actually arise?
5. **Comparative Anchorer** — prior art across dbt/DJ/LinkML/SHACL/OWL/Splink/etc.
6. **Commitment Enforcer** — strips v1/future-work hedges; demands explicit commit-or-open
7. **User-Model Anchor** — single-team / no-tenants; rejects multi-team framings
8. **Pacing Critic** — too long/dense; demands baby steps

---

## What's NOT yet built (modeling router + downstream)

These are out of scope for `heavily-involved` so far.  Land in modeling router.

1. **`knot.protocols`** — DI Protocol classes:
   - `Protocol` base + `ProtocolKind` enum (DISAGREEMENT_AWARE, RESOLVED, ER_DECISION_LENS, TARGET_DIRECT)
   - `ERProtocol`, `MaterializerProtocol`, `TranslatorProtocol`, `ConstraintEvaluator`, `DerivationEvaluator`
   - `DqRunner` family: DqNormalizeRunner, DqResolveRunner, DqMergeRunner, DqPublishRunner
   - QueryExecutor family: `QueryReader`, `Materializer`, `Introspector`, `ViewManager`
   - `DataContext` Pydantic shape (or its postgres-stored equivalent under your pivot)
   - Result shapes: `ERResult`, `MaterializeResult`, `DqResult`, `TranslateResult`, `ConstraintResult`, `DerivationResult`, `ScoreColumnMap`, `DqColumnMap`
   - `RunContext` + `BindingInfo` typed Pydantic models
2. **`Binding` model + folding into Spec** (per your pivot)
3. **`compile()`** — Spec → WorkflowSpec.  Toposort by structural + strategic edges.  Per-source normalize fan-out.  Cross-class pinning of parent run hashes.  Per-stage cache_keys.
4. **`WorkflowSpec` Pydantic shape** — toposorted stages with kind, class_name, source_name, impl_name, cache_key, pinned_parent_runs.
5. **Modeling router at `/model/*`** — register deployed impls, set bindings, trigger runs.
6. **Wiring**: `compile()` emits Maestro-shaped JSON; toy_maestro consumes it; publish gate steps 3+4 cross-check DataContexts against the active spec.
7. **Audit walk-back** end-to-end: `(:KnotRun)` audit nodes in Neo4j → compile_hash → pinned spec revisions → contributing source rows.

---

## Open design questions (named per Pattern 14)

1. **Bindings on Spec or external?** (your pivot lever 1)
2. **DataContexts: class-level vs postgres-stored?** (your pivot lever 2)
3. **Impl source: knot-hosted vs deployed?** (your pivot lever 3)
4. **Naming**: Spec vs Project (no name change made; either works)
5. **Draft branching**: only from currently-published or also from any historical workflow's pinned spec?  Today: parent_revision can be any spec_revisions row.
6. **Spec drafts and impl drafts: same lineage or parallel?** If bindings fold into Spec → same.  If not → two drafts, two gates.
7. **Per-class watermarks**: source_watermarks today are flat dict[str, str].  Under per-source compile fan-out, the watermark for each source flows into that source's normalize cache_key (already designed in earlier compile() — code stripped).
8. **Recursive CTEs**: rejected at sql_gen.  But Translator impls for non-lake targets (Cypher, Gremlin) CAN handle recursion.  Boundary: SQL emission from spec-graph rules → no recursion; Translator-emitted Cypher → yes recursion.
9. **Constraint expression input via API**: `POST /spec/drafts/{id}/constraints` not yet built — Constraint.body is an expression tree, hard to specify in JSON.  Skipped from the spec router (Pattern 9: explicitly open).

---

## Maestro vocabulary we adopted

From toy_maestro research.  Verbatim from Netflix Maestro source:

- **Status** (`WorkflowInstance.Status`): `CREATED`, `IN_PROGRESS`, `TIMED_OUT`, `STOPPED`, `FAILED`, `SUCCEEDED`
- **Step status** (`StepInstance.Status`): 22-value enum including `NOT_CREATED`, `CREATED`, `INITIALIZED`, `WAITING_FOR_SIGNALS`, `EVALUATING_PARAMS`, `WAITING_FOR_PERMITS`, `STARTING`, `RUNNING`, `FINISHING`, `DISABLED`, `UNSATISFIED`, `SKIPPED`, `SUCCEEDED`, `COMPLETED_WITH_ERROR`, `INTERNALLY_FAILED`, `FATALLY_FAILED`, `USER_FAILED`, `PLATFORM_FAILED`, `TIMEOUT_FAILED`, `STOPPED`, `TIMED_OUT`, `PAUSED`
- **Step types**: `Workflow`, `Subworkflow`, `Foreach`, `Conditional`, `Notebook`, `Titus`, `Sparksql`, `Trino`, `NoOp` etc.
- **URL paths**: `/maestro/api/v3/workflows/{wf_id}/start`, `/maestro/api/v3/workflows/{wf_id}/instances/{instance_id}` etc.
- **Workflow definition shape**: `{id, name, params, steps: [{id, type, sub_type, params, transition: {predecessors: [...]}}]}`

---

## Pitfalls / gotchas

- **Same-name slots**: Two Slots with the same `name` on different classes (e.g. B2's Movie.imdb_id + Person.imdb_id) are distinct objects.  spec_store handles this with UID-based cycle scheme.  canonical_dump also tracks by `id()` but emits `{"$ref": <name>}` — fine for hash determinism, but a deserializer using canonical bytes would have to handle this.
- **Pydantic v2 cycles**: `model_dump()` raises "Circular reference detected" on the spec graph.  `model_validate(model_dump())` doesn't round-trip.  Use `spec_store.spec_to_dict / spec_from_dict` instead.
- **OntologyClass `__getattr__` recursion**: Pydantic and Python internals probe for sentinel attributes; the `__getattr__` raises `AttributeError` immediately for `_`-prefixed or `model_`-prefixed names so they fall back cleanly.
- **partial unique index on `published`**: postgres rejects two-statement demote-then-promote (`UPDATE ... SET published=FALSE`; `UPDATE ... SET published=TRUE`) because the index transiently sees zero/two rows.  spec_store does both in one statement: `SET published = (revision = $1) WHERE revision = $1 OR published = TRUE`.
- **Stale spec_revisions across schema changes**: if you change the serializer format, existing rows in postgres won't deserialize.  TRUNCATE + reseed.
- **`pkill` chain in shell**: `pkill ... && other-cmd` returns exit 144 sometimes — `pkill` returns 1 when no match, and shell oddities with `&& disown` chains.  Run cleanups + starts as separate commands.
- **Marimo notebook discovery**: marimo's directory scanner reads first 512 bytes; long header docstrings push `marimo.App` past byte 512 and the file silently disappears from the home page.  Trim header docstrings.

---

## Common workflow commands

```bash
# Stack up + verify
cd /mnt/main/code/knot
bash scripts/down.sh && bash scripts/up.sh && bash scripts/wait-ready.sh
.venv/bin/pytest tests/ -q

# Wipe + reseed spec
.venv/bin/python -c "
import psycopg
with psycopg.connect('postgresql://knot:knot@localhost:5432/knot_control', autocommit=True) as c:
    c.execute('TRUNCATE spec_revisions RESTART IDENTITY CASCADE')
"

# Start spec API
.venv/bin/uvicorn knot.api.main:app --host 0.0.0.0 --port 8000 &

# Start toy maestro
.venv/bin/uvicorn knot.orchestrator.toy_maestro.app:app --host 0.0.0.0 --port 8001 &

# Generate fresh fixture data (deterministic)
.venv/bin/python -m tools.generator B2

# Install all extras
.venv/bin/pip install -e '.[runtime,test,notebook,dev]'
```

**Connection details**:
- Postgres: `postgresql://knot:knot@localhost:5432/knot_control` (env `KNOT_CONTROL_DSN`)
- Neo4j: `bolt://localhost:7687`, auth `(neo4j, knottest)`
- Spec API: http://localhost:8000 (Tailscale: http://100.65.247.65:8000)
- Toy Maestro: http://localhost:8001
- Marimo: not currently running on this branch (notebooks/ was stripped)

---

## Recommended next steps (your call)

1. **Resolve the pivot levers** in a fresh design conversation:
   - Write `design/staging/binding-on-spec.md` — fold or external?
   - Write `design/staging/runtime-di-surface.md` — Config + DataContexts as the only mutable surface; impls as deployed code
   - Amend `core-design.md` commitment 5 if you're committing to the deployed-impl model
2. **Then build the modeling router**:
   - `src/knot/protocols/` — protocol classes + result shapes + RunContext
   - Add `Binding` to `metaschema.py` (or create a sibling `binding_store.py` if you keep them external)
   - Add modeling-router schema tables (`bound_impls` / `impl_config` / `pipeline_runs` / `compiled_workflows` etc.)
   - `src/knot/compile.py` — Spec → WorkflowSpec
   - `src/knot/api/model.py` — `/model/*` router
3. **Then wire toy_maestro into compile()** so demo impls run end-to-end
4. **Then a walkthrough notebook** covering the full lifecycle

---

## Branch commit log (heavily-involved)

```
75efc09 restructure: separate core / demo packages / fixture data
0c236cb add toy Maestro scheduler: DuckDB-backed lake-query workflow dispatcher
69d7927 phase 9: spec router — drafts, mutations, publish gate
6ca0688 phase 8: control_schema + spec_store with draft/publish lifecycle
acf508c phase 6+7: canonical_dump + content_hash + sql_gen
4bddb2e phase 1-5: spec graph metaschema (real refs throughout)
a774353 strip knot implementation; keep only test data + test setup
```

Branch points off `main` at `6bae061`.  `main` has the older
implementation that was stripped (preserved in git history if you want
to cherry-pick anything).

---

## What changed in this conversation (summary)

1. Continued from main: added `POST /sources` + spec_revisions + per-source normalize compile fan-out (commit `6bae061`)
2. You stripped everything to start fresh on `heavily-involved` (commit `a774353`)
3. Phases 1-9 rebuild — spec graph + canonical + sql_gen + spec_store + spec router with drafts/publish (4bddb2e through 69d7927)
4. Toy Maestro scheduler (0c236cb)
5. Restructure into core/demo/data/tools/docs (75efc09)

Key design conversations during the session:
- Source mutability and binding to workflow → led to per-source normalize fan-out + understanding that compile_hash carries source identity
- Drafts requirement → led to spec_store's draft lifecycle + publish gate
- DI runtime surface (your reframing) → flagged as pivot lever
- "Spec" vs "Project" naming → no decision
- Source structure (where data + demo packages live) → led to the restructure
- Same-name-Slot collision → led to UID-based cycle scheme in spec_store

Mood / pacing signals from this session:
- Tolerance for me running too far without checkpoint is low
- Multi-tenant smell rule still applies (single-team posture is non-negotiable)
- Pattern 9 (no v0/v1/future-work hedges) — every "open" must be explicitly named, not buried
- Real types over discriminator strings (Pattern 1) consistently enforced
- Verify on clean stack always, never trust stale schema

End of state document.  Read this first in the new session.
