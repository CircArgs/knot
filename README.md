<p align="center">
  <img src="docs/header.png" alt="knot" height="120">
</p>

A reflective ontology compiler — a pure Python library that takes a
typed dataclass spec (classes, slots, sources, source bindings,
constraints) and emits the runtime artifacts: postgres DDL, resolved
views, per-slot weight seed, batch SCD2 writes, ER stamping SQL, and
query SQL.

knot is a **compiler**, not a runtime: every method returns SQL
strings (or SQL templates with named placeholders). The host owns
connections, transactions, ingest scheduling, ER policy, and the
choice of migration tool.

```python
from knot import Spec, types

spec = Spec(identifier_slot_name="canonical_id", schema="knot_data")

person = spec.add_class("Person")
person.slot("name", types.TEXT, required=True)

movie = spec.add_class("Movie")
movie.slot("title", types.TEXT, required=True)
movie.slot("year", types.INTEGER)
movie.slot("director", person)                    # FK — pass the class
movie.slot("title_embedding", types.VECTOR(384))  # pgvector + HNSW

imdb = spec.add_source("imdb")
imdb_movie = imdb.bind(movie)

# Compile.
pg.execute(spec.ddl())                            # full target schema
close_out, insert = imdb_movie.write_sql()        # SCD2 batch ingest
q = movie.resolved.order_by(movie.col.year, "desc").limit(10)
pg.execute(q.sql())                               # read substrate
```

## Walkthrough

`notebooks/` ships a 5-step demo against a live postgres+pgvector,
end-to-end:

1. **01_deploy** — deploy the v1 spec (Person + Movie + imdb)
2. **02_ingest** — ingest imdb movies → bindings (canonical_id NULL)
3. **03_migrate** — `import media_spec.full` grows the spec to
   17 classes / 14 sources / 56 bindings (movies + games + podcasts +
   tv + webscraped mentions); Atlas diffs + applies the schema
   change against the live DB
4. **04_ingest_more** — ingest the 55 newly-enabled feeds
5. **05_er** — `sentence-transformers` embeddings + HNSW k-NN
   cross-source matching → resolver view fills

The spec is a package, FastAPI-router style:

```
notebooks/media_spec/
  base.py         # spec = Spec(...)
  person.py       # Person + extensions
  movies.py       # Movie + MovieCredit + 3 sources
  games.py        # Studio · Platform · Game · Release · GameCredit
  podcasts.py     # Podcast · PodcastEpisode · PodcastCredit
  tv.py           # Show · Season · TVEpisode · TVCredit
  webscraped.py   # Mention (low-trust, raw_payload preservation)
  full.py         # spec.include(games.part); spec.include(podcasts.part); …
```

Each domain owns a self-contained `part = Spec(...)`; the root
`base.spec` composes them via `spec.include(part)`.

## Posture

- **Pure library.** No HTTP, no connection pool, no scheduler — the
  host owns those. Compile functions return SQL strings; the host's
  connector binds parameters and executes.
- **No migration runtime.** `Spec.ddl()` emits the canonical target
  schema; teams pipe it through their own migration tool. Atlas is
  the tested default ([CLAUDE.md §"Schema deployment"](CLAUDE.md)
  has the recipe + alternatives).
- **Postgres-only today.** All emitters target postgres. A future
  Trino / Spark / cypher dialect lands as a sibling dispatch table.
- **Hot-path methods don't validate.** `Query.sql`, `binding.write_sql`,
  etc. skip `spec.validate()` — that runs once at deploy time via
  `Spec.ddl()` or at host startup. Per-request walks of N classes is
  wasteful.
- **Single-team posture.** Trusted spec authors, no multi-tenant
  defenses.

## Layout

```
knot/
  spec.py                    Spec + OntologyClass + Source + SourceBinding
                             + Constraint + Slot + Severity + ClassKind
  ast/
    types.py                 types.TEXT, …, types.ARRAY(…), types.VECTOR(…)
    expr.py                  Expr AST + ``this`` correlator + Aggregate
    select.py                Query AST + Layer enum
  compile/
    ddl.py                   schema + tables + indexes + FK ALTERs + views
    resolver.py              <class>_resolved / _all_sources views
    constraints.py           per-constraint validation SELECTs
    write.py                 SCD2 ingest + ER stamping SQL templates
    weight.py                source_weight seed
    expr.py                  @singledispatch compile_sql over Expr
    query.py                 @singledispatch compile_query over Query
```

## Development

```bash
# Editable install
.venv/bin/pip install -e .

# Unit tests (pure, no I/O)
.venv/bin/pytest tests/unit/ -q

# Integration tests against the live pgvector postgres on :5433
docker compose up -d postgres
.venv/bin/pytest tests/integration/ -q

# Lint + format
.venv/bin/ruff check knot/ tests/
.venv/bin/ruff format knot/ tests/

# Rebuild the notebooks (marimo .py → executed .ipynb)
cd notebooks && bash build.sh
```

See [CLAUDE.md](CLAUDE.md) for the design rules and posture, the
smell audit (patterns we've eliminated), and the migration-tool
recommendations.
