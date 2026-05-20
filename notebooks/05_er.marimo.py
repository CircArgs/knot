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
    from _demo import connect
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
    # Read + write fully through knot:
    #   cls.from_source(s).where(col.is_null())    → unembedded rows
    #   binding.update_slot_sql("title_embedding") → batched UPDATE
    # The embedding worker batches per source and pipes one jsonb
    # payload per source through the slot-update API.
    import json as _json

    _total = 0
    for _src in spec.sources.values():
        if _src.name == "_user_corrections":
            continue
        _binding = movie.binding_for(_src)
        if _binding is None:
            continue
        _df = pd.read_sql_query(
            movie.from_source(_src)
                 .where(movie.col.title_embedding.is_null())
                 .sql(),
            engine,
        )
        if _df.empty:
            continue
        _vecs = model.encode(_df["title"].tolist(), normalize_embeddings=True)
        _payload = [
            {"source_identifier": _sid, "title_embedding": _v.tolist()}
            for _sid, _v in zip(_df["source_identifier"], _vecs, strict=False)
        ]
        with pg.cursor() as _cur:
            _cur.execute(
                _binding.update_slot_sql("title_embedding"),
                {"rows": _json.dumps(_payload)},
            )
        _total += len(_df)
        print(f"  {_src.name}: embedded {len(_df)} titles")
    print(f"embedded {_total} titles total")
    return


@app.cell
def _(engine, movie, pd, spec):
    # Confirm: every row now has an embedding. Per-source via the knot
    # read API — one from_source query per source, assembled in pandas.
    from knot.ast.expr import count as _count

    _rows = []
    for _src in spec.sources.values():
        _q = movie.from_source(_src).select(
            _count(),
            _count(movie.col.title_embedding),
        )
        _df = pd.read_sql_query(_q.sql(), engine)
        _rows.append({
            "source_name": _src.name,
            "rows": int(_df.iloc[0, 0]),
            "embedded": int(_df.iloc[0, 1]),
        })
    pd.DataFrame(_rows).sort_values("source_name").reset_index(drop=True)
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
    # or use a deterministic key. Read via from_source + unresolved
    # filter; write via the BATCHED assign_canonicals_sql so the whole
    # batch lands in one round-trip.
    _imdb_src = imdb_movie_b.source
    imdb_rows = pd.read_sql_query(
        movie.from_source(_imdb_src)
             .where(movie.col.canonical_id.is_null())
             .select(movie.bindings_col.source_identifier, movie.col.title)
             .sql(),
        engine,
    )
    print(f"minting {len(imdb_rows)} canonical_ids for imdb")

    _assignments = [
        {
            "canonical_id": f"m_{uuid.uuid4().hex[:10]}",
            "source_identifier": _si,
            "er_metadata": {"method": "mint", "source": "imdb"},
        }
        for _si in imdb_rows["source_identifier"]
    ]
    with pg.cursor() as _cur:
        _cur.execute(
            imdb_movie_b.assign_canonicals_sql(),
            {"assignments": json.dumps(_assignments)},
        )
    return


@app.cell
def _(engine, json, movie, pd, spec, tmdb_movie_b):
    # Phase 2 (read): for each tmdb row, find the nearest imdb row.
    # Two-query pattern via knot — one query for the tmdb rows needing
    # ER, then a per-row k-NN against imdb using
    # title_embedding.distance_to(target_vec). Per-row roundtrips, but
    # every query is composed through the knot Query AST (no raw SQL).
    # Production-scale ER would batch via a host-built lateral-join
    # view; the demo prioritizes substrate fidelity.
    _imdb_src = spec.sources["imdb"]
    _tmdb_src = tmdb_movie_b.source

    _emb = movie.col.title_embedding
    _sid = movie.bindings_col.source_identifier
    _cid = movie.col.canonical_id

    tmdb_df = pd.read_sql_query(
        movie.from_source(_tmdb_src)
             .where(_cid.is_null())
             .select(_sid, movie.col.title, _emb)
             .sql(),
        engine,
    )

    _hits = []
    for _, _row in tmdb_df.iterrows():
        _vec = _row["title_embedding"]
        if isinstance(_vec, str):
            _vec = json.loads(_vec)
        _knn_q = (
            movie.from_source(_imdb_src)
                 .where(_cid.is_not_null())
                 .order_by(_emb.distance_to(_vec))
                 .limit(1)
                 .select(_sid, movie.col.title, _cid, _emb.distance_to(_vec))
        )
        _hit = pd.read_sql_query(_knn_q.sql(), engine)
        if not _hit.empty:
            _hits.append({
                "tmdb_id": _row["source_identifier"],
                "tmdb_title": _row["title"],
                "imdb_id": _hit.iloc[0, 0],
                "imdb_title": _hit.iloc[0, 1],
                "imdb_canonical": _hit.iloc[0, 2],
                "distance": float(_hit.iloc[0, 3]),
            })
    candidates = pd.DataFrame(_hits).sort_values("distance").reset_index(drop=True)
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
    # Per-canonical breakdown via the all_sources view — its per-source
    # jsonb columns already encode provenance; pandas counts how many
    # sources contributed per canonical row.
    _as = pd.read_sql_query(movie.all_sources.sql(), engine)
    if "title" in _as.columns:
        _as["source_count"] = _as["title"].apply(
            lambda v: len(v) if isinstance(v, dict) else 0
        )
        _as.sort_values("source_count", ascending=False).head(15)[
            ["canonical_id", "source_count", "title"]
        ]
    else:
        _as.head(15)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Where knot's responsibility ends

    | knot | worker |
    |---|---|
    | `VECTOR(384)` column + HNSW index in `Spec.ddl()` | picked the encoder, dim, metric |
    | `binding.update_slot_sql()` for embedding backfill | wrote the encoder loop |
    | `binding.assign_canonicals_sql()` for batched ER stamping | picked the threshold, blocking strategy, mint scheme |
    | `slot.distance_to(vec)` k-NN expression (HNSW-aware) | per-row candidate iteration / scoring |
    | `er_metadata jsonb` column on bindings | populated `{"method": ..., "distance": ...}` for audit |

    Every cell above composes through knot expressions —
    `from_source(s).where(...).order_by(slot.distance_to(v)).limit(k)`
    is the substrate's k-NN form, no raw SQL required.
    """)
    return


if __name__ == "__main__":
    app.run()
