# The source-layer contract

**Status:** authoritative. This is the canonical contract between the team's data ingestion (out of scope for knot) and knot's compiled workflows (which start at normalize). Other design notes that conflict with this document are wrong.

**Date locked:** 2026-04-29.

**Amended:** 2026-04-29 — source-to-ontology mapping replaced 1:1 source-to-class with an explicit discriminator-based dispatcher. Homogeneous shorthand preserved. See [Source-to-ontology mapping](#source-to-ontology-mapping).

**See also:** [`knot-as-compiler.md`](knot-as-compiler.md) — establishes that knot is a compiler that dispatches workflow tasks. This document specifies what knot's tasks expect to find in the lake when they run.

---

## The statement

> Knot starts at the lake. Sources are the team's responsibility — they put rows in the lake however they want, by whatever means they already use (batch jobs, Kafka consumers, scheduled scrapers, manual loads). Knot does not ingest. Knot's compiled workflows begin at the *normalize* stage and assume each registered source's data is already-in-lake at source-shape.
>
> Each `kind=source` node in knot is a *declaration of what's there*: where to find the source's rows, what shape they're in, which column identifies a row uniquely, and how the source maps to the ontology. The team owns getting data into that location and keeping it fresh; knot owns reading it from there onward.

This makes the seam between knot and the team's data infrastructure sharp: **team gets data into the lake; knot transforms, resolves, and publishes from there.** No `SourceReader` interface in knot core. No knot-managed credentials, schedules, or fetch logic.

## What knot needs to be true about a source's lake table

Four things. The team is responsible for ensuring all four hold.

### 1. It exists at a declared location

The source's node payload carries a `location` field. The value is a URI; the `QueryReader` impl bound for the deployment knows how to resolve the scheme.

Examples by lake impl:

| Lake impl | URI scheme | Resolves to |
|---|---|---|
| Toy scheduler (DuckDB+parquet) | `lake://<source>` | `/lake/<source>.parquet` |
| Iceberg | `iceberg://<catalog>.<namespace>.<table>` | Iceberg catalog lookup |
| Postgres-backed lake | `pg://<schema>.<table>` | direct table reference |
| S3+parquet | `s3://<bucket>/<prefix>/` | listed parquet files |

Knot core never resolves the URI. The `QueryReader` impl does. If a deployment switches lake backends, the URI schemes change but the source-node contract doesn't.

### 2. It conforms to the declared shape

The source's Pydantic spec declares the columns and their types. The lake table at the location is expected to match. Knot does not validate at the source layer — *normalize* is where structural validation runs and non-conforming rows are dropped — but the expectation is that the team has produced a table whose shape matches the declared spec. Unexpected drift surfaces at normalize as validation failures.

### 3. It has exactly one identifier column

The source's spec marks one slot with `identifier: true`. That column is the **source-natural-key**: what knot uses to disambiguate rows within this source, and what the ER strategy binds to canonical entity ids in the resolve stage.

Required. A source without a stable per-row identifier is not a valid source.

### 4. It optionally has a watermark column

A column marked `watermark: true` (typically a timestamp, but any monotonic field works). Knot uses it to ask the lake for "rows since X" rather than full-table reads on each compile. Without it, normalize processes everything every run.

Optional. Recommended for any source with non-trivial row counts or refresh rate.

## What is explicitly NOT in this contract

Knot does not need to know, and does not own:

- **How the team gets rows into the location.** Spark batch, Kafka Connect, scheduled scrapers, manual COPY, a colleague pasting CSV — all out of scope. The team picks.
- **Authentication to wherever the data originally came from.** IMDB API keys, source database creds, S3 IAM — none of knot's business.
- **Refresh schedule or freshness SLA.** If the team wants imdb_movies to refresh hourly, that's their batch job's business. Knot's normalize task runs against whatever's in the lake when the workflow runs.
- **Replace-vs-append semantics at the lake level.** A source can be full-replace each refresh or append-only — knot processes what's there.
- **Storage format.** Parquet, Avro, Iceberg, postgres rows — the lake impl problem, not the source contract.
- **Connector code.** No `SourceReader` interface exists. Sources are passive descriptors of what's in the lake, not active fetchers.

## Source-to-ontology mapping

The source declares its own shape (`tt_id`, `runtime_min`); the ontology declares its shape (`Movie.title`, `Movie.runtime_minutes`). The mapping between them lives **on the source node**, not on the ontology class.

Rationale: source-side keeps the source self-contained. A new source onboarding adds one node (the source with its mapping); it does not require editing the ontology class. The ontology stays a clean domain model; the source carries the rosetta to that model.

A source's rows can map to **one** ontology class or to **multiple**. Two shapes:

### Homogeneous shorthand — `mapping:` (singular)

Every row in the source's lake table is the same kind of thing. One ontology class, one field map. This is the common case.

### Heterogeneous — `discriminator:` + `mappings:` (plural)

A single source produces rows for multiple classes. The source declares a **discriminator column**: a column on the lake table whose value (per row) selects which mapping applies. Each enum value either points at an ontology class with its own field map, or is explicitly dropped.

**Strict over enum values.** Every value the discriminator can take must appear in `mappings` — either pointing at a class or with `drop: true`. An unmapped value fails the run loudly at normalize. The team has to explicitly acknowledge each value; silent drops are not allowed.

**Wildcard opt-out.** If the team does want lax behavior, they write `"*": {drop: true}` as the catch-all. This is the *only* way to drop unknown values without enumerating them, and it's explicit on the source node.

**Why this and not subclass-tree expansion.** An earlier sketch had the compiler walk the ontology subclass graph and synthesize per-subclass routing. Rejected: subclass-ness is an ontology-graph fact (used for derived views and queries), not a source-routing mechanism. Source routing is whatever the source's own data says — declared on the source node, explicit, auditable. A row's class comes from the discriminator the team configured, not from a tree walk over the ontology.

## Example 1: `imdb_movies` — homogeneous shorthand

A toy-scheduler/DuckDB source where every row is a movie.

### Source node payload

```yaml
id: https://knot.demo/imdb_movies
name: imdb_movies
initial_trust: 0.85
location: lake://imdb_movies
classes:
  ImdbMovie:
    attributes:
      tt_id:        {range: string,   identifier: true}
      title:        {range: string}
      director:     {range: string}
      release_year: {range: integer}
      runtime_min:  {range: integer}
      ingested_at:  {range: datetime, watermark: true}
mapping:
  ontology_class: Movie
  fields:
    tt_id:        source_natural_key
    title:        title
    director:     director
    release_year: release_year
    runtime_min:  runtime_minutes      # source-name → ontology-name
```

### What's in the lake at `lake://imdb_movies`

Resolved by the toy DuckDB lake impl to `/lake/imdb_movies.parquet`:

```
tt_id      | title       | director       | release_year | runtime_min | ingested_at
-----------+-------------+----------------+--------------+-------------+---------------------
tt6751668  | Parasite    | Bong Joon-ho   | 2019         | 132         | 2026-04-29 13:02:55
tt0114369  | Se7en       | David Fincher  | 1995         | 127         | 2026-04-29 13:02:55
tt0068646  | The Godfather | Francis F. C.| 1972         | 175         | 2026-04-29 13:02:55
...
```

Source columns. Source-shape. Source-natural-key (`tt_id`). Watermark (`ingested_at`). The team produced this however; knot reads it from here onward.

### What knot does with it (normalize stage)

When the compiled workflow's `normalize:imdb_movies` task runs, the bound impl:

1. Reads from `lake://imdb_movies` via `QueryReader` (impl resolves the URI).
2. Applies the source's source→ontology mapping (`runtime_min → runtime_minutes`, etc.).
3. Runs structural validation (declared on the ontology + source specs) — drops non-conforming rows, records failures in the run record.
4. Writes the ontology-shaped result to the per-source-facts layer of the lake (typically partitioned by source — out of scope for this doc; see materialization layer specs).

Subsequent stages (resolve, merge, validate, publish) operate on the per-source-facts layer and beyond. None of them re-read the source layer.

## Example 2: `imdb_credits` — heterogeneous, discriminator-routed

A single source whose rows describe several distinct ontology classes. The `role` column is the discriminator.

### Source node payload

```yaml
id: https://knot.demo/imdb_credits
name: imdb_credits
initial_trust: 0.85
location: lake://imdb_credits
classes:
  ImdbCredit:
    attributes:
      credit_id:    {range: string,   identifier: true}
      role:         {range: string}                       # discriminator column
      person_name:  {range: string}
      tt_id:        {range: string}                       # FK back to a movie
      character:    {range: string}
      ingested_at:  {range: datetime, watermark: true}
discriminator: role
mappings:
  actor:
    ontology_class: Person
    fields:
      credit_id:   source_natural_key
      person_name: name
  director:
    ontology_class: Director
    fields:
      credit_id:   source_natural_key
      person_name: name
  casting_director:
    drop: true                                            # explicit; not silently ignored
  # No "*" wildcard — any unknown role fails the run loudly.
```

### What's in the lake at `lake://imdb_credits`

```
credit_id  | role             | person_name      | tt_id     | character        | ingested_at
-----------+------------------+------------------+-----------+------------------+--------------------
nm0001129  | actor            | Frances McDormand| tt0116282 | Marge Gunderson  | 2026-04-29 13:02:55
nm0000399  | director         | Joel Coen        | tt0116282 |                  | 2026-04-29 13:02:55
nm0500001  | casting_director | (some name)      | tt0116282 |                  | 2026-04-29 13:02:55
nm0123456  | stunt_double     | (some name)      | tt0116282 |                  | 2026-04-29 13:02:55
...
```

### What knot does with it (normalize stage)

The compiled workflow's `normalize:imdb_credits` task runs once, but emits per-class outputs:

1. Reads rows from `lake://imdb_credits` via `QueryReader`.
2. For each row: looks up `row.role` in `mappings`.
   - `actor` → apply Person field map; route to per-source-facts under `Person`.
   - `director` → apply Director field map; route to per-source-facts under `Director`.
   - `casting_director` → drop (explicit).
   - `stunt_double` → **fail the run.** Not in the mapping, no wildcard, surfaces as a normalize-stage error with the offending value.
3. Structural validation runs per emitted class against the corresponding ontology constraints.
4. Per-source-facts is written partitioned by class as well as source, so the rest of the per-class workflows pull only the rows they need.

A `Person` workflow run reads `imdb_credits`'s `Person` partition alongside other `Person` sources; a `Director` workflow run reads the `Director` partition. The source itself appears in both classes' source-sets at compile time.

## Implications

- **Source onboarding is mechanical.** Author the source's Pydantic spec + mapping, point at the lake location, register. Done. No knot-side connector code.
- **Source data lifecycle is the team's.** They can backfill, replay, re-export, or migrate the source layer without touching knot.
- **Ingestion patterns are pluggable by design — outside knot.** Pull-mode connectors, push-mode webhooks, CDC streams, scheduled ETL — pick any of them; they all end at "rows in the lake at the declared location," which is where knot picks up.
- **The agent-readable surface stays clean.** An LLM consuming the ontology + sources can answer "what sources contribute to Movie?" and "what columns does each carry?" purely from knot's spec. It doesn't need to know how data got there.
- **The audit story remains intact.** Each fact in `per_source_facts` traces back to a source-natural-key in a specific source at a specific revision. The pre-normalize stage (the team's ingestion) doesn't break the audit because it's not part of knot's workflow — knot's audit starts at normalize, where the lake row was read at a known offset.

## Architectural rules that follow from this

1. **Knot core code does not pull from external systems.** Not via `SourceReader`, not via inline HTTP calls, not via SDK helpers. Source data is already-in-lake when a workflow compiles.
2. **There is no `SourceReader` interface.** Earlier boundary specs listed it; this document supersedes that. Sources are passive lake declarations, not active runtime objects.
3. **Compiled workflows begin at normalize.** No `ingest` task in `WorkflowSpec`. The first task per source is `normalize:<source_name>`.
4. **The `location` URI scheme is the lake impl's concern.** The source node carries an opaque URI; the `QueryReader` resolves it. Adding a new lake backend means a new `QueryReader` impl that recognizes new URI schemes; no source-node-payload change.

## What this changes vs. earlier drafts

Earlier drafts of the boundary spec ([`legacy/architecture-boundary-spec-vs-data-plane.md`](legacy/architecture-boundary-spec-vs-data-plane.md)) listed `SourceReader` among the eight interfaces, with a `read(source_node_id)` method. Earlier sketches of `WorkflowSpec` had explicit `ingest:<source>` tasks.

This document supersedes both. `SourceReader` is removed; the compiled workflow starts at `normalize`. The team's ingestion is invisible to knot by design.

The list of named protocols has since evolved (per `staging/seam-contract-pattern.md` and `staging/query-executor.md`): `QueryReader` / `Materializer` / `Introspector` / `ViewManager` (lake execution); `ERProtocol` (per-class ER); `DqRunner` (custom DQ); free-form materialization impls; `Translator` (consumer query expansion). Plus orthogonal `Orchestrator`. The exact set is loose — the architectural primitive is the protocol + DataContext + pure-data context pattern (per `staging/di-input-contract.md`), not a fixed enumeration of named interfaces.
