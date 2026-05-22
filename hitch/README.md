# hitch

End-to-end reference app on top of **knot** (the substrate) and
**knot_graphql** (the SDL+resolver adapter). Adds the host-shaped
pieces knot deliberately leaves out:

| layer | tool |
|---|---|
| ingest / ER / embedding orchestration | Temporal |
| API | FastAPI + Ariadne |
| embeddings | sentence-transformers (`all-MiniLM-L6-v2`, 384-d) |
| database | postgres 16 + pgvector |

`hitch` is **not** part of knot. It depends on knot; knot does not
depend on hitch. If knot's surface is missing something hitch needs,
that's a knot gap — surface it, don't paper over it here.

## Run it

```bash
# from /mnt/main/code/knot
cd hitch
docker compose up --build

# in another shell, once everything is healthy:
docker compose exec hitch-worker python -m scripts.deploy
docker compose exec hitch-worker python -m scripts.run_demo

# query the live API
curl -s http://localhost:8000/graphql -H 'content-type: application/json' \
    -d '{"query":"{ movieList(first: 10) { canonical_id title year director { name } } }"}' | jq
```

Open the Temporal UI at <http://localhost:8088> to inspect runs.

## Architecture

```
                      ┌─────────────────────┐
                      │ pgvector/pgvector    │
                      │      pg16            │
                      └──────────▲──────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
   ┌──────────┴──────┐   ┌──────┴──────┐   ┌──────┴──────┐
   │ hitch-api       │   │ hitch-worker│   │ temporal    │
   │ FastAPI+Ariadne │   │ workflows   │◀──│ auto-setup  │
   │ /graphql        │   │ + activities│   │ + UI :8088  │
   └─────────────────┘   └─────────────┘   └─────────────┘
       reads only          writes only
       (resolved view)     (binding upserts +
                            ER + embeddings)
```

All DB writes go through **knot SQL emitters**:

| activity | knot call |
|---|---|
| ingest                | `binding.write_sql()` |
| pre-write validation  | `binding.validate_rows_sql()` |
| embed backfill        | `binding.update_slot_sql("title_embedding")` |
| ER mint               | `binding.assign_canonicals_sql()` |
| post-write sweep      | `spec.emit_validation()` |
| GraphQL reads         | `cls.resolved.where(...).sql()` via `knot_graphql.Resolvers` |

## Spec

`hitch/spec.py` — Movie / Person / Credit across `imdb`, `tmdb`,
`rottentomatoes`. Movie has a `title_embedding: VECTOR(384, cosine)`.
Credit has an ENUM `role`. Movie's `director` is a `Person` FK that
gets translated source-id → canonical-id by ER's assign_canonical
fan-out.

The `_user_corrections` source is wired automatically by knot
(`Spec.__post_init__`); deploy seeds it at weight = 1e6 so it
dominates the resolver argmax.

## Replay safety

Workflow code never imports knot's SQL emitters — every DDL/DML
template is built inside an `@activity.defn`, per the
[`docs/temporal-adapter.md`](../docs/temporal-adapter.md) contract:

> Compiling a SQL string is deterministic, but the moment you do it
> inside a workflow you've leaked the spec module reference into the
> workflow history — a redeploy with a different spec replays into a
> poisoned state.

The ER policy is deterministic-mint (sha1 of identity fields), so
retries converge to the same canonical_ids.

## Limits / what's out of scope

- No auth, no multi-tenant.
- No live source fetch — `IngestWorkflow` reads from `hitch/seeds/*.json`.
  Wiring real source APIs is a one-activity swap (`fetch_seed_batch`).
- The ER activity uses sha1-deterministic minting, not embedding
  similarity. For a "real" ER policy, swap `decide_canonicals` for one
  that calls `from_source(other).order_by(distance_to(my_vec)).limit(k)`
  and applies a threshold; the rest of the wiring doesn't change.
- No mutation resolvers in GraphQL. The write path is Temporal-
  orchestrated; mutations would shim into `IngestWorkflow` /
  `ERWorkflow` triggers — left for a v2 if needed.
