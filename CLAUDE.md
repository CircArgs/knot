# CLAUDE.md — knot library

**knot** is a **multi-source MDM schema compiler** — a typed SQL
compiler specialized for the problem of fusing N sources' claims
about the same entities into one canonical view, with entity
resolution as a first-class write primitive. Closest category-peer:
jOOQ (typed SQL compiler, no connection ownership) / LinkML
(declarative spec → DDL emission); closest problem-peer: MDM
platforms (Reltio, Tamr, Senzing) — except knot ships only the
compiler layer and hands runtime to the host.

## Purpose (locked-in north-star — every change must justify against these)

1. **Compile a typed Python spec to postgres SQL.** Pure library,
   no I/O. `Spec.ddl()` emits the target schema; `Query.sql()`
   emits read SQL; `binding.{write,assign_canonical,recanonicalize,
   retract,update_slot}_sql()` emit write SQL. The host runs the
   strings.

2. **Fuse multi-source claims into one canonical view.** N sources
   publish bindings about the same entity; knot's `_resolved` view
   picks per-slot winners via argmax over per-(source, class, slot)
   weights, with `_all_sources` exposing per-source jsonb provenance.
   The host tunes weights at runtime (`source_weight` is a live
   policy table, not a baked-in constant).

3. **Entity resolution as a first-class write primitive.**
   `assign_canonical_sql` is one atomic CTE chain: stamp
   `canonical_id`, forward-translate this row's FK columns
   (source-id → canonical-id), backward-fan-out to every
   referencing class's bindings, idempotency-gated.
   `recanonicalize` cascades canonical-id changes graph-wide.
   No ORM expresses this.

4. **One typed predicate AST for reads, virtuals, and constraints;
   one typed retrieval AST for relational, graph-walk, and k-NN.**
   `cls.col.year >= 1888` is the same `Expr` in `.where(...)`,
   `add_constraint(body=...)`, and `add_virtual(where=...)`. The
   same `Query` composes relational filters, transparent FK chains
   (`movie.col.director.name`), and vector k-NN
   (`slot.distance_to(v)` as a sort/predicate/projection) — three
   retrieval shapes, one builder. Authoring the same Expr in
   constraints + virtuals + reads keeps the spec the single source
   of truth.

5. **knot is a substrate, not a runtime.** No connections, no
   scheduler, no migration engine, no ingest worker, no ER policy
   decisions. The host owns all of that; knot just compiles.

Branch `library/v0` was the focused-library line; the current
working branch (`library/v0-substrate`) reinforces the substrate
posture against any drift toward becoming an ORM (the surface looks
ORM-shaped — typed query builder, fluent `.where().order_by()`,
upserts, partial UPDATEs — but the semantic substrate underneath
(resolved view, weight argmax, ER cascade) is what makes knot a
different category). Anything that talks to a connection, serves
HTTP, holds runtime state, or assembles a GraphQL endpoint lives
outside the library — in a *reference adapter* a team builds around
it. The earlier monorepo (API service + UI + ingest + ER + AI) is
in git history on `draft-rfc` and `main`.

## Posture

- **Pure library.** No FastAPI, no HTTP, no `psycopg.connect`, no
  ingest path inside `knot/`. Compile functions return SQL strings;
  the host runs them (and binds params for the few write/seed
  emitters that emit named placeholders).
- **No migration runtime.** knot emits the *target* schema as
  canonical DDL (`Spec.ddl()`); teams run that through their existing
  schema-diff tool (sqldef / Atlas / dbmate / …) to produce
  migrations against a live DB. Rationale + tool recs are in the
  **Schema deployment** section below. knot is a compiler, not a
  migration engine.
- **Postgres-only today.** All emitters target postgres. A future
  Trino / Spark / cypher dialect lands as a sibling module
  (`expr_sql_trino.py`, `query_sql_trino.py`, etc.) — same dispatch
  pattern, separate file per target. Not preemptively built.
- **Module-level constants, not env vars.** The default schema name
  (`knot_data`), the corrections source name (`_user_corrections`),
  the weight table name (`source_weight`) are exposed as kwargs on
  the emitter functions; their *defaults* are constants you can rebind
  before import. No `os.environ.get` anywhere.
- **Weights are opaque.** Per-(source, class, slot) weights live in
  `source_weight`; the resolver argmaxes over them. knot does not
  constrain the range, calibrate them, or pretend they're
  probabilities. Whatever scoring algorithm produced the numbers owns
  that — knot just stores + reads.
- **Bindings is the only table per class.** 3 relations: 1 table
  (`<class>_bindings`) + 2 views (`<class>_resolved`,
  `<class>_all_sources`). Bindings holds every slot value + the
  per-source ER-stamped `canonical_id`. The resolved view computes
  argmax over bindings + weights at read time; the "set of known
  canonical_ids" is implicit — `SELECT DISTINCT canonical_id FROM
  bindings WHERE canonical_id IS NOT NULL` — no separate canonical
  registry table. No FK constraints emitted (postgres can't FK to
  views; bindings stay loose pre-ER); referential integrity is
  enforced by ER orchestration (see "Entity resolution + FK
  semantics" below) and surfaced by the data-quality validators.
- **Single-team posture.** Trusted authors of the spec, no
  multi-tenant defenses, no sandboxing.
- **Sync.** The compiler is sync (pure transforms). Adapters wrap it
  for async hosts if they want.

## Host integration

knot is a library, not a service. It's `pip install`-ed into the
host processes that own postgres connections. The reference shape is
**three deployment surfaces, one shared spec**:

- **Per-source ingest workers** (Temporal workflows). Each source
  (`imdb`, `tmdb`, `rottentomatoes`, …) gets its own workflow
  definition with its own auth, rate limits, schedule, source-shaped
  normalization. Activities pull from the source, normalize, then
  call `binding.write_sql()` to get one upsert SQL template, and
  execute it with `{"rows": rows}` bound by the driver. The upsert
  preserves the ER-stamped `canonical_id` and `er_metadata` across
  re-ingests; every other slot + `raw_payload` gets overwritten.
- **ER workers** (Temporal workflows). Look at unresolved bindings
  (`cls.unresolved`), decide canonical_ids (whatever scoring /
  matching policy the team owns), call `binding.assign_canonical_sql()`
  or `binding.recanonicalize_sql()` and bind `{"canonical_id": …,
  "source_identifier": …, "er_metadata": json.dumps({...}) or None}`
  via the connector. **Each call is one atomic statement that does
  four things at once:** (1) stamp the binding's canonical_id,
  (2) translate this row's own FK columns from source-id to
  canonical-id (forward lookup against the target's bindings, same
  source's namespace), (3) fan out to every referencing class's
  bindings to rewrite their FK columns where they held the just-
  stamped source-id (backward, also source-scoped), (4) register the
  canonical_id in the identity table. Recanonicalize cascades
  canonical-id rewrites to every referencing binding (source-
  agnostic, since post-ER FK columns hold canonical-ids).
  Ingest cadence and ER cadence are independent — that decoupling
  is why these are separate workflows.
- **Service API** (FastAPI / GraphQL / REST / whatever). Translates
  incoming requests into knot `Query` AST nodes using the spec's
  classes, calls `q.sql(schema=...)` to compile to a SQL string,
  executes the SQL, maps rows to the response shape it owes its
  caller.

The **spec** is the shared dependency — a Python module that every
surface imports alongside knot. Spec + knot together compile to SQL;
the host process owns the connection and executes. Three deployment
shapes, one source of truth for schema + bindings + weights +
constraints.

knot has no awareness of HTTP, no workflow concepts, no ingest
scheduler, no ER policy, no auth, no response shape. It is a
**substrate** the host consumes:

- the service API is the *active read consumer* that composes
  `Query` ASTs, possibly issues many per request, joins knot results
  with whatever else it needs;
- the workers are *active write consumers* that orchestrate ingest
  + ER as durable workflows;
- knot provides the AST language and the SQL compiler. Nothing more.

This is the design intent behind every "the host owns this" line in
the rest of this document. The library's job ends at the SQL
string (plus the named-placeholder dict, where one is needed).

## Schema deployment

knot's contract is **"here's the target schema; you deploy it."**
`Spec.ddl(schema=…)` returns one canonical CREATE script — schema,
extension (when needed), all tables, indexes, FK constraints, views.
Idempotent throughout (`IF NOT EXISTS` / `CREATE OR REPLACE`). For
first deploys, run it as-is. For migrations against a live DB, feed
the script through a schema-diff tool. **knot deliberately does not
own the diff.**

### Why

The schema-diff problem is hard, well-explored, and *orthogonal* to
"compile a typed spec to SQL." Mature tools (sqldef, Atlas, dbmate)
have years of edge-case shake-out — composite types, generated
columns, expression defaults, partitions, drift detection — that
knot would have to rediscover one bug at a time. Teams also have
strong opinions about migration tooling; locking the spec to one
engine would be the wrong fight. The architecturally honest answer
to a senior engineer asking "what about migrations" is *"knot
compiles, your migration tool diffs"* — same posture as "knot emits
SQL, your host opens connections."

### Tool recommendations

| tool | when to pick it |
|---|---|
| **[Atlas](https://atlasgo.io/)** (`atlas schema diff` / `apply`) | **Tested with knot 2026-05-18; the default recommendation.** Single Go binary, first-class `pgvector` support (handles `vector(N)` columns and HNSW indexes with operator classes — `vector_cosine_ops` / `_l2_ops` / `_ip_ops`). Needs a clean throwaway "dev DB" to render the desired state; trivial to provision (one `CREATE DATABASE atlas_dev`). The community edition ignores views entirely — which is fine, knot's CREATE OR REPLACE views run as a second step after the schema diff. |
| **[sqldef](https://github.com/sqldef/sqldef)** (`psqldef`) | Lightweight (one binary, no dev DB needed). Worth re-testing against current knot DDL; was previously fragile on a now-removed bindings shape. |
| **[dbmate](https://github.com/amacneil/dbmate)** | imperative migrations + manual SQL. Pick if the team already runs imperative migrations and just wants knot's emitted DDL as the starting point for hand-authored steps. |
| **Alembic / Flyway / Liquibase** | language-/JVM-specific. Use the "raw SQL" file mode and paste in `Spec.ddl()` output. |

### Process

The two-phase deploy loop with Atlas:

```bash
# One-time: create the dev DB Atlas uses to render desired state.
docker exec knot-postgres psql -U knot -d knot \
  -c "CREATE DATABASE atlas_dev"

# 1. Spec change — edit the Python spec module.

# 2. Emit the target schema *without* views (Atlas community can't
#    manage views; knot rebuilds them in step 4).
python -c "
from your_spec import spec
print(spec.ddl(schema='knot_data', include_views=False))
" > target.sql

# 3. Diff against the live DB; review, then apply.
atlas schema diff \
  --from "postgres://knot:knot@localhost:5433/knot?sslmode=disable" \
  --to "file://target.sql" \
  --dev-url "postgres://knot:knot@localhost:5433/atlas_dev?sslmode=disable" \
  -s knot_data

atlas schema apply \
  --url "postgres://knot:knot@localhost:5433/knot?sslmode=disable" \
  --to "file://target.sql" \
  --dev-url "postgres://knot:knot@localhost:5433/atlas_dev?sslmode=disable" \
  -s knot_data

# 4. Rebuild views only — `Spec.views_ddl()` returns just the
#    CREATE OR REPLACE VIEW statements (no tables, indexes, or
#    weight table). Idempotent, no data dependence, always safe
#    after Atlas applies the table diff.
python -c "
from your_spec import spec
import psycopg
psycopg.connect(...).execute(spec.views_ddl())
"

# 5. Seed weight rows for any new (source, class, slot) triples —
#    INSERT-only, ON CONFLICT DO NOTHING.
python -c "
from your_spec import spec
from knot.compile.weight import emit_weight_seed
for sql, params in emit_weight_seed(spec, schema='knot_data'):
    pg.execute(sql, params)
"
```

Notebook **03_migration** walks through this loop end-to-end with a
toy spec; review there for the live shape.

### What `Spec.ddl()` covers vs. doesn't

**Covers** (schema concerns — knot's job):
- `CREATE SCHEMA IF NOT EXISTS …`
- `CREATE EXTENSION IF NOT EXISTS vector;` (gated on any VECTOR slot)
- `source_weight` table (runtime weight-policy table)
- Per concrete class: bindings table (one row per
  `(source, source_id)`, PK on the pair, holds slot values +
  `raw_payload` + `er_metadata` + the ER-stamped `canonical_id`),
  btree index on `canonical_id`, HNSW index per vector slot on
  bindings. **No canonical table** — bindings is the only table per
  class; the "set of known canonical_ids" is implicit via
  `SELECT DISTINCT canonical_id FROM bindings WHERE canonical_id IS
  NOT NULL`. **No FK constraints** — bindings stay loose by design;
  ER orchestrates referential integrity via forward translation +
  backward fan-out.
- Per concrete class: `_resolved` view (argmax-over-weight) and
  `_all_sources` view (per-source jsonb provenance)
- Per virtual class: filtered view over its parent's `_resolved` view

**Doesn't cover** (host / migration-tool concerns):
- **`source_weight` seed rows** — separate emitter
  (`emit_weight_seed()`). Data, not schema; run after the DDL.
- **Backfills** — adding a NOT NULL column to a non-empty table
  needs an `UPDATE` first. knot emits the column; the host (or the
  migration tool's pre-script) backfills.
- **Data migrations** — vector dim changes need re-embedding every
  row, type changes might need value coercion. knot emits the new
  schema shape; the host owns repopulation.
- **Drift detection / audit / rollback** — your migration tool's job.

### History — why `diff_against_db` was removed

Earlier `library/v0` shipped `knot.compile.migrate.diff_against_db` —
an Alembic-style autogen that introspected the live DB and produced
reconciling SQL (`MigrationOp` lists, destructive flags, target
categorization). Roughly 1000 lines + tests; worked for the simple
cases. Removed in favor of "emit target, delegate diff" for four
concrete reasons:

1. **Trust problem.** Selling knot to a team meant asking them to
   take a homegrown autogen on faith for the load-bearing piece of
   any deploy. Senior engineers correctly push back on *"the diff is
   automatic and we wrote it ourselves."* The pitch is much easier
   when knot owns the schema and a battle-tested external tool owns
   the diff.
2. **Edge-case tail.** sqldef and Atlas have years of shake-out on
   cases knot would have to rediscover: composite types,
   generated/computed columns, expression defaults, partitions,
   inheritance, role/grant management. Each one would be a bug
   filed against knot.
3. **Concrete vector breakage.** Adding `VECTOR` slots immediately
   surfaced three bugs in the autogen — HNSW indexes dropped on
   every diff, silent dim mismatch (`vector(384) → vector(768)`),
   silent metric mismatch (`cosine → l2`). Each fix required
   bespoke introspection (`pg_attribute.atttypmod`, `pg_index` +
   `pg_opclass`). This previewed the maintenance shape: every new
   postgres feature would need bespoke handling in knot. Not its
   job.
4. **Posture consistency.** knot is a *compiler*. "Compiler emits
   target, external tool reconciles" is the same shape as the rest
   of the library's host-boundary lines. `init_sql(query_fn=…)`
   was the one place that quietly violated it.

The migrate code lives in git history before the removal commit
(grep `diff_against_db` in the log). Don't reintroduce — see
"What NOT to do."

## Entity resolution + FK semantics

knot's reads (the resolved view, virtual class views, FK-walking
queries like `movie.col.director.name == "Tarantino"`) join on
`canonical_id`. So FK columns on the bindings table need to *hold*
canonical-ids by the time anything reads the resolved layer — even
though sources naturally publish their own ids (e.g. imdb's
`tt0110912` for Pulp Fiction's `director` field).

The mechanism: every `binding.assign_canonical_sql()` call is one
atomic statement (a chain of writable CTEs) that does **four things**:

1. **Stamp** — `UPDATE <class>_bindings SET canonical_id = … WHERE
   source_name = … AND source_identifier = … AND canonical_id IS
   NULL`. No-op re-runs by design.
2. **Forward FK translation** — for each `ClassRef` slot on this
   class, look up the column's current value in the target's
   bindings table (same source's namespace) and rewrite from
   source-id to the target's canonical-id. `COALESCE` keeps the
   source-id if the target hasn't been ER'd yet — the next step
   catches the orphan later.
3. **Backward fan-out** — for each `(referencing_class, fk_slot)`
   pair from `cls.referrers`, `UPDATE <ref_class>_bindings SET
   <fk_slot> = <new_canonical_id> WHERE source_name = <this stamp's
   source> AND <fk_slot> = <this stamp's source_identifier>`. Gated
   on `EXISTS (SELECT 1 FROM stamp)` so a re-run on an already-
   stamped row never force-fanouts (strict idempotency).
4. **Register** — `INSERT INTO <class> (canonical_id) SELECT
   canonical_id FROM stamp ON CONFLICT DO NOTHING`. Driven by the
   stamp CTE so a no-op stamp doesn't phantom-register.

`binding.recanonicalize_sql()` cascades canonical-id rewrites to
referencing bindings (source-agnostic — post-ER FK columns hold
canonical-ids, no source coupling).

**Same-source assumption.** Step 2 + 3 assume a binding's FK column
holds source-ids in the *binding's own source's* namespace
(imdb credit's `.movie` is an imdb movie id). Sources that publish
cross-source references — corrections specifically — bypass this
shape; they should write the canonical-id directly into the FK
column at ingest time. knot doesn't model corrections-source
referential semantics; the host owns it.

**Pre-ER FK orphans.** Bindings whose `canonical_id IS NULL` are
filtered out of the resolved view (`WHERE canonical_id IS NOT
NULL`), so unresolved orphans never surface in reads. The data-
quality validators (`spec.emit_validation()`) can be extended to
flag FK columns that still hold non-canonical-id-shaped values
after the target's ER cadence has caught up.

**Why no FK constraints.** FK columns live on the bindings table.
Pre-ER they hold source-ids that don't exist in any canonical
registry; post-ER they hold canonical-ids that do. A postgres FK
constraint would fail at ingest time on every pre-ER source-id.
DEFERRABLE doesn't help (source-ids may never become canonical-ids
if the target source is never ingested). So bindings stay loose by
design, and referential integrity is enforced by ER orchestration
(steps 2 + 3 above) plus the data-quality scan.

## Constraint enforcement

knot doesn't bundle constraint checks into the write SQL. There are
three independent primitives:

1. `binding.write_sql()` — one upsert SQL template. No constraint
   awareness.
2. `spec.emit_validation()` — one SELECT per constraint. No writes.
   Returns both user-declared constraints AND built-in invariants
   knot derives from the spec shape (see "Built-in constraints"
   below). Opt out with `include_builtins=False`.
3. `pg.transaction()` — host opens it, runs (1), runs (2), decides
   whether to commit or rollback. Use `with pg.transaction():`
   even on autocommit psycopg3 connections; `cur.execute("BEGIN")`
   is a NO-OP under autocommit.

The host composes them. The canonical "ingest with enforcement"
shape is ~10 lines:

```python
def ingest_with_enforcement(binding, rows, *, spec, pg, schema):
    sql = binding.write_sql(schema=schema)
    payload = json.dumps(rows)
    severity = {c.name: c.severity for c in spec.constraints}

    with pg.transaction(), pg.cursor() as cur:
        cur.execute(sql, {"rows": payload})
        for rule, vsql in spec.emit_validation(schema=schema):
            if severity[rule].value != "error":
                continue
            cur.execute(vsql)
            bad = cur.fetchall()
            if bad:
                raise ConstraintViolation(rule, bad)
    # got here ⇒ committed; ERROR-severity constraints held.
```

Knot exposes the primitives because **the host owns the enforcement
policy**. Five common shapes from the same three primitives:

| policy | shape |
|---|---|
| **Block on any violation** | raise on first non-empty SELECT → rollback (above) |
| **Block on new violations only** | snapshot baseline counts before write, post-write count after, raise iff `post > pre` |
| **Block on specific rules** | filter by name or severity (above filters to ERROR; WARNINGs are informational) |
| **Don't block** | commit the write, run validation asynchronously, post violations to a `data_quality` table or page oncall |
| **Soft-fail per row** | bucket the batch into clean + violating, commit the clean subset, route violators to a review queue |

All five are the same primitives composed differently. If knot
bundled (1) + (2) into one SQL script with a hardcoded "any
violation → rollback" policy, only the first option would be
possible without escape hatches.

**Gotcha:** `emit_validation()` SELECTs run against the whole
`*_resolved` view by default, so pre-existing violations in
unrelated rows would also block your write under the simple "block
on any violation" policy. Three knobs to scope:

1. `emit_validation(scope_to_source_identifiers={"imdb": [...]})`
   — restricts each SELECT to canonical_ids touched by that
   source's batch (delta-only enforcement). Inlines the
   `(source, identifier)` tuples as SQL literals.
2. Baseline-vs-post-write count diffing — host snapshots before
   the write and only blocks when the delta is positive.
3. Fully decoupled validation — separate Temporal workflow runs
   `emit_validation()` on a schedule; never blocks ingest.

In a Temporal deployment, the "decoupled scheduled sweep" pattern
is usually the right default with `scope_to_source_identifiers=`
layered in for the rules where blocking is genuinely required.

### Built-in constraints

knot ships two families of invariants automatically from the spec
shape — no user declaration needed. Both are prefixed `_builtin_`
in the constraint name so hosts can filter them by name if needed:

- `_builtin_fk_orphan_<Class>_<slot>` — per `ClassRef` slot. Body
  is `slot.is_null() | slot.target_exists()`. Catches a post-ER
  FK column that doesn't match any canonical_id in the target's
  resolved view. Severity: ERROR. (ER's forward FK translation
  *should* maintain this, but the check surfaces the case where
  it didn't — e.g. the referenced binding was never ER-stamped.)
- `_builtin_required_null_<Class>_<slot>` — per required
  non-identifier slot. Body is `slot.is_not_null()`. Catches the
  case where every source's claim for a required slot is null,
  leaving the resolved row NULL. Severity: WARNING.

The host doesn't declare these — adding `required=True` or a
`ClassRef` slot to the spec is enough. Opt out via
`spec.emit_validation(include_builtins=False)` for user-only.

## Layout

```
knot/
  __init__.py          # public re-exports
  spec.py              # Spec, OntologyClass, VirtualClass, Slot,
                       # Source, SourceBinding, SlotMapping,
                       # Constraint, Severity, ClassKind
  ast/                 # spec-layer primitives — no SQL knowledge
    __init__.py
    types.py           # types.TEXT, …, types.ARRAY(…), types.VECTOR(…) —
                       # the canonical type surface (Vector backed by
                       # pgvector + HNSW)
    expr.py            # Expr AST: Ref, FkRef, FkChainRef, Compare,
                       # BoolOp, Not, IsNull, InList, Between, Exists,
                       # CountRel, Raw, This, Aggregate + ``this``
                       # magic accessor for outer-scope refs
    select.py          # read substrate: Query, OrderBy
  compile/
    __init__.py
    ddl.py             # bindings tables + indexes + source_weight +
                       # resolved/all_sources views + virtual class
                       # views — Spec.ddl() emits the full target
                       # schema in one script (no canonical table per
                       # class; bindings is the only one)
    resolver.py        # per-(source, class, slot) argmax resolved views
    constraints.py     # constraint validation SELECTs
    write.py           # upsert + ER (assign_canonical, recanonicalize,
                       # retract) — all in one statement each
    weight.py          # source_weight INSERT-only seed
    expr_sql.py        # @singledispatch compile_sql over Expr nodes
    query_sql.py       # @singledispatch compile_query over Query nodes
tests/
  unit/                # pure unit tests (~211 tests; no I/O)
  integration/         # ~22 tests against live postgres on :5433
notebooks/
  query_playground.py  # end-to-end marimo playground (spec → DDL →
                       # ingest → query)
```

There is intentionally **no `RFC.md`, no `LIBRARY_DESIGN.md`** in this
tree — they got deleted as stale. Design conversation lives in git
history and in the auto-memory; the code is the contract.

## Workflow

```bash
# Install editable in the root .venv.
.venv/bin/pip install -e .

# Smoke-test the public surface.
.venv/bin/python -c "
from knot import Spec, types, this
print('ok')
"

# Unit tests (pure, no I/O).
.venv/bin/pytest tests/unit/ -q

# Integration tests against the live postgres compose service.
# Brings up postgres if it's not already running on :5433.
.venv/bin/pytest tests/integration/ -q

# Lint + format.
.venv/bin/ruff check knot/ tests/
.venv/bin/ruff format knot/ tests/

# Interactive playground (binds 0.0.0.0:2718 for Tailscale access).
.venv/bin/marimo edit --host 0.0.0.0 --port 2718 notebooks/query_playground.py
```

## The user-facing surface (canonical patterns)

**Spec construction** — dataclass builders, single-file in `knot/spec.py`:

```python
from knot import Spec, types

spec = Spec(id="movies", version="0.1")

person = spec.add_class("Person")
person.slot("canonical_id", types.TEXT, identifier=True)
person.slot("name", types.TEXT, required=True)
person.slot("birth_country", types.TEXT)

movie = spec.add_class("Movie")
movie.slot("canonical_id", types.TEXT, identifier=True)
movie.slot("title", types.TEXT, required=True)
movie.slot("year", types.INTEGER)
movie.slot("director", person)             # FK — pass the class directly
movie.slot("genres", types.ARRAY(types.TEXT))
movie.slot("title_embedding", types.VECTOR(384))   # pgvector + HNSW

credit = spec.add_class("Credit")
# Typed enum — DDL emits TEXT CHECK (col IN (...)) inline; no
# CREATE TYPE ceremony, no migration headache when values change.
# validate_rows_sql adds a membership pre-check at ingest time.
credit.slot(
    "role",
    types.ENUM("director", "actor", "writer", "producer"),
    required=True,
)
credit.slot("movie", movie, required=True)
credit.slot("person", person, required=True)
```

**Vector slots** lower to `vector(N)` columns plus a per-column HNSW
index (operator class picked by the slot's `metric=` kwarg —
`"cosine"` / `"l2"` / `"ip"`). The deploy SQL gets a single `CREATE
EXTENSION IF NOT EXISTS vector;` prepended when any vector slot is
present. Embedding values themselves are runtime data the host (an
embedding worker, usually separate from the ingest worker) computes
and binds via the connector — knot never sees vectors, same as it
never sees rows. The postgres image needs pgvector installed; the
vanilla `postgres:16-alpine` knot's compose has historically used
won't satisfy a spec that declares a `VECTOR` slot.

**Sources and bindings** — source-method-chained. Weight is its
own concern, set separately via `set_default_weight` / `set_weight`
(declared after the mapping, not woven into the mapping kwargs):

```python
imdb = spec.add_source("imdb")
imdb_movie = imdb.bind(movie)
imdb_movie.slot(class_slot="canonical_id", source_slot="imdb_id")
imdb_movie.slot(class_slot="year", source_slot="release_year")
imdb_movie.slot(class_slot="runtime", source_slot="runtime",
                sql="(regexp_match(runtime, '[0-9]+'))[1]::int")
# Weight — separate API on the binding. Opaque floats; higher wins.
imdb_movie.set_default_weight(0.85)      # applies to every slot
imdb_movie.set_weight("year", 0.9)       # per-slot override
imdb_movie.set_weight("runtime", 0.7)
# Slots not explicitly mapped → implicit passthrough at default_weight.
```

**Ingest, ER, and corrections live on the entities they describe**
— not on `Spec`:

```python
# Single-binding write — one upsert SQL template; host binds rows via the connector.
sql = imdb_movie.write_sql(schema="knot_data")
with pg.cursor() as cur:
    cur.execute(sql, {"rows": json.dumps(rows)})

# ER decisions on a specific binding row — same shape: SQL + host binds.
cur.execute(imdb_movie.assign_canonical_sql(), {
    "canonical_id": "m_x",
    "source_identifier": "tt001",
    "er_metadata": json.dumps({"run_id": "r42", "method": "exact_title_year"}),
})
cur.execute(imdb_movie.recanonicalize_sql(), {
    "new_canonical_id": "m_y",
    "source_identifier": "tt001",
    "er_metadata": None,  # None ⇒ inherit closed row's er_metadata
})

# Class-level constraints + virtual subclasses
movie.add_constraint("year_sane", body=movie.col.year >= 1888)
movie.add_virtual("DirectedMovie",
                  where=movie.has_any(credit, role="director"))

# Corrections binding lookup
spec.enable_corrections(default_weight=1e6)
corr_b = movie.corrections_binding()
```

**Read substrate** — fluent immutable queries with outer-scope
correlation, transparent FK walks, per-slot aggregates. Every read
declares its layer explicitly via one of three entry points on the
class — there is no silent default:

- ``cls.resolved`` — argmax view, one row per canonical_id, the
  resolver's winners. The default "current state" shape.
- ``cls.all_sources`` — per-source provenance view, one row per
  canonical_id with each slot as a jsonb of
  ``{source_name: {value, weight}}``.
- ``cls.from_source(source)`` — one source's raw bindings about this
  class. Useful pre-ER (when ``canonical_id`` is still NULL) and for
  per-source audits.

```python
from knot import this

# Top 10 movies + their directors (resolved view, FK walk, project)
q = (movie.resolved.order_by(movie.col.year, "desc")
                   .limit(10)
                   .select(movie.col.title, movie.col.director.name))

# Directors with more than 5 movies (correlation + count aggregate)
q = person.resolved.where((movie.col.director == this.Person).count() > 5)

# People who never directed (.none() aggregate)
q = person.resolved.where((movie.col.director == this.Person).none())

# What does imdb specifically claim? (raw bindings, one source)
q = movie.from_source(imdb).order_by(movie.col.year, "desc").limit(10)

sql = q.sql(schema="knot_data")
```

The cross-source raw bindings stream (every source's claims, no
filter) is intentionally not exposed at the user surface — it's
rarely the right read shape, and ``from_source`` covers per-source
inspection cleanly. Drop to raw SQL if you genuinely need it.

**Spec keeps only whole-graph methods.** Per-entity facts live on
the entity they describe (see above); Spec is the registrar and
holds operations that genuinely span the whole graph:

```python
spec.validate()                                # raises SpecError if malformed

# Target schema — one canonical CREATE script for the whole spec.
# Idempotent (IF NOT EXISTS / CREATE OR REPLACE throughout). For first
# deploys, execute directly. For migrations against a live DB, pipe
# through a schema-diff tool — knot doesn't own the diff. See the
# "Schema deployment" section below.
sql = spec.ddl(schema="knot_data")
pg.execute(sql)                                # first deploy
# … or for migration:
#   python -c "print(spec.ddl(schema='knot_data'))" | psqldef --dry-run …

checks = spec.emit_validation(schema="knot_data")              # [(name, sql), …]
```

Per-entity runtime methods live on the entity. Every one returns
SQL templates only — no row data, no value args, no `json.dumps`
inside knot. The host binds via the connector.

```python
sql               = q.sql(schema="knot_data")                  # literals inlined
sql_write         = binding.write_sql(schema="knot_data")      # %(rows)s::jsonb (upsert)
sql_validate      = binding.validate_rows_sql(schema=…)        # %(rows)s::jsonb — pre-write per-row validator
sql_update_slot   = binding.update_slot_sql("title_embedding") # %(rows)s::jsonb — per-slot UPDATE (backfill)
sql_assign        = binding.assign_canonical_sql(schema=…)     # %(canonical_id)s, %(source_identifier)s, %(er_metadata)s
sql_assigns       = binding.assign_canonicals_sql(schema=…)    # %(assignments)s::jsonb — batched ER mint
sql_recan         = binding.recanonicalize_sql(schema=…)       # %(new_canonical_id)s, %(source_identifier)s, %(er_metadata)s
sql_retract       = binding.retract_sql(schema=…)              # %(canonical_id)s, %(source_identifier)s — DELETE
sql_explain       = movie.explain_winner_sql(slot="year")      # SELECT: per (canonical_id, source) value + weight + is_winner + margin
```

Read-side k-NN composes anywhere a value expression does:

```python
target = [0.1, 0.2, ...]  # literal vector
q = (movie.from_source(imdb)
          .order_by(movie.col.title_embedding.distance_to(target))
          .limit(10)
          .select(movie.col.title, movie.col.title_embedding.distance_to(target)))

# Cross-row form: distance_to accepts another VectorRef
q = (other_movie.from_source(tmdb)
                .order_by(other.col.emb.distance_to(movie.col.emb))
                .limit(5))
# HNSW index ONLY fires on cls.from_source(s) queries (the resolved
# view's per-slot argmax is a correlated subquery that blocks index
# push-down). ML/blocking workflows route through from_source; serving-
# side k-NN against the canonical view seq-scans unless the host
# materializes its own view.
```

Virtual classes nest — `VirtualClass.add_virtual` chains, depth-
ordered at DDL emit time:

```python
directed = movie.add_virtual("DirectedMovie", where=...)
recent_directed = directed.add_virtual("RecentDirectedMovie",
                                       where=movie.col.year >= 2000)
```

The read path lives on the query, not the spec. The write/ER path
lives on the binding, not the spec. Rows + values never enter the
compile API — knot emits SQL templates with `%(named)s::jsonb`
placeholders, the host's connector binds actual data. Multi-binding
atomic write = multiple `binding.write_sql()` calls, all run in one
`pg.transaction()`.

**Façade contract.**

- **Deploy-time methods validate; hot-path methods don't.**
  ``Spec.ddl`` and ``Spec.emit_validation`` call ``Spec.validate()``
  first (deploy / governance moments — bad spec → caught early).
  ``Query.sql``, ``binding.write_sql``, ``binding.assign_canonical_sql``,
  ``binding.recanonicalize_sql``, ``binding.retract_sql`` *don't*
  validate — they run per API request / per ingest batch / per ER
  decision, and walking 17 classes + N bindings every call is
  wasteful. Host is expected to ``spec.validate()`` once at startup
  (or rely on ``Spec.ddl`` having done so at deploy). Free functions
  in ``knot.compile.*`` likewise never validate — back door for
  "compile this known-broken spec anyway" tests.
- ``Spec.ddl(schema=…)`` returns the canonical CREATE script — the
  *target* schema, no diffing. Migrations against a live DB are an
  external tool's job (sqldef / Atlas / dbmate / …). knot's prior
  ``init_sql(query_fn=…)`` / ``diff_against_db`` autogen is removed;
  see "Schema deployment" for the rationale and "What NOT to do"
  for the retention rule.
- ``Query.sql`` returns a SQL string with literals inlined — no
  parameter list, the host calls ``cur.execute(sql)`` and is done.
- The binding compile methods (``binding.write_sql``,
  ``binding.validate_rows_sql``, ``binding.update_slot_sql``,
  ``binding.assign_canonical_sql``, ``binding.assign_canonicals_sql``
  (batched), ``binding.recanonicalize_sql``, ``binding.retract_sql``)
  return SQL templates with named placeholders (``%(rows)s::jsonb``,
  ``%(assignments)s::jsonb``, ``%(canonical_id)s``, …). The host
  binds runtime data via its connector. Pre-write validation +
  enforcement composition is the host's policy (see the "Constraint
  enforcement" section).
- ``emit_validation`` and ``emit_weight_seed`` return parameterized
  statements because their parameters are derived from the spec
  itself, not from runtime input.

**Weight runtime**:
- Per-(source, class, slot) value lives in `<schema>.source_weight`.
  Opaque float column; no range constraint, no schema-level
  calibration assertion.
- The seed emitter (`emit_weight_seed`) is **INSERT-only** (`ON
  CONFLICT DO NOTHING`). Spec values are *initial conditions*; once a
  row exists, the operator owns it. Redeploying the spec never
  clobbers operator tuning.

## Boundary rules

- **No I/O in `knot/`.** Anything that calls `psycopg.connect`,
  `await conn.execute`, or otherwise talks to a resource is a bug.
- **No env-var indirection.** Per-deployment knobs are kwargs on the
  emit functions (defaults are module-level constants).
- **Spec → compile direction only.** `knot/compile/*.py` imports from
  `knot/spec.py`, `knot/expr.py`, `knot/select.py`, `knot/types.py`.
  The spec layer must not import from `knot/compile/`. The metaschema
  is upstream of every emitter.
- **`knot.types` is THE type surface.** `Primitive` enum, `Array`,
  `ClassRef` exist internally in `knot.spec` but are not in the
  public `__init__.py` exports. Users never type
  `Primitive.TEXT` — they type `types.TEXT`.
- **One way to do everything.** No deprecation cruft. `spec.bind(...)`,
  `binding.map(...)`, string shorthand `"text"`, the `.fk()` method,
  the `Primitive`/`Array`/`ClassRef` public imports, and the
  `accuracy` field name were all removed when their replacements
  shipped. This is pre-release; refactor by deletion.
- **Notebooks go through knot expressions only — no raw SQL,
  anywhere.** Every cell in every file under `notebooks/` (demo
  deck, deploy walkthroughs, ER notebooks, all of them) MUST read
  and write via knot's compiled surface: `cls.resolved`,
  `cls.all_sources`, `cls.from_source(s)`, `cls.unresolved`,
  `.where(col.is_null())`, `.order_by(col.distance_to(vec))`,
  `binding.write_sql()`, `binding.assign_canonical_sql()`,
  `binding.recanonicalize_sql()`, `binding.retract_sql()`,
  `spec.emit_validation()`, `spec.ddl()`. Forbidden in any notebook
  cell: `pd.read_sql_query(f"SELECT ... FROM {cls.bindings_table_name}
  ...")` or any other raw-SQL string that bypasses knot's
  expressions. A notebook dropping to raw SQL anywhere tells the
  viewer knot's surface is insufficient — defeats the entire pitch.
  If a needed surface doesn't exist, that's a knot gap to file.
  The only carve-out: **postgres catalog introspection**
  (`information_schema`, `pg_views`, `pg_indexes`, etc.) — that's
  inspecting postgres metadata, not knot's relational layer.

## Conventions (apply proactively)

- **Frozen dataclasses with `slots=True` for AST nodes.** Pure data,
  no rendering methods. Builder methods on user-facing types
  (`OntologyClass.slot`, `Query.where`, `Source.bind`) construct
  nodes; rendering lives in `knot/compile/`.
- **`@functools.singledispatch` for tree compilation** (Expr, Query).
  Adding a new node type = one register; adding a new compilation
  target = one new module.
- **Free functions for spec → SQL emission** (DDL, migrate, write,
  resolver, weight). The Spec is a fixed shape, not a recursive
  heterogeneous tree — dispatch doesn't earn its keep.
- **`match` statements for type-discriminated dispatch** where the set
  is closed and small.
- **Compile-time validation over runtime checks.** Construction-time
  raises (typos in slot refs, bad enum coercions, malformed bindings)
  surface failures where the user can fix them; `Spec.validate()`
  catches cross-entity issues; `compile_*` assumes the spec is valid.
- **Match scope to what was actually requested.** Bug fixes don't get
  surrounding cleanup; one-shot operations don't get helper
  abstractions; three similar lines beats a premature factoring.
- **No `v0`/`v1`/`future-work` framing in code.** Either commit to a
  design or explicitly call it out as an open question in the PR
  description / commit message.

## What NOT to do

- Don't add a service, a router, FastAPI, or anything that mounts an
  HTTP route. This branch is a library.
- Don't reach for env-var config. Constants are rebindable; kwargs
  are kwargs.
- Don't reintroduce `knot.db`, `knot.api`, `knot.graph`, or
  `knot.extensions` modules. Those moved to history.
- Don't reintroduce `Pydantic` for spec entities — dataclasses are
  the design; the future Java port maps them to records / sealed
  interfaces / enum.
- Don't reintroduce `.fk()`, string-shorthand types, `Primitive.TEXT`
  in user-facing code, `spec.bind()`, `binding.map()`, or the
  `accuracy` field name. Those got removed deliberately.
- Don't add GraphQL emission or Pydantic row-model emission inside
  `knot/` yet. Those are adapter-package territory. The current
  library compiles to SQL; the rest is downstream.
- Don't paper over operator agency at runtime — the weight seed is
  INSERT-only, deliberately. Don't add an `--overwrite` flag.
- Don't reintroduce 0..1 / probability constraints on weights, or
  rename them back to "trust". Weights are opaque floats by design;
  calibration is an external concern.
- Don't add label-only fields to entities. ``Spec.id`` and
  ``Spec.version`` were dropped because they never reached the
  compile path — purely documentation. If a team wants to label
  the spec, that lives in the codebase (filename, module name,
  repo), not on the dataclass.
- Don't reintroduce ``knot.compile.migrate`` / ``diff_against_db``
  or any flavor of "introspect the live DB and emit reconciling
  SQL." Schema deployment is a tool-delegation problem (sqldef /
  Atlas / dbmate); knot's job ends at ``Spec.ddl()``. The full
  rationale + tool recommendations are in the **Schema deployment**
  section; the prior code is in git history if you want to read why
  it didn't earn its keep.

## Smell audit — patterns we've eliminated

Catalog of design smells that earlier versions of knot carried and
that we deleted. Use these as patterns to watch for going forward;
the closing question for any new addition should be "does this fit
the same shape as anything below?"

| smell | what it was | why it was bad | how to spot it |
|---|---|---|---|
| **God-Spec methods** | ``spec.assign_canonical``, ``spec.compile_query``, ``spec.add_constraint(primary=cls, …)`` | The method talked about an entity (binding, query, class) but lived on ``Spec`` — required passing the entity in as an arg, redundant with ``self`` | Method takes an entity kwarg that pins which entity it's about. Move to that entity. |
| **Runtime data in the compile API** | ``binding.write(rows)`` json.dumps-ed rows inside knot; ``emit_assign_canonical(canonical_id=…)`` packaged values into params dict | knot is a SQL compiler. Rows / IDs / metadata are runtime data the host's connector binds. Mixing them blurs the line and forces knot to own serialization. | Compile function takes runtime values as args. Hand back SQL templates with named placeholders; let the host bind. |
| **Wrapper dataclasses with no behavior** | ``ClassWrites(binding=…, rows=…)``, ``BatchWrite(statements=…)`` | Container with no methods, just transport. Adds API surface (import, construct, unpack) for no payoff. | A dataclass whose sole job is to pass two adjacent fields to another function. Use a tuple, dict, or pass them directly. |
| **Bundled policy in the SQL** | ``enforce=True`` appended a PL/pgSQL DO block to the write SQL; ``json.dumps`` inside ``assign_canonical`` | Library hardcoded one policy ("any violation → rollback"); host couldn't pick "delta-only" or "scheduled sweep" without bypassing the API. | An emitter takes a policy-shaped kwarg (``enforce``, ``strict``, ``on_conflict``). Split into primitives; let the host compose. |
| **Label-only fields** | ``Spec.id``, ``Spec.version`` | Never embedded in SQL, never structural. Just typing overhead at construction. | A required field that's never read by ``compile/*``. Drop it. |
| **Defaulted opinions** | trust as float in [0, 1] with CHECK constraint; ``schema="knot_data"`` default; ``base_trust=0.67`` | Hid a calibration / probability / naming opinion that didn't earn its keep. | A default that's "the conventional thing for this domain" rather than "the simplest thing that compiles". Make it required, or drop the value entirely (rename to be opaque). |
| **Per-entity redeclaration of universal facts** | ``cls.slot("canonical_id", types.TEXT, identifier=True)`` on every class | Same line, every class, every spec. Identifier slot is a spec-level convention; per-class declaration is noise. | A line that gets copy-pasted across N entities. Promote to a spec-level field, apply automatically. |
| **Spec versioning by duplication** | notebook's ``spec_v1`` / ``spec_v2`` / ``spec_v3`` rebuilt the whole spec for each "version" | Hides the actual migration story (mutate one spec, re-emit ``Spec.ddl()``, let the migration tool diff against live). Fake versioning. | Multiple spec objects with overlapping definitions. Mutate one spec in place; expose a stage marker for marimo-style cell deps. |
| **Strings where objects exist** | ``emit_assign_canonical(source_name="imdb", class_name="Movie")``; ``Query.target_suffix: str = "_resolved"`` threaded through every ``compile_sql`` variant | The user has the ``Source`` / ``OntologyClass`` objects in scope, and the compile path has the ``Layer`` enum — strings force name-lookups and turn typos into runtime "relation does not exist" errors. | A kwarg / field that takes a string when an enum or already-resolved object would carry the same information. Take the typed value (``Layer.RESOLVED`` / the ``Source`` object). |
| **Spec-level operations that span all entities** | ``spec.emit_batch_write([ClassWrites…])`` taking a list when each binding could just expose its own ``.write_sql()`` | The "do this for many entities" function bundles what should be N independent operations. Host can compose them via the language (loops, transactions) without a library helper. | A spec method that loops over entities calling the same per-entity emitter. Move the work onto each entity; let the host iterate. |
| **Silent semantic defaults** | ``cls.where(...)`` / ``.order_by(...)`` / etc. on ``OntologyClass`` silently routed to ``<class>_resolved`` | The layer choice (resolved vs raw bindings vs per-source provenance) is load-bearing semantics — picking one by default hid the choice. ``Query.from_source(s)`` compounded it by producing valid SQL against the wrong layer (filtered ``source_name`` on the resolved view → zero rows, no error). | A method that silently picks one of several semantically-different shapes. Force the choice to surface: three entry points (``cls.resolved``, ``cls.all_sources``, ``cls.from_source(s)``) each *return* a Query rather than letting one mode masquerade as the default. |
| **Owning a concern outside the compiler's scope** | ``knot.compile.migrate.diff_against_db`` — Alembic-style autogen that introspected the live DB and emitted reconciling SQL. ~1k lines + tests. Worked for simple cases but added a homegrown autogen as a load-bearing piece of every deploy. | Forced the pitch *"the diff is automatic and we wrote it ourselves"* on a team — the exact pitch any senior engineer pushes back on. Also created an open-ended edge-case maintenance commitment (every new postgres feature → bespoke introspection logic in knot; vector slots immediately surfaced HNSW over-drop + silent dim/metric mismatch). | A subsystem whose job overlaps with a mature external tool (sqldef, Atlas, dbmate for migrations) and whose correctness story depends on edge-case introspection. Delete it and document the delegation pattern; "knot compiles, your tool reconciles" is the same posture as "knot emits SQL, your host opens connections." |

When adding a new API surface, run through this list. If the new
shape matches any row, propose the alternative before committing.

## Auto-memory

`~/.claude/projects/-mnt-main-code-knot/memory/MEMORY.md` carries
durable user-style cues. The most relevant for working in this tree:

- `feedback_design_thinking_style.md` — Nick's recurring patterns
  (concision, comparative anchoring, cull-claims-that-don't-earn).
- `prerelease_no_deprecation.md` — refactor by deletion; no legacy
  retention paths.
- `feedback_cli_json_first.md` — programmatic introspection uses
  `--json` or MCP, never the rendered TUI.
- `feedback_keep_pushing.md` — mid-workflow, just continue; don't
  ask whether to keep going.
