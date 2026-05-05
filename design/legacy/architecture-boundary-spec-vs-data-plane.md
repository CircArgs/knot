---
title: Architecture boundary — knot core (spec/metadata) vs. data plane (delegated)
status: note
created_at: 2026-04-29T14:30:00+00:00
project: knot
tags: [architecture, abstraction, dependency-injection, load-bearing]
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# The boundary

There is exactly one architectural seam in knot. Everything else is
implementation detail. **Knot core code touches only spec/metadata
state. Every read or write into the data plane goes through an
injected interface.**

This document is load-bearing. If a PR violates the boundary, it
gets rejected. No exceptions for "it works" or "the prototype is
all postgres anyway." The boundary exists so that swapping
postgres-as-lake for Iceberg/object-store/Spark is a binding change,
not a rewrite.

## What knot owns vs. what knot delegates

| Side | What lives there | Tables / state |
|---|---|---|
| **Core (knot owns)** | Ontology, revisions, sources registry, pipeline specs, corrections audit, identity, authz, history, pipeline_run records | `ontology_nodes`, `ontology_node_revisions`, `sources_state`, `corrections`, `history`, `pipeline_runs` |
| **Data plane (knot delegates)** | Source data, validated rows, resolved entities, DQ results, materialized output | `per_source_facts`, `resolved_facts`, `dq_results`, materialization-target tables |

The split is *not* "what's read-only vs. read-write" or "what's
versioned vs. what's not". The split is **"what is knot's spec
about its own world" vs. "what is the data we manage on behalf of
the team."** Knot's spec it owns directly. The data it touches
only via injected collaborators.

## The interfaces

Eight interfaces. They are the only seams between knot core and
the data plane. Anything that does *work* in the data plane
implements one of these.

```
SourceReader            # pulls rows from a registered external source
LakeWriter              # writes to per_source_facts; idempotent by (source, key, prop)
LakeReader              # reads per_source_facts / resolved_facts; never re-implements joins
MergeEngine             # produces resolved_facts from per_source_facts + trust state
SchemaValidator         # runs SHACL / LinkML constraints on a row batch
DqRunner                # evaluates dq_check specs against the lake
MaterializationWriter   # writes resolved entities to a target (lake / neo4j / etc)
Translator              # compiles GraphQL / Cypher / SPARQL → target dialect
```

Plus one orthogonal seam:

```
Orchestrator            # dispatches a job spec; returns a run handle
```

`Orchestrator` is *not* one of the eight. It is *composed by* impls
that need to do work elsewhere. A `SparkSQLMerger` doesn't run
Spark — it depends on `Orchestrator` and asks it to dispatch a job.
A `LakeWriter` for Iceberg might also depend on `Orchestrator` to
batch writes through a Spark job. Two layers, two injections, two
swap points.

> **Rule of thumb:** any time an impl wants to *do work somewhere
> else*, it depends on `Orchestrator`. Any time an impl wants to
> *talk to data*, it depends on `LakeReader` / `LakeWriter`. Those
> two are the load-bearing seams.

## The swap test

The single test for whether a piece of code is on the right side of
the boundary:

> **Could I swap the prototype postgres-as-lake for Iceberg-on-S3
> without touching any file under `server/api/` or any repository
> that handles knot core tables?**

If yes, the abstraction holds for that code path. If no, the
abstraction is broken there and needs to come back behind the
boundary.

## Examples — applying the boundary

### Right ✓

```python
# server/api/corrections.py
async def submit_correction(body: CorrectionRequest, ...,
                            lake: LakeWriter = Depends(get_lake_writer)):
    correction = await insert_correction(conn, ...)        # core table — direct OK
    await lake.write_facts(                                # data plane — via interface
        source=USER_CORRECTIONS_SOURCE,
        rows=[Fact(...)],
    )
```

### Wrong ✗

```python
# server/api/corrections.py
async def submit_correction(body: CorrectionRequest, ..., pool=Depends(get_pool)):
    correction = await insert_correction(conn, ...)
    await conn.execute(                                    # ✗ direct SQL into lake table
        "INSERT INTO per_source_facts ...", ...
    )
```

The "wrong" version isn't wrong because dual-write is bad — it's
wrong because the API handler imports knowledge of the lake's
schema. Switch lake to Iceberg → this file changes.

## Where the prototype currently violates the boundary

(To be filled in by the audit pass — see "Audit follow-ups" below.)

The current dual-write inside `submit_correction` is the canonical
violation example. There are likely others in:
- `server/api/entities.py` — `_hydrate_entity` reaches into
  `per_source_facts` directly with SQL
- `server/api/queries.py` — query execution against the lake
- `server/repositories/facts.py` — by name, this is on the wrong side
  of the line; should be a `LakeWriter`/`LakeReader` impl
- `server/repositories/entities.py` — same; if it touches
  `per_source_facts` or `resolved_facts`, it's a data-plane impl

## How we hammer this in (so it stops happening)

Defense in depth. Documentation drifts. Code review misses things.
The grep does not.

### 1. This vault note

Cite it in PR reviews. Link from `CLAUDE.md` so every Claude session
loads it as context. Consider it the architectural constitution.

### 2. Module structure

```
server/
  core/                  # knot's own state, no data-plane writes
  api/                   # HTTP handlers — must depend on injected interfaces
  repositories/          # repos for core tables ONLY
  data_plane/            # the boundary
    __init__.py          # interfaces + DI registry
    postgres/            # default impls for the prototype
    iceberg/             # future impl
    spark/               # future impl
  models/                # pydantic spec models (Pipeline, ErStrategy, etc)
```

`server/data_plane/__init__.py` defines the eight interfaces and the
DI registry. Nothing else. Concrete impls live under sub-packages
named after the binding tech. Pages 1-3 of any new contributor's
ramp-up should be that file.

### 3. Lint guard (load-bearing)

A pre-commit hook that fails the commit if any file under:

- `server/api/`
- `server/core/`
- `server/repositories/{nodes,sources_state,corrections,history,pipeline_runs,revisions,merge_runs}.py`

…contains a SQL string referencing any of the data-plane tables:

- `per_source_facts`
- `resolved_facts`
- `dq_results`
- `materialized_*`
- `er_decisions`

Implementation: a single bash script in `scripts/lint-boundary.sh`
that greps for the four table names against the path allowlist.
Fails CI on hit. Engineers cannot accidentally drift past the line
because the line is mechanical.

### 4. Code review checklist

Add to the PR template:

> - [ ] If this PR adds a new SQL query, does it touch a data-plane
>       table? If so, which interface does it go behind?
> - [ ] If this PR adds a new injected dependency, is it one of the
>       eight interfaces (or `Orchestrator`)? If not, justify in PR
>       description.
> - [ ] Does this PR pass the swap test? (Could the lake be Iceberg
>       and this code still compile + work?)

## Migration path from current state

The current code violates the boundary in known places (corrections,
entities). Migration is in three phases:

**Phase 1 — define the seam.** Write `server/data_plane/__init__.py`
with the eight interfaces. Pure type definitions, no impls yet.

**Phase 2 — extract postgres impls.** Move
`server/repositories/facts.py` and any other data-plane SQL into
`server/data_plane/postgres/`. Keep behavior identical. Wire the
default DI registry to use them.

**Phase 3 — switch call sites.** Replace direct SQL in `api/` with
interface calls. The lint guard catches anything missed.

Each phase is independently shippable. Phase 1 alone — with the
lint guard turned on — already prevents new violations.

## Why this boundary exists

Three things knot's value proposition depends on:

1. **Lake migration is binding-only.** When the team graduates from
   postgres-as-lake to Iceberg/object-store, the change is a new
   `data_plane/iceberg/` package and a registry update. No api/, no
   repositories of core tables, no business logic.

2. **MergeEngine / ER are pluggable.** A `SparkSQLMerger` and an
   `InlinePostgresMerger` and a `DbtMerger` all satisfy the same
   interface. The team can pick the engine without rewriting knot.

3. **Knot is honest about what it owns.** The audit story works
   because knot tracks every change vector. If knot's API code is
   reaching into the lake directly, the audit story stops being
   knot's responsibility — the API handler bypassed the abstraction
   that would have audited the write. Boundary discipline = audit
   integrity.

## Audit follow-ups

This note is paired with an audit pass — three review agents survey
the current codebase for divergence and produce a remediation list.
See `architecture-boundary-audit-2026-04-29.md` (forthcoming) for the
findings and the per-file remediation queue.
