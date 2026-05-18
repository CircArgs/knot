# CLAUDE.md — knot library

**knot** is a reflective ontology compiler — a pure Python library that
takes a typed dataclass spec (classes, slots, sources, source bindings,
constraints) and emits the runtime artifacts (postgres DDL, resolved
views, per-slot weight seed, batch writes, migration ops, query SQL).

Branch `library/v0` is the focused library. Anything that talks to a
connection, serves HTTP, holds runtime state, or assembles a GraphQL
endpoint lives outside the library — in a *reference adapter* a team
builds around it. The earlier monorepo (API service + UI + ingest + ER
+ AI) is in git history on `draft-rfc` and `main`.

## Posture

- **Pure library.** No FastAPI, no HTTP, no `psycopg.connect`, no
  ingest path inside `knot/`. Compile functions return SQL strings;
  the host runs them (and binds params for the few write/seed
  emitters that emit named placeholders).
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
  call `binding.write_sql()` to get `(close_out_sql, insert_sql)`
  and execute each with `{"rows": rows}` bound by the driver.
- **ER workers** (Temporal workflows). Look at unresolved bindings,
  decide canonical_ids (whatever scoring / matching policy the team
  owns), call `binding.assign_canonical_sql()` or
  `binding.recanonicalize_sql()` and bind `{"canonical_id": …,
  "source_identifier": …, "er_metadata": json.dumps({...}) or None}`
  via the connector. Ingest cadence and ER cadence are independent
  — that decoupling is why these are separate workflows.
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

## Constraint enforcement

knot doesn't bundle constraint checks into the write SQL. There are
three independent primitives:

1. `binding.write_sql()` — close-out + insert SQL templates. No
   constraint awareness.
2. `spec.emit_validation()` — one SELECT per constraint. No writes.
3. `pg.transaction()` — host opens it, runs (1), runs (2), decides
   whether to commit or rollback.

The host composes them. The canonical "ingest with enforcement"
shape is ~10 lines:

```python
def ingest_with_enforcement(binding, rows, *, spec, pg, schema):
    close_out, insert = binding.write_sql(schema=schema)
    payload = json.dumps(rows)
    severity = {c.name: c.severity for c in spec.constraints}

    with pg.transaction(), pg.cursor() as cur:
        cur.execute(close_out, {"rows": payload})
        cur.execute(insert,    {"rows": payload})
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
`*_resolved` view, so pre-existing violations in unrelated rows
would also block your write under the simple "block on any
violation" policy. Production deployments usually pick one of:
scope to just-touched canonical_ids (knot doesn't emit that variant
today — would need a `scope_to=` kwarg), delta-only blocking
(baseline vs post-write counts), or fully decoupled validation
(separate scheduled workflow, never blocks ingest). In a Temporal
deployment, the "decoupled scheduled sweep" pattern is usually the
right default with delta-only blocking layered in for the rules
where blocking is genuinely required.

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
    ddl.py             # canonical tables + bindings tables + indexes
                       # + FK ALTERs + source_weight table + virtual
                       # class views
    resolver.py        # per-(source, class, slot) argmax resolved views
    constraints.py     # constraint validation SELECTs
    data_io.py         # batch SCD2 writes (close-out + insert)
    weight.py          # source_weight INSERT-only seed
    migrate.py         # diff_against_db (Alembic-style autogen) — the
                       # single source of truth for "what SQL to run";
                       # ``Spec.init_sql`` is a one-line façade over it
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
# Single-binding write — SQL templates only; host binds rows via the connector.
close_out, insert = imdb_movie.write_sql(schema="knot_data")
with pg.transaction(), pg.cursor() as cur:
    cur.execute(close_out, {"rows": json.dumps(rows)})
    cur.execute(insert,    {"rows": json.dumps(rows)})

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

# Schema deploy or migrate — one SQL script, ready to execute.
sql = spec.init_sql(schema="knot_data")        # query_fn=None → full create
sql = spec.init_sql(query_fn=q, schema="knot_data")  # introspect → diff only
pg.execute(sql)

checks = spec.emit_validation(schema="knot_data")              # [(name, sql), …]
```

Per-entity runtime methods live on the entity. Every one returns
SQL templates only — no row data, no value args, no `json.dumps`
inside knot. The host binds via the connector.

```python
sql               = q.sql(schema="knot_data")                  # literals inlined
close_out, insert = binding.write_sql(schema="knot_data")      # both ref %(rows)s::jsonb
sql_assign        = binding.assign_canonical_sql(schema=…)     # %(canonical_id)s, %(source_identifier)s, %(er_metadata)s
sql_recan         = binding.recanonicalize_sql(schema=…)       # %(new_canonical_id)s, %(source_identifier)s, %(er_metadata)s
sql_close         = binding.close_out_sql(schema=…)            # %(canonical_id)s, %(source_identifier)s
```

The read path lives on the query, not the spec. The write/ER path
lives on the binding, not the spec. Rows + values never enter the
compile API — knot emits SQL templates with `%(named)s::jsonb`
placeholders, the host's connector binds actual data. Multi-binding
atomic write = multiple `binding.write_sql()` calls, all run in one
`pg.transaction()`.

**Façade contract.**

- Every method calls ``Spec.validate()`` first; an invalid spec
  raises ``SpecError`` instead of compiling. Free functions in
  ``knot.compile.*`` do not validate — they're the back door for
  "compile this known-broken spec anyway" cases (mostly tests).
- ``init_sql`` is a thin shim over ``diff_against_db``:
  ``query_fn=None`` substitutes an empty-DB callable, so an empty
  schema gets the full create sequence and a populated schema gets
  only the delta. One code path, two modes.
- ``Query.sql`` returns a SQL string with literals inlined — no
  parameter list, the host calls ``cur.execute(sql)`` and is done.
- The binding compile methods (``binding.write_sql``,
  ``binding.assign_canonical_sql``, ``binding.recanonicalize_sql``,
  ``binding.close_out_sql``) return SQL templates with named
  placeholders (``%(rows)s::jsonb``, ``%(canonical_id)s``, …). The
  host binds runtime data via its connector.
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
| **Spec versioning by duplication** | notebook's ``spec_v1`` / ``spec_v2`` / ``spec_v3`` rebuilt the whole spec for each "version" | Hides the actual migration story (mutate one spec, ``init_sql`` diffs against live DB). Fake versioning. | Multiple spec objects with overlapping definitions. Mutate one spec in place; expose a stage marker for marimo-style cell deps. |
| **Strings where objects exist** | ``emit_assign_canonical(source_name="imdb", class_name="Movie")``; ``Query.target_suffix: str = "_resolved"`` threaded through every ``compile_sql`` variant | The user has the ``Source`` / ``OntologyClass`` objects in scope, and the compile path has the ``Layer`` enum — strings force name-lookups and turn typos into runtime "relation does not exist" errors. | A kwarg / field that takes a string when an enum or already-resolved object would carry the same information. Take the typed value (``Layer.RESOLVED`` / the ``Source`` object). |
| **Spec-level operations that span all entities** | ``spec.emit_batch_write([ClassWrites…])`` taking a list when each binding could just expose its own ``.write_sql()`` | The "do this for many entities" function bundles what should be N independent operations. Host can compose them via the language (loops, transactions) without a library helper. | A spec method that loops over entities calling the same per-entity emitter. Move the work onto each entity; let the host iterate. |
| **Silent semantic defaults** | ``cls.where(...)`` / ``.order_by(...)`` / etc. on ``OntologyClass`` silently routed to ``<class>_resolved`` | The layer choice (resolved vs raw bindings vs per-source provenance) is load-bearing semantics — picking one by default hid the choice. ``Query.from_source(s)`` compounded it by producing valid SQL against the wrong layer (filtered ``source_name`` on the resolved view → zero rows, no error). | A method that silently picks one of several semantically-different shapes. Force the choice to surface: three entry points (``cls.resolved``, ``cls.all_sources``, ``cls.from_source(s)``) each *return* a Query rather than letting one mode masquerade as the default. |

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
