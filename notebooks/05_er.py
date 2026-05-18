import marimo

__generated_with = "0.23.5"
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # knot — 05: embeddings + entity resolution

    Two sources have published overlapping movie records (imdb in
    02_ingest, tmdb in 04_ingest_tmdb). Each row has its own
    `source_identifier`; none has a `canonical_id` yet. The
    resolver view is empty.

    This notebook runs two independent worker stages — same
    decoupling pattern as ingest:

    | stage | scans for | does |
    |---|---|---|
    | **embedding** | `title_embedding IS NULL` | encodes title → UPDATE column |
    | **ER** | `canonical_id IS NULL` | k-NN against other sources → assign canonical |

    knot owns the *schema* (`VECTOR(384)` slot + HNSW index, built
    by `Spec.ddl()` in 01 and extended by 03) and the *SQL* for
    canonical assignment (`binding.assign_canonical_sql()`).
    Everything else — encoder choice, threshold, blocking
    strategy, reranker — belongs to the host worker.
    """)
    return


@app.cell
def _():
    import json
    import uuid

    import pandas as pd
    import psycopg
    from sentence_transformers import SentenceTransformer
    from sqlalchemy import create_engine

    return SentenceTransformer, create_engine, json, pd, psycopg, uuid


@app.cell
def _():
    # Full spec composed in (we need both source bindings).
    import movies_spec.full  # noqa: F401
    from movies_spec import imdb_movie_b, movie
    from movies_spec.full import tmdb_movie_b

    return imdb_movie_b, movie, tmdb_movie_b


@app.cell
def _(create_engine, psycopg):
    pg = psycopg.connect(
        host="localhost",
        port=5433,
        user="knot",
        password="knot",
        dbname="knot",
        autocommit=True,
    )
    engine = create_engine("postgresql+psycopg://knot:knot@localhost:5433/knot")
    SCHEMA = "knot_demo"
    SCHEMA
    return SCHEMA, engine, pg


@app.cell
def _(SCHEMA, movie, engine, pd, pg):
    # Resolved view is empty going in: every row's canonical_id is
    # NULL, the view filters those out.
    resolved_before = pd.read_sql_query(movie.resolved.sql(schema=SCHEMA), engine)
    print(f"resolved rows BEFORE ER: {len(resolved_before)}")
    resolved_before
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Stage 1 — embedding worker

    Load the encoder, find unembedded rows, encode their titles in
    one batch, push back via UPDATE. A real worker would loop
    forever; here we do it once.

    knot's contribution: the `title_embedding vector(384)` column
    + the HNSW index with `vector_cosine_ops`. The model
    (`all-MiniLM-L6-v2`, 384 dims) is the worker's choice and
    belongs in worker config, not knot.
    """)
    return


@app.cell
def _(SentenceTransformer):
    model = SentenceTransformer("all-MiniLM-L6-v2")
    print(f"loaded encoder · dim={model.get_sentence_embedding_dimension()}")
    return (model,)


@app.cell
def _(SCHEMA, model, engine, pd, pg):
    unembedded = pd.read_sql_query(
        f"""
        SELECT source_name, source_identifier, valid_from, title
        FROM {SCHEMA}.movie_bindings
        WHERE title_embedding IS NULL
        """,
        engine,
    )
    embeddings = model.encode(unembedded["title"].tolist(), normalize_embeddings=True)
    print(f"encoded {len(unembedded)} titles → {embeddings.shape}")

    with pg.cursor() as cur:
        for (sn, si, vf, _), vec in zip(
            unembedded.itertuples(index=False), embeddings, strict=False
        ):
            cur.execute(
                f"""
                UPDATE {SCHEMA}.movie_bindings
                SET title_embedding = %(vec)s::vector(384)
                WHERE source_name = %(sn)s
                  AND source_identifier = %(si)s
                  AND valid_from = %(vf)s
                """,
                {"vec": str(vec.tolist()), "sn": sn, "si": si, "vf": vf},
            )
    return


@app.cell
def _(SCHEMA, engine, pd, pg):
    # Confirm: every row now has an embedding.
    pd.read_sql_query(
        f"""
        SELECT source_name, COUNT(*) AS rows, COUNT(title_embedding) AS embedded
        FROM {SCHEMA}.movie_bindings
        GROUP BY source_name
        ORDER BY source_name
        """,
        engine,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Stage 2 — ER worker (k-NN blocking + threshold)

    Two-phase: mint canonical_ids for imdb (the anchor source),
    then for each tmdb row find the nearest imdb row by cosine
    distance and either reuse the matched canonical_id or mint a
    new one if no neighbor is close enough.

    The k-NN query uses `<=>` (cosine distance via
    `vector_cosine_ops`) — the HNSW index does the heavy lifting,
    O(log n) per lookup. The pgvector idiom for "k=1 nearest
    neighbor" is `ORDER BY embedding <=> query LIMIT 1`.

    This notebook keeps the policy deliberately simple: single
    candidate, hard threshold, no reranker. The full cascade
    (sparse blocker → dense blocker → cross-encoder reranker →
    LLM on the ambiguous middle) layers on the same primitives.
    knot's contract ends at `binding.assign_canonical_sql()`.
    """)
    return


@app.cell
def _(SCHEMA, imdb_movie_b, json, engine, pd, pg, uuid):
    # Phase 1: mint canonical_id for every imdb row. imdb is the
    # "anchor" — in production you'd pick the most trusted source
    # or use a deterministic key.
    imdb_rows = pd.read_sql_query(
        f"SELECT source_identifier, title "
        f"FROM {SCHEMA}.movie_bindings "
        f"WHERE source_name = 'imdb' AND canonical_id IS NULL",
        engine,
    )
    print(f"minting {len(imdb_rows)} canonical_ids for imdb")

    assign_imdb = imdb_movie_b.assign_canonical_sql(schema=SCHEMA)
    with pg.cursor() as cur:
        for si in imdb_rows["source_identifier"]:
            cur.execute(
                assign_imdb,
                {
                    "canonical_id": f"m_{uuid.uuid4().hex[:10]}",
                    "source_identifier": si,
                    "er_metadata": json.dumps({"method": "mint", "source": "imdb"}),
                },
            )
    return


@app.cell
def _(SCHEMA, engine, pd, pg):
    # Phase 2 (read): for each tmdb row, find the nearest imdb row.
    # CROSS JOIN LATERAL drives the per-row k-NN.
    candidates = pd.read_sql_query(
        f"""
        SELECT
            t.source_identifier  AS tmdb_id,
            t.title              AS tmdb_title,
            i.source_identifier  AS imdb_id,
            i.title              AS imdb_title,
            i.canonical_id       AS imdb_canonical,
            (t.title_embedding <=> i.title_embedding) AS distance
        FROM {SCHEMA}.movie_bindings t
        CROSS JOIN LATERAL (
            SELECT source_identifier, title, canonical_id, title_embedding
            FROM {SCHEMA}.movie_bindings
            WHERE source_name = 'imdb' AND canonical_id IS NOT NULL
            ORDER BY title_embedding <=> t.title_embedding
            LIMIT 1
        ) i
        WHERE t.source_name = 'tmdb' AND t.canonical_id IS NULL
        ORDER BY distance
        """,
        engine,
    )
    candidates.head(15)
    return (candidates,)


@app.cell
def _(SCHEMA, candidates, json, pg, tmdb_movie_b, uuid):
    # Phase 2 (write): threshold + assign. cos distance ≤ 0.20 →
    # similarity ≥ 0.80. Reasonable cut for all-MiniLM-L6-v2 on movie
    # titles; production tunes this against labeled data.
    THRESHOLD = 0.20
    matched = int((candidates["distance"] <= THRESHOLD).sum())
    print(f"matched {matched}/{len(candidates)} tmdb rows at distance ≤ {THRESHOLD}")

    assign_tmdb = tmdb_movie_b.assign_canonical_sql(schema=SCHEMA)
    with pg.cursor() as cur:
        for row in candidates.itertuples(index=False):
            if row.distance <= THRESHOLD:
                canonical_id = row.imdb_canonical
                method = "matched"
                matched_imdb_id = row.imdb_id
            else:
                canonical_id = f"m_{uuid.uuid4().hex[:10]}"
                method = "mint"
                matched_imdb_id = None
            cur.execute(
                assign_tmdb,
                {
                    "canonical_id": canonical_id,
                    "source_identifier": row.tmdb_id,
                    "er_metadata": json.dumps(
                        {
                            "method": method,
                            "distance": float(row.distance),
                            "matched_imdb_id": matched_imdb_id,
                        }
                    ),
                },
            )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Confirm — resolver view picks the winner per slot

    The resolver argmaxes per `(canonical_id, slot)` across both
    sources' weights. imdb has weight 0.85, tmdb 0.70 — so for
    matched rows where both sources agree-but-disagree, imdb wins.
    The `_all_sources` view exposes both claims per slot.
    """)
    return


@app.cell
def _(SCHEMA, movie, engine, pd, pg):
    pd.read_sql_query(
        movie.resolved.order_by(movie.col.year, "desc").limit(15).sql(schema=SCHEMA),
        engine,
    )
    return


@app.cell
def _(SCHEMA, engine, pd, pg):
    # Per-canonical breakdown — how many sources contributed to each.
    pd.read_sql_query(
        f"""
        SELECT canonical_id,
               jsonb_object_agg(source_name, source_identifier) AS sources,
               COUNT(*) AS source_count
        FROM {SCHEMA}.movie_bindings
        WHERE canonical_id IS NOT NULL AND valid_to IS NULL
        GROUP BY canonical_id
        ORDER BY source_count DESC, canonical_id
        LIMIT 15
        """,
        engine,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Where knot's responsibility ends

    | knot | worker |
    |---|---|
    | `VECTOR(384)` column + HNSW index in `Spec.ddl()` | picked the encoder, dim, metric |
    | `binding.write_sql()` for ingest | wrote the embedding-fill UPDATE loop |
    | `binding.assign_canonical_sql()` for ER stamping | picked the threshold, blocking strategy, mint scheme |
    | resolver views over canonical bindings | wrote the candidate-query SQL using `<=>` |
    | `er_metadata jsonb` column on bindings | populated `{"method": ..., "distance": ...}` for audit |

    The candidate-generation SQL is currently hand-written
    (`<=>` against `movie_bindings`). A
    `movie.col.title_embedding.distance(vec)` operator on the read
    substrate would let the same query be expressed via the Query
    AST. That's the natural next library addition — same shape as
    FK walks and aggregates, one more Expr node + one more
    `compile_sql.register`. Until then, raw SQL works.
    """)
    return


if __name__ == "__main__":
    app.run()
