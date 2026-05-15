# CLAUDE.md — knot library

**knot** is a reflective ontology compiler — a pure Python library that
takes a typed dataclass spec (classes, slots, sources, source bindings,
constraints) and emits the runtime artifacts (postgres DDL, resolved
views, per-slot trust seed, batch writes, migration ops, query SQL).

Branch `library/v0` is the focused library. Anything that talks to a
connection, serves HTTP, holds runtime state, or assembles a GraphQL
endpoint lives outside the library — in a *reference adapter* a team
builds around it. The earlier monorepo (API service + UI + ingest + ER
+ AI) is in git history on `draft-rfc` and `main`.

## Posture

- **Pure library.** No FastAPI, no HTTP, no `psycopg.connect`, no
  ingest path inside `knot/`. Compile functions return SQL strings (or
  `(sql, params)` pairs); the host runs them.
- **Postgres-only today.** All emitters target postgres. A future
  Trino / Spark / cypher dialect lands as a sibling module
  (`expr_sql_trino.py`, `query_sql_trino.py`, etc.) — same dispatch
  pattern, separate file per target. Not preemptively built.
- **Module-level constants, not env vars.** The default schema name
  (`knot_data`), the corrections source name (`_user_corrections`),
  the trust table name (`source_trust`) are exposed as kwargs on the
  emitter functions; their *defaults* are constants you can rebind
  before import. No `os.environ.get` anywhere.
- **Single-team posture.** Trusted authors of the spec, no
  multi-tenant defenses, no sandboxing.
- **Sync.** The compiler is sync (pure transforms). Adapters wrap it
  for async hosts if they want.

## Layout

```
knot/
  __init__.py          # public re-exports
  spec.py              # Spec, OntologyClass, VirtualClass, Slot,
                       # Source, SourceBinding, SlotMapping,
                       # Constraint, Severity, ClassKind
  types.py             # types.TEXT, types.INTEGER, …, types.ARRAY(…)
                       # — THE canonical type surface
  expr.py              # Expr AST: Ref, FkRef, FkChainRef, Compare,
                       # BoolOp, Not, IsNull, InList, Between,
                       # Exists, CountRel, Raw, This, Aggregate
                       # + `this` magic accessor for outer-scope refs
  select.py            # read substrate: Query, OrderBy
  compile/
    __init__.py
    ddl.py             # canonical tables + bindings tables + indexes
                       # + FK ALTERs + source_trust table + virtual
                       # class views
    resolver.py        # per-(source, class, slot) argmax resolved views
    constraints.py     # constraint validation SELECTs
    data_io.py         # batch SCD2 writes (close-out + insert)
    trust.py           # source_trust INSERT-only seed
    migrate.py         # diff_against_db (Alembic-style autogen)
    flyway.py          # render MigrationOps into Flyway V/R files
    expr_sql.py        # @singledispatch compile_sql over Expr nodes
    query_sql.py       # @singledispatch compile_query over Query nodes
tests/
  unit/                # pure unit tests (~210 tests; no I/O)
  integration/         # ~17 tests against live postgres on :5433
notebooks/
  query_playground.py  # end-to-end marimo playground (spec → DDL →
                       # ingest → query)
  builder_tinker.py    # spec builder tinker
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
```

**Sources and bindings** — source-method-chained:

```python
imdb = spec.add_source("imdb")
imdb_movie = imdb.bind(movie, base_trust=0.85)
imdb_movie.slot(class_slot="canonical_id", source_slot="imdb_id")
imdb_movie.slot(class_slot="year", source_slot="release_year", trust=0.9)
imdb_movie.slot(class_slot="runtime", source_slot="runtime",
                sql="(regexp_match(runtime, '[0-9]+'))[1]::int", trust=0.7)
# Slots not explicitly mapped → implicit passthrough at base_trust.
```

**Read substrate** — fluent immutable queries with outer-scope
correlation, transparent FK walks, per-slot aggregates:

```python
from knot import this

# Top 10 movies + their directors (FK walk + projection + order/limit)
q = (movie.order_by(movie.col.year, "desc")
          .limit(10)
          .select(movie.col.title, movie.col.director.name))

# Directors with more than 5 movies (correlation + count aggregate)
q = person.where((movie.col.director == this.Person).count() > 5)

# People who never directed (.none() aggregate)
q = person.where((movie.col.director == this.Person).none())

sql, params = spec.compile_query(q, schema="knot_data")
```

**Compile façade** — every emitter has an ergonomic method on `Spec`
that delegates to the corresponding `knot.compile.*` free function.
Use the methods in user code; the free functions stay as the
underlying implementations (adapters and tests call them directly).

```python
ddl_script       = spec.emit_ddl(schema="knot_data")            # → str
views_script     = spec.emit_resolved_views(schema="knot_data") # → str
trust_seed       = spec.emit_trust_seed(schema="knot_data")     # → [(sql, params), …]
validations      = spec.emit_validation(schema="knot_data")     # → [(name, sql), …]
batch_write      = spec.emit_batch_write(writes, schema="knot_data")  # → BatchWrite
migration_ops    = spec.diff_against_db(query_fn, schema="knot_data") # → [MigrationOp, …]
flyway_files     = spec.emit_flyway_files(migration_ops, version="v1", slug="init")
sql, params      = spec.compile_query(query_node, schema="knot_data")  # → (sql, params)
```

**Façade contract** (vs free functions):

- **Always validates first.** Every method calls
  ``Spec.validate_strict()`` before delegating; an invalid spec
  raises ``SpecError`` instead of compiling. The free functions in
  ``knot.compile.*`` do NOT validate — they're the back door for "show
  me what this broken spec would emit" cases (mostly tests).
- **DDL-shaped methods return a single SQL script**, blank-line
  separated, each statement ``;``-terminated. The free function
  returns ``list[str]`` for per-statement addressability; the façade
  joins for the common "just run it" call site.
- **Parameterized / per-element methods keep their list shape** —
  each element carries metadata (constraint name, op target) or
  per-row params that doesn't concatenate cleanly.

**Trust runtime**:
- Per-(source, class, slot) value lives in `<schema>.source_trust`.
- The seed emitter is **INSERT-only** (`ON CONFLICT DO NOTHING`). Spec
  values are *initial conditions*; once a row exists, the operator
  owns it. Redeploying the spec never clobbers operator tuning.

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
  resolver, trust, flyway). The Spec is a fixed shape, not a
  recursive heterogeneous tree — dispatch doesn't earn its keep.
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
- Don't paper over operator agency at runtime — trust seed is
  INSERT-only, deliberately. Don't add an `--overwrite` flag.

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
