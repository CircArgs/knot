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
    from _demo import SCHEMA, connect
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer, connect, json, pd, uuid


@app.cell
def _():
    # Full spec composed in (we need both source bindings).
    # tmdb_movie_b lives in the movies.py domain file once .full
    # has been imported (it's defined there, full just composes).
    import media_spec.full  # noqa: F401
    from media_spec import imdb_movie_b, movie, spec
    from media_spec.movies import tmdb_movie_b

    return imdb_movie_b, movie, spec, tmdb_movie_b


@app.cell
def _(connect):
    pg, engine = connect()
    return engine, pg


@app.cell
def _(engine, movie, pd):
    # Resolved view is empty going in: every row's canonical_id is
    # NULL, the view filters those out.
    resolved_before = pd.read_sql_query(movie.resolved.sql(), engine)
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
def _(engine, model, movie, pd, pg, spec):
    # Read via knot: cls.from_source per source + .where(col.is_null())
    # gives "rows where this column is NULL." The UPDATE for vector
    # backfill is the one host-owned raw SQL — knot has no in-place
    # slot-update API yet (gap tracked separately).
    _all = []
    for _src in spec.sources.values():
        if _src.name == "_user_corrections":
            continue
        _df = pd.read_sql_query(
            movie.from_source(_src)
                 .where(movie.col.title_embedding.is_null())
                 .sql(),
            engine,
        )
        if not _df.empty:
            _all.append(_df[["source_name", "source_identifier", "title"]])
    unembedded = pd.concat(_all, ignore_index=True) if _all else pd.DataFrame()
    embeddings = model.encode(unembedded["title"].tolist(), normalize_embeddings=True)
    print(f"encoded {len(unembedded)} titles → {embeddings.shape}")

    _table = movie.bindings_table_name
    with pg.cursor() as _cur:
        for (_sn, _si, _), _vec in zip(
            unembedded.itertuples(index=False), embeddings, strict=False
        ):
            _cur.execute(
                f"""
                UPDATE {_table}
                SET title_embedding = %(vec)s::vector(384)
                WHERE source_name = %(sn)s
                  AND source_identifier = %(si)s
                """,
                {"vec": str(_vec.tolist()), "sn": _sn, "si": _si},
            )
    return


@app.cell
def _(engine, movie, pd):
    # Confirm: every row now has an embedding.
    pd.read_sql_query(
        f"""
        SELECT source_name, COUNT(*) AS rows, COUNT(title_embedding) AS embedded
        FROM {movie.bindings_table_name}
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
def _(engine, imdb_movie_b, json, movie, pd, pg, uuid):
    # Phase 1: mint canonical_id for every imdb row. imdb is the
    # "anchor" — in production you'd pick the most trusted source
    # or use a deterministic key.
    imdb_rows = pd.read_sql_query(
        f"SELECT source_identifier, title "
        f"FROM {movie.bindings_table_name} "
        f"WHERE source_name = 'imdb' AND canonical_id IS NULL",
        engine,
    )
    print(f"minting {len(imdb_rows)} canonical_ids for imdb")

    assign_imdb = imdb_movie_b.assign_canonical_sql()
    with pg.cursor() as _cur:
        for _si in imdb_rows["source_identifier"]:
            _cur.execute(
                assign_imdb,
                {
                    "canonical_id": f"m_{uuid.uuid4().hex[:10]}",
                    "source_identifier": _si,
                    "er_metadata": json.dumps({"method": "mint", "source": "imdb"}),
                },
            )
    return


@app.cell
def _(engine, movie, pd):
    # Phase 2 (read): for each tmdb row, find the nearest imdb row.
    # CROSS JOIN LATERAL drives the per-row k-NN.
    bindings = movie.bindings_table_name
    candidates = pd.read_sql_query(
        f"""
        SELECT
            t.source_identifier  AS tmdb_id,
            t.title              AS tmdb_title,
            i.source_identifier  AS imdb_id,
            i.title              AS imdb_title,
            i.canonical_id       AS imdb_canonical,
            (t.title_embedding <=> i.title_embedding) AS distance
        FROM {bindings} t
        CROSS JOIN LATERAL (
            SELECT source_identifier, title, canonical_id, title_embedding
            FROM {bindings}
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
def _(candidates, json, pg, tmdb_movie_b, uuid):
    # Phase 2 (write): threshold + assign. cos distance ≤ 0.20 →
    # similarity ≥ 0.80. Reasonable cut for all-MiniLM-L6-v2 on movie
    # titles; production tunes this against labeled data.
    THRESHOLD = 0.20
    matched = int((candidates["distance"] <= THRESHOLD).sum())
    print(f"matched {matched}/{len(candidates)} tmdb rows at distance ≤ {THRESHOLD}")

    _assign_tmdb = tmdb_movie_b.assign_canonical_sql()
    with pg.cursor() as _cur:
        for _row in candidates.itertuples(index=False):
            if _row.distance <= THRESHOLD:
                _canonical_id = _row.imdb_canonical
                _method = "matched"
                _matched_imdb_id = _row.imdb_id
            else:
                _canonical_id = f"m_{uuid.uuid4().hex[:10]}"
                _method = "mint"
                _matched_imdb_id = None
            _cur.execute(
                _assign_tmdb,
                {
                    "canonical_id": _canonical_id,
                    "source_identifier": _row.tmdb_id,
                    "er_metadata": json.dumps(
                        {
                            "method": _method,
                            "distance": float(_row.distance),
                            "matched_imdb_id": _matched_imdb_id,
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
def _(engine, movie, pd):
    pd.read_sql_query(
        movie.resolved.order_by(movie.col.year, "desc").limit(15).sql(),
        engine,
    )
    return


@app.cell
def _(engine, movie, pd):
    # Per-canonical breakdown — how many sources contributed to each.
    pd.read_sql_query(
        f"""
        SELECT canonical_id,
               jsonb_object_agg(source_name, source_identifier) AS sources,
               COUNT(*) AS source_count
        FROM {movie.bindings_table_name}
        WHERE canonical_id IS NOT NULL
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
