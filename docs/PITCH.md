# knot + hitch — team pitch

**knot** is a typed Python compiler that turns a multi-source MDM spec
into postgres SQL — DDL, reads, ER writes, validation. Pure library,
zero connections, zero scheduler.

**hitch** is the reference application that consumes knot. It wires
Temporal (ingest / embed / ER / validation orchestration), FastAPI +
Ariadne (GraphQL API), pgvector (cosine k-NN), and sentence-transformers
(real embeddings) into a runnable end-to-end stack. The whole demo —
postgres-pgvector + Temporal cluster + worker + API — comes up with
`docker compose up`.

This doc is the pitch.

---

## The problem

Three media-data providers (imdb, tmdb, rottentomatoes) each have an
opinion about the same movie. They disagree on runtime, on title case,
on whether "Paul Thomas Anderson" should also be "P. T. Anderson". A
typical MDM stack solves this with a heavyweight commercial product
(Reltio, Tamr, Senzing) that bundles the spec, the deploy engine, the
write pipeline, the matching policy, and the runtime into one
opinionated box.

knot ships only the **compiler layer**:

- **Spec** — a typed Python module that declares classes, slots, FK
  references, constraints, virtual subclasses, sources, and per-source
  bindings.
- **Compile** — `Spec.ddl()` emits the canonical CREATE script
  (tables, indexes, resolved-view, all-sources view, weight table).
  `Query.sql()` compiles a typed read AST to one SQL string.
  `binding.write_sql()` / `assign_canonicals_sql()` /
  `translate_fks_sql()` / `retract_sql()` emit SQL templates the host
  binds with rows / ER decisions / cleanup directives.

That's it. No connections, no Temporal coupling, no HTTP routes, no
ER policy decisions baked into the library. The host supplies all of
that — and hitch is one such host, ready to swap.

The pitch is that this split makes the substrate **trustable** the way
jOOQ or LinkML are trustable: it does one thing (compile a typed spec
to SQL), and you keep your existing runtime, your existing deploy
tooling, your existing ER policy choices.

---

## What you get from knot (the substrate)

### A spec that's the single source of truth

```python
movie = spec.add_class("Movie")
movie.slot("title", types.TEXT, required=True)
movie.slot("year", types.INTEGER)
movie.slot("director", person)                          # FK
movie.slot("title_embedding", types.VECTOR(384, metric="cosine"))
movie.slot("mpaa_rating", types.ENUM("G","PG","PG_13","R","NC_17","NOT_RATED"))

movie.add_constraint("year_sane", body=movie.col.year >= 1888)
movie.add_constraint(                                   # FK chain
    "director_alive_at_release",
    body=movie.col.director.birth_year.is_null()
       | movie.col.year.is_null()
       | (movie.col.director.birth_year < movie.col.year),
)
movie.add_virtual("RecentMovie", where=movie.col.year >= 1995)
```

That spec compiles to:
- `Spec.ddl()` — the postgres schema (bindings tables + resolved
  views + all_sources views + weight table + HNSW indexes).
- `spec.emit_validation()` — one SELECT per user constraint + 10
  built-in invariants (FK-orphan checks, required-null checks).
  Returns rows of violators.
- Per concrete class, three read entry points — `cls.resolved`,
  `cls.all_sources`, `cls.from_source(src)` — each typed as a
  `Query` you compose with `.where`, `.order_by`, `.select`,
  `.limit`, FK chain walks, and correlated aggregates.

### A resolver that runs per-slot argmax over your weights

Per (source, class, slot) weights live in a `source_weight` table you
mutate at runtime. The `<class>_resolved` view picks the
highest-weight non-NULL claim per slot, per canonical_id. Tie-break is
alphabetical-source-name (deterministic, undocumented in v0 — flagged
for the v1 readme). NULL claims are skipped by the argmax so a
high-weight source that doesn't have a value for slot X loses to a
lower-weight source that does.

The `<class>_all_sources` view exposes the full per-source provenance
as `{source: {value, weight}, …}` jsonb, scoped per slot. Combined
with `cls.explain_winner_sql(slot=…)` that breaks down per-row,
per-source value/weight/is-winner/margin — full read-side
observability.

### ER as a first-class write primitive

`binding.assign_canonicals_sql()` is **one atomic statement** (a chain
of writable CTEs) that does **four things at once**:

1. **Stamp** — `UPDATE <class>_bindings SET canonical_id = …` on the
   row's (source, source_identifier) where `canonical_id IS NULL`.
2. **Forward FK translation** — for each `ClassRef` slot on this
   class, look up the column's current value in the target binding's
   table (same source's namespace) and rewrite source-id →
   canonical-id.
3. **Backward fan-out** — for each `(referencing_class, fk_slot)`
   that references this class, UPDATE the referencer's FK column
   where it currently holds this row's source-id (gated on
   `EXISTS (stamp)` for strict idempotency).
4. **Register** — implicit via the bindings table itself (knot
   deliberately doesn't have a separate canonical registry table).

Re-running on already-stamped rows is a strict no-op. ER decisions are
deterministic when the policy that produced them is deterministic.

### A new primitive: `binding.translate_fks_sql()`

Bug we hit during hitch development: `write_sql`'s
`ON CONFLICT DO UPDATE` preserves `canonical_id + er_metadata` but
overwrites every other slot, including FK columns. A re-ingest after
ER wiped the canonical-id translation back to source-ids; ER's
stamp guard (`canonical_id IS NULL`) then refused to re-fanout.

The new primitive emits one standalone UPDATE per FK slot, joining
the target binding's `(source_id → canonical_id)` map. Idempotent
(the WHERE matches only source-id-shaped values). The host runs it
after every upsert. hitch's IngestWorkflow does this automatically.
The fix is in knot proper — every host with re-ingest cadence
needs it.

### Constraint enforcement is a host concern, not a library opinion

`spec.emit_validation()` returns `[(name, sql)]` pairs. The host
decides the policy:

| policy | host shape |
|---|---|
| **Block on any violation** | raise on first non-empty SELECT → rollback |
| **Block on NEW violations only** | snapshot baseline counts pre-write, check delta |
| **Block on specific rules** | filter by name or severity |
| **Don't block (sweep)** | run validation on a Temporal schedule, page on ERROR |
| **Soft-fail per row** | bucket the batch into clean+violating, commit clean, route violators to a review queue |

All five flow from the same three primitives (`write_sql`,
`emit_validation`, `pg.transaction()`). hitch picks the **scheduled
sweep** for the demo; switching to **block-on-error inline** would be
a 10-line activity change.

### Built-in constraints — knot derives invariants from the spec shape

| family | trigger | severity |
|---|---|---|
| `_builtin_fk_orphan_<Class>_<slot>` | every `ClassRef` slot — FK value doesn't match a canonical_id in the target's resolved view | ERROR |
| `_builtin_required_null_<Class>_<slot>` | every required non-identifier slot — every source's claim was NULL | WARNING |

Hosts can opt out via `spec.emit_validation(include_builtins=False)`.

---

## What hitch adds (the host)

hitch is the reference deployment that consumes knot. Read it as
**"here's what a real team would build around knot"**, not as part of
knot. Its architecture:

```
   postgres ◀── pgvector/pg16
       ▲
       │
   ┌───┴───────────────┐    ┌─────────────────────┐
   │   hitch-api       │    │  hitch-worker       │
   │   FastAPI +       │    │  Temporal worker    │
   │   Ariadne         │    │                     │
   │   /graphql        │    │  Workflows:         │
   │                   │    │   IngestWorkflow    │
   │   reads only,     │    │   EmbedWorkflow     │
   │   resolved view   │    │   ERWorkflow        │
   │                   │    │   ValidationSweep   │
   └───────────────────┘    │                     │
                            │  All activities are │
                            │  sync — knot is sync│
                            │  + psycopg is sync. │
                            └──────────┬──────────┘
                                       │
                            ┌──────────┴──────────┐
                            │  temporal cluster   │
                            │  (auto-setup +  UI) │
                            └─────────────────────┘
```

### Temporal orchestration (the design that earned its keep)

- **Per (source, class) `IngestWorkflow`** — fetch seed JSON → run
  `binding.validate_rows_sql()` to bucket clean from violating →
  `binding.write_sql()` upsert → `binding.translate_fks_sql()`
  recovery. Idempotent throughout.
- **Per (source, class, embedding-slot) `EmbedWorkflow`** — query
  bindings with NULL embedding → call sentence-transformers (real,
  not mocked) → `binding.update_slot_sql("title_embedding")` write.
  Loops to drain.
- **Per class `ERWorkflow`** — two policies wired up:
  - **Embedding k-NN** for Person/Studio/Movie — per source, run
    one pgvector cosine-distance query that finds the nearest
    already-stamped binding in OTHER sources; below-threshold reuse
    the neighbor's canonical_id; above-threshold mint a fresh
    sha1-of-identity (deterministic).
  - **Composite sha1-of-FKs** for Credit — its identity is
    `(movie_canonical, person_canonical, role)`; after Movie + Person
    ER stamps the FK columns canonical, sha1 over those collapses
    cross-source claims cleanly.
- **`ValidationSweepWorkflow`** — runs `spec.emit_validation()` and
  reports per-rule violation counts.

The replay-safety story holds because:

- **Workflow code never imports knot's emitters.** Activities do.
  Compiling a SQL string is deterministic, but the moment you do it
  inside a workflow you've leaked the spec module reference into the
  workflow history — a redeploy with a different spec replays into
  poisoned state. hitch enforces this contractually
  (`docs/temporal-adapter.md`).
- **Every knot SQL primitive is genuinely idempotent.** write_sql is
  upsert; assign_canonicals stamps `WHERE canonical_id IS NULL` +
  EXISTS-guards fanout; update_slot positional-matched; retract is a
  DELETE.
- **Mints are deterministic.** sha1-of-identity for fallback mints,
  sha1-of-FKs for Credit. Re-runs of the same input converge to the
  same canonical_id.

### GraphQL API (built atop knot_graphql)

`knot_graphql` is a sibling adapter package — given any spec, it
emits one SDL string + a universal `Resolvers` class. hitch wires
that into a real FastAPI + Ariadne server with:

- **Per-request memoization** for FK walks and reverse-count fields —
  Tarantino in N credits triggers ONE lookup, not N (~50%
  improvement on deep queries in the demo's scale).
- **Cursor + input validation** — `first: -5` and bogus `after:`
  cursors return clean 400s instead of leaking psycopg stacktraces or
  silently returning the whole list.
- **`debug=False`** on Ariadne — no resolver-local context dump on
  error.
- **Mutations** — one `correct<Class>` per concrete class plus
  `retract`. Mutations write to the `_user_corrections` binding
  (weighted 1e6 in `source_weight` → dominates the resolver argmax).
  Read-after-write returns the post-correction resolved row in one
  round-trip.
- **`canonical_id` snake_case throughout** — fields and args
  consistent, no codegen hybrid identifiers.

### Embedded vectors are real, not mocked

The worker container pre-downloads
`sentence-transformers/all-MiniLM-L6-v2` (384-d, normalized). Every
identity string (`Person.name`, `Studio.name`, `Movie.title`) gets a
real embedding per source. The k-NN ER query uses pgvector's `<=>`
cosine operator. The HNSW index is emitted in DDL (`vector_cosine_ops`)
and exists in postgres — the planner seq-scans only because the demo
has 16 rows; at any production cardinality the index kicks in.

### Concrete demo numbers (small-but-real)

Demo spec: 4 concrete classes (Person/Studio/Movie/Credit) + 6 virtual
classes (DirectedMovie/RecentMovie/ClassicMovie/LongMovie/Blockbuster/
LivingPerson) + **16 user constraints** + 10 builtin = **26
constraints evaluated** per sweep.

Demo data: 5 movies (Reservoir Dogs, Pulp Fiction, Boogie Nights, The
Shining, Eyes Wide Shut), 4 studios, 6 unique people, 7 credits —
across 3 sources with deliberate variance ("Miramax" vs "Miramax
Films", "P. T. Anderson" vs "Paul Thomas Anderson", USA vs United
States) to stress the ER.

Ingest produces **35 bindings rows**. Embedding workflow computes
**33 vectors** (one per source per row with a name/title). ER
results:

| class | bindings | canonicals | mints | matches |
|---|---|---|---|---|
| Person | 16 | 6 | 6 (imdb) | 10 (tmdb→imdb, rt→imdb/tmdb) |
| Studio | 9 | 4 | 4 | 5 |
| Movie | 12 | 5 | 5 | 7 |
| Credit | 17 | 17 | — (sha1 of FKs) | — |

PTA fused. Miramax fused. Warner Bros. fused across all 3 sources.

Validation sweep: 25/26 constraints pass; the one violation is
`movie_has_director_credit` firing for Eyes Wide Shut (we
deliberately seeded the movie sans a director credit to demo the
correlated-aggregate constraint catching real data quality issues).

### Test coverage

446 unit + knot_graphql tests green. 18 end-to-end integration tests
on live postgres green. Per-knot-primitive coverage:

- `emit_translate_fks_sql` — 11 tests
- `emit_assign_canonicals_sql` — full coverage
- `emit_validation` (with FK chain JOIN emission, recently fixed) — covered
- Constraint NULL-safety patterns — covered

---

## What this doc is honest about

### Open gaps (with concrete next steps)

- **GraphQL N+1** — per-request memoization halved roundtrips, but a
  proper DataLoader (batched IN-list queries) would be the next
  step. Acceptable at demo scale; mandatory for production. The
  surface change lives in `knot_graphql.Resolvers`, not in knot.
- **Schema evolution** — `Spec.ddl()` is `CREATE IF NOT EXISTS`-only.
  Adding a slot crashes at view-rebuild time with `UndefinedColumn`
  (loud, not silent). Use Atlas / sqldef / dbmate to diff the new
  target schema against the live DB; the recommended Atlas flow is
  documented in `CLAUDE.md`. knot deliberately doesn't bundle the
  diff — that's where the prior `diff_against_db` autogen lived,
  and it lost the trust battle vs. mature external tools.
- **Reverse-count fields on virtual classes** — knot_graphql emits
  them only on concrete types. A virtual subclass loses the reverse
  count even though FK walks work. The codegen change is small if
  it earns its keep for v1.
- **ER metadata is shallow** — `er_metadata` records `policy +
  source + method`. Adding run_id, decision-time inputs, and human
  reviewer (when applicable) is straightforward and the er_metadata
  jsonb column is already there.
- **No mutation auditing yet** — corrections go straight to the
  bindings table. A reviewer-of-corrections workflow that gates
  high-weight corrections on a second pair of eyes is a v1 add.

### Smells we already deleted

CLAUDE.md catalogues design dead-ends we ran into and removed. The
short version: every "let knot decide" reflex got pushed back to the
host. The library got smaller. The post-deletion shape:

- knot owns the spec → SQL compilation, the resolved view definition,
  the ER cascade SQL, the validation SQL, the weight table shape.
- The host owns the connection, the migration tool choice, the ER
  policy, the validation enforcement policy, the workflow engine
  choice, the auth surface, the API style.

That split is the pitch.

---

## How to run it

```bash
cd hitch
docker compose up --build           # postgres + temporal + api + worker
docker compose exec hitch-worker python -m scripts.deploy    # apply DDL + seed weights
docker compose exec hitch-worker python -m scripts.run_demo  # ingest → embed → ER → validate

# Query the live API
curl -sL -X POST http://localhost:8000/graphql/ \
  -H 'content-type: application/json' \
  -d '{"query":"{ movieList { title director { name movieDirectorCount } studio { name } } }"}'

# Write a correction via GraphQL
curl -sL -X POST http://localhost:8000/graphql/ \
  -H 'content-type: application/json' \
  -d '{"query":"mutation { correctMovie(canonical_id:\"m_X\", source_identifier:\"my-fix\", year: 1995) { title year } }"}'
```

Open the Temporal UI at <http://localhost:8088> to inspect workflow
histories. Open the live SDL at <http://localhost:8000/sdl> to see
what's exposed.

---

## TL;DR

- **knot** is a small, typed, sync, no-I/O library that compiles a
  multi-source MDM spec into postgres SQL. It ships nothing else.
- **hitch** wraps it in real-world infrastructure (Temporal +
  pgvector + FastAPI + sentence-transformers + Ariadne) to prove
  the substrate is end-to-end deployable.
- The split is deliberate. The host gets to choose its migration
  tool, ER policy, validation enforcement shape, API framework,
  workflow engine, and auth without fighting the library.
- Embedding-driven ER (k-NN with sha1-mint fallback) fuses
  cross-source variants ("Miramax Films" / "Miramax", "P. T.
  Anderson" / "Paul Thomas Anderson") that string-equality policies
  would split.
- GraphQL mutations close the read+write loop via the
  `_user_corrections` binding's dominant 1e6 weight.
- 446 unit + 18 integration tests green; demo runs end-to-end in
  ~30 seconds on fresh data.

If the team adopts knot, every existing piece (migrations, workflow,
auth, API shape) stays. Only the spec → SQL → resolved-view layer
gets replaced.
