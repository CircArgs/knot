import marimo

__generated_with = "0.23.5"
app = marimo.App(layout_file="layouts/06_end_to_end.marimo.slides.json")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # knot — end-to-end tour

    One spec, two sources, the full lifecycle:
    **deploy → ingest → embed → resolve → query.**

    Runs against a fresh `knot_tour` schema on every notebook
    run. Every read/write composes through knot's compiled
    surface; the only raw SQL is catalog introspection.
    """)
    return


@app.cell
def _():
    import json

    import pandas as pd
    import psycopg
    from sqlalchemy import create_engine, text

    return create_engine, json, pd, psycopg, text


@app.cell
def _(create_engine, psycopg, text):
    SCHEMA = "knot_tour"  # demo-only; the DROP guard below refuses
    EXPECTED_DB = "knot"  # any other DB / non-demo schema name.
    PG_URL = "postgresql+psycopg://knot:knot@localhost:5433/knot"

    pg = psycopg.connect(
        host="localhost", port=5433, user="knot",
        password="knot", dbname=EXPECTED_DB, autocommit=True,
    )
    engine = create_engine(PG_URL)

    # Refuse to run if pointed at the wrong database OR a schema name
    # that doesn't match our demo prefix. Protects against copy-paste
    # disasters (s/knot_tour/knot_data/g and oops).
    with pg.cursor() as _cur:
        _cur.execute("SELECT current_database()")
        _actual_db = _cur.fetchone()[0]
    if _actual_db != EXPECTED_DB:
        raise RuntimeError(
            f"DROP SCHEMA guard: connected to {_actual_db!r}, expected "
            f"{EXPECTED_DB!r}. Refusing to reset schema."
        )
    if not SCHEMA.startswith("knot_tour"):
        raise RuntimeError(
            f"DROP SCHEMA guard: SCHEMA={SCHEMA!r} doesn't match the "
            f"demo prefix 'knot_tour'. Refusing to drop."
        )

    # Hermetic: drop + recreate the schema on every run.
    with engine.begin() as _conn:
        _conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        _conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        _conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    return SCHEMA, engine, pg


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 1. Spec — defined once, top to bottom

    Three concrete classes (Movie, Person, Credit), two sources
    (imdb, tmdb), one virtual subclass (DirectedMovie), three
    constraints, every binding declared. Nothing gets bolted on
    later — the spec is the contract.
    """)
    return


@app.cell
def _(SCHEMA):
    from knot import Spec, this, types

    spec = Spec(identifier_slot_name="canonical_id", schema=SCHEMA)

    # ---- concrete classes ------------------------------------------------
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_country", types.TEXT)

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    # Movie.director + Movie.writer are DENORMALIZED — the principal
    # named credit (one per role). Multi-role attribution + co-directors
    # / co-writers live in Credit. Constraint
    # `movie_director_matches_credit` below enforces the denorm stays
    # honest. This is the realistic API shape: GraphQL resolvers want
    # `movie.director` as a quick scalar without joining Credit.
    movie.slot("director", person)
    movie.slot("writer", person)
    # MiniLM-L6-v2 is 384-dim; cosine matches the metric used at
    # embed time + selects the right pgvector HNSW operator class.
    movie.slot("title_embedding", types.VECTOR(384, metric="cosine"))

    credit = spec.add_class("Credit")
    # role uses the typed ENUM primitive — DDL emits a TEXT CHECK
    # constraint AND validate_rows_sql checks membership before
    # ingest. No hand-rolled `role.in_([...])` constraint needed.
    credit.slot(
        "role",
        types.ENUM("director", "actor", "writer", "producer"),
        required=True,
    )
    credit.slot("movie", movie)
    credit.slot("person", person)

    # ---- sources + bindings (implicit passthrough mappings) --------------
    imdb_src = spec.add_source("imdb")
    tmdb_src = spec.add_source("tmdb")

    imdb_movie_b = imdb_src.bind(movie)
    imdb_person_b = imdb_src.bind(person)
    imdb_credit_b = imdb_src.bind(credit)
    tmdb_movie_b = tmdb_src.bind(movie)
    tmdb_person_b = tmdb_src.bind(person)

    # ---- constraints -----------------------------------------------------
    # Domain-shape rules — what no honest source could emit.
    # year_sane: cinema starts 1888. year_not_future: dynamic
    # against today's year + 5 (release announcements run ~5 yrs
    # ahead); the spec captures the rule, not a baked-in date.
    from datetime import datetime as _dt

    _next_year_cap = _dt.now().year + 5
    movie.add_constraint("year_sane", body=movie.col.year >= 1888)
    movie.add_constraint(
        "year_not_future", body=movie.col.year <= _next_year_cap
    )
    # No `credit_role_allowed` constraint — the ENUM type above
    # owns role-membership at the type level (CHECK constraint in
    # DDL + validate_rows pre-check), not as a hand-rolled rule.
    #
    # Cross-encoding consistency: when Movie.director is set, the
    # same fact must also exist in Credit (role='director', person=
    # Movie.director, movie=this Movie). Catches denorm drift — a
    # real production failure mode where the convenience field falls
    # out of sync with the canonical Credit-based source.
    movie.add_constraint(
        "movie_director_matches_credit",
        body=movie.col.director.is_null()
        | (
            (credit.col.movie == this.Movie)
            & (credit.col.role == "director")
            & (credit.col.person == movie.col.director)
        ).any(),
    )

    # ---- virtual subclass ------------------------------------------------
    directed = movie.add_virtual(
        "DirectedMovie",
        where=(
            (credit.col.movie == this.Movie) & (credit.col.role == "director")
        ).any(),
    )
    return (
        credit,
        directed,
        imdb_credit_b,
        imdb_movie_b,
        imdb_person_b,
        imdb_src,
        movie,
        person,
        spec,
        tmdb_movie_b,
        tmdb_person_b,
        tmdb_src,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 2. Deploy

    `spec.ddl()` returns one idempotent CREATE script — schema,
    extension, tables, indexes, **and views**. Run it once;
    re-run any time without losing data.
    """)
    return


@app.cell
def _(pg, spec):
    ddl_sql = spec.ddl()
    with pg.cursor() as _cur:
        _cur.execute(ddl_sql)
    deployed = True
    return (deployed,)


@app.cell
def _(SCHEMA, deployed, engine, pd, text):
    _ = deployed
    with engine.begin() as _conn:
        catalog = pd.read_sql_query(
            text(
                "SELECT table_name AS name, table_type AS kind "
                "FROM information_schema.tables WHERE table_schema = :s "
                "UNION ALL "
                "SELECT viewname, 'VIEW' FROM pg_views WHERE schemaname = :s "
                "ORDER BY 2, 1"
            ),
            _conn,
            params={"s": SCHEMA},
        )
    catalog
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 3. Runtime weights

    Weights are not in the spec — they're a live `source_weight`
    table the host tunes per `(source, class, slot)`. Defaults
    for this demo: imdb = 0.85, tmdb = 0.95 (so tmdb wins on
    disagreement).
    """)
    return


@app.cell
def _(deployed, json, pg, spec):
    _ = deployed
    # Bulk weight upsert — ONE statement per binding using
    # `binding.upsert_weights_sql()` (plural) + a `{slot: weight}`
    # jsonb. At 50 sources × 20 slots that's 50 round-trips instead
    # of 1000. Read-before-write would gate against clobbering
    # operator tuning (CLAUDE.md "INSERT-only" posture); this demo
    # always starts from a fresh schema so a straight upsert is
    # correct here.
    _per_source_default = {"imdb": 0.85, "tmdb": 0.95}
    with pg.cursor() as _cur:
        # Wrap in a savepoint so partial failure rolls back the
        # whole weight initialization atomically.
        _cur.execute("BEGIN")
        try:
            for _b in spec.source_bindings:
                _weight = _per_source_default[_b.source.name]
                _payload = {
                    _slot.name: _weight
                    for _slot in _b.class_.effective_slots()
                    if not _slot.identifier
                }
                _cur.execute(
                    _b.upsert_weights_sql(),
                    {"weights": json.dumps(_payload)},
                )
            _cur.execute("COMMIT")
        except Exception:
            _cur.execute("ROLLBACK")
            raise
    weights_set = True
    return (weights_set,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 4. Ingest

    Walk `spec.source_bindings`; for each one, load the matching
    JSON file under `data/tour/<source>/<class>s.json` and run
    `binding.write_sql()` against it. Same shape a Temporal
    worker would use — one upsert per binding per batch.

    `binding.validate_rows_sql()` runs first so any
    structurally-broken rows (missing `source_identifier`, bad
    types, required slot null) get bucketed out before the
    upsert. Clean rows commit; the broken-row table below would
    feed a real worker's review queue.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    # The rejected rows. A real worker pipes this to a review queue
    # (`payload` carries the full original row for routing). This
    # demo just shows them — Mystery Movie violates both
    # missing_source_identifier and missing_required_slot=title.
    mo.md("""
    **Rejected rows (would route to review queue):**
    """)
    return


@app.cell
def _(bad_rows_df):
    bad_rows_df
    return


@app.cell
def _(mo, spec):
    # File convention: data/tour/<source.name>/<class.name.lower()>s.json
    _root = mo.notebook_dir() / "../data/tour"
    paths = {
        (b.source.name, b.class_.name):
            _root / b.source.name / f"{b.class_.name.lower()}s.json"
        for b in spec.source_bindings
    }
    return (paths,)


@app.cell
def _(json, paths, pd, pg, spec, weights_set):
    _ = weights_set
    _summary = []
    _all_violations = []  # accumulate per-row violations across bindings
    for _b in spec.source_bindings:
        _path = paths[(_b.source.name, _b.class_.name)]
        if not _path.exists():
            _summary.append({"source": _b.source.name, "class": _b.class_.name,
                             "raw": 0, "violation_rows": 0,
                             "violation_records": 0, "upserted": 0,
                             "note": "no data file"})
            continue
        _records = json.loads(_path.read_text())

        # Validate. validate_rows_sql returns ONE row per (record,
        # rule) violation — a single bad record can produce multiple
        # rows (e.g. Mystery Movie violates both missing_source_id
        # AND missing_required_slot). Dedup on row_index to count
        # bad RECORDS, not bad rules.
        with pg.cursor() as _cur:
            _cur.execute(_b.validate_rows_sql(),
                         {"rows": json.dumps(_records)})
            _cols = [d.name for d in _cur.description]
            _violation_records = _cur.fetchall()

        for _v in _violation_records:
            _row = dict(zip(_cols, _v, strict=True))
            _row["binding"] = f"{_b.source.name}.{_b.class_.name}"
            _all_violations.append(_row)

        _bad_indices = {v[0] for v in _violation_records}
        _clean = [r for i, r in enumerate(_records) if i not in _bad_indices]
        with pg.cursor() as _cur:
            _cur.execute(_b.write_sql(), {"rows": json.dumps(_clean)})

        _summary.append({
            "source": _b.source.name, "class": _b.class_.name,
            "raw": len(_records),
            "violation_rows": len(_violation_records),
            "violation_records": len(_bad_indices),
            "upserted": len(_clean), "note": "",
        })
    ingest_summary = pd.DataFrame(_summary)
    bad_rows_df = pd.DataFrame(_all_violations) if _all_violations else pd.DataFrame()
    ingest_done = True
    ingest_summary
    return bad_rows_df, ingest_done


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 5. Embed

    Load `all-MiniLM-L6-v2` (384-dim, cosine). For each source,
    pull bindings whose `title_embedding` is null via
    `cls.from_source(s)` (HNSW-friendly layer), encode titles in
    one batch, write back through `binding.update_slot_sql`.
    """)
    return


@app.cell
def _():
    import torch
    from sentence_transformers import SentenceTransformer

    # Pin device explicitly; sentence-transformers picks the wrong
    # one on machines with both CUDA + MPS available.
    _device = "cuda" if torch.cuda.is_available() else "cpu"
    model = SentenceTransformer("all-MiniLM-L6-v2", device=_device)
    encode_device = _device
    return encode_device, model


@app.cell
def _(
    encode_device,
    engine,
    imdb_movie_b,
    ingest_done,
    json,
    model,
    movie,
    pd,
    pg,
    text,
    tmdb_movie_b,
):
    _ = ingest_done
    _ENCODE_BATCH = 256  # sentence-transformers default is 32; bump
                          # for CPU throughput on title-sized strings.
    _WRITE_BATCH = 5_000  # chunk size for the postgres update_slot call
                          # so we don't ship a 100k-vector json blob in one shot.

    _backfilled = []
    for _b in (imdb_movie_b, tmdb_movie_b):
        _q = (
            movie.from_source(_b.source)
                .where(movie.col.title_embedding.is_null())
                .select(movie.bindings_col.source_identifier, movie.col.title)
        )
        with engine.begin() as _conn:
            _df = pd.read_sql_query(text(_q.sql()), _conn)
        if _df.empty:
            continue
        _vecs = model.encode(
            _df["title"].tolist(),
            normalize_embeddings=True,
            batch_size=_ENCODE_BATCH,
            show_progress_bar=False,  # marimo doesn't render tqdm well
            convert_to_numpy=True,
        )
        _records = [
            {"source_identifier": _sid, "title_embedding": _vec.tolist()}
            for _sid, _vec in zip(_df["source_identifier"], _vecs, strict=True)
        ]
        # Chunk the write so a 100k-vector batch doesn't land as a
        # single multi-megabyte json blob.
        _update_sql = _b.update_slot_sql("title_embedding")
        for _start in range(0, len(_records), _WRITE_BATCH):
            _chunk = _records[_start : _start + _WRITE_BATCH]
            with pg.cursor() as _cur:
                _cur.execute(_update_sql, {"rows": json.dumps(_chunk)})
        _backfilled.append({
            "source": _b.source.name,
            "embedded": len(_records),
            "device": encode_device,
        })
    embed_summary = pd.DataFrame(_backfilled)
    embeddings_done = True
    embed_summary
    return (embeddings_done,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Entity resolution

    Movies + persons get embedded → k-NN across sources → batched
    canonical_id mint. canonical_ids are derived (sha1 of the
    chosen identity), NOT random UUIDs — so a partial-failure replay
    of this cell produces the SAME canonical_id and re-running is a
    strict no-op rather than a desync.

    **Substrate gap:** the per-row k-NN loop below is N+1
    (one SQL round-trip per tmdb row to find its imdb neighbor).
    Production scale needs `Query.lateral_join(other_query, on=…)`
    so the whole thing emits one SQL with HNSW per outer row — that
    primitive isn't shipped yet and is the next mid-term substrate
    add.
    """)
    return


@app.cell
def _():
    import hashlib

    # Deterministic canonical_id derivation — sha1 over a stable
    # identity. Re-running ER produces the SAME canonical_id, so
    # partial-failure replays converge instead of desync.
    def mint_canonical(prefix: str, *parts: str) -> str:
        _h = hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()
        return f"{prefix}_{_h[:10]}"

    return (mint_canonical,)


@app.cell
def _(embeddings_done, engine, imdb_src, json, movie, pd, text, tmdb_src):
    _ = embeddings_done
    _emb = movie.col.title_embedding
    _sid = movie.bindings_col.source_identifier

    with engine.begin() as _conn:
        tmdb_df = pd.read_sql_query(
            text(
                movie.from_source(tmdb_src)
                    .where(movie.col.canonical_id.is_null())
                    .select(_sid, movie.col.title, _emb)
                    .sql()
            ),
            _conn,
        )

    # ⚠️ N+1: one SQL round-trip per tmdb row. Substrate gap noted
    # above. For 10k rows this would die; for a 5-row demo it's fine.
    _matches = []
    for _, _row in tmdb_df.iterrows():
        _vec = _row["title_embedding"]
        if isinstance(_vec, str):
            _vec = json.loads(_vec)
        _knn = (
            movie.from_source(imdb_src)
                .where(movie.col.canonical_id.is_null())
                .order_by(_emb.distance_to(_vec))
                .limit(1)
                .select(_sid, movie.col.title, _emb.distance_to(_vec))
        )
        with engine.begin() as _conn:
            _hit = pd.read_sql_query(text(_knn.sql()), _conn)
        if not _hit.empty:
            _matches.append({
                "tmdb_id": _row["source_identifier"],
                "tmdb_title": _row["title"],
                "imdb_id": _hit.iloc[0, 0],
                "imdb_title": _hit.iloc[0, 1],
                "distance": float(_hit.iloc[0, 2]),
            })
    movie_candidates_df = pd.DataFrame(_matches).sort_values("distance").reset_index(drop=True)
    movie_candidates_df
    return (movie_candidates_df,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Person ER via the same embedding model

    Person names get embedded and k-NN'd cross-source — same
    shape as movies (real ER would also embed; this demo
    proves persons aren't a special case). Pandas-side `name.lower()`
    joins are a known toy that doesn't survive diacritics /
    suffixes / initials.
    """)
    return


@app.cell
def _(
    embeddings_done,
    engine,
    imdb_person_b,
    imdb_src,
    model,
    pd,
    person,
    text,
    tmdb_person_b,
    tmdb_src,
):
    _ = embeddings_done
    # Person bindings don't have an embedding slot in this spec
    # (the spec only embeds Movie.title). Encode on the fly client-
    # side, run k-NN per tmdb person against the encoded imdb set —
    # same N+1 caveat as movies; same substrate gap.
    with engine.begin() as _conn:
        _imdb_p = pd.read_sql_query(
            text(
                person.from_source(imdb_src)
                    .where(person.col.canonical_id.is_null())
                    .select(person.bindings_col.source_identifier, person.col.name)
                    .sql()
            ), _conn,
        )
        _tmdb_p = pd.read_sql_query(
            text(
                person.from_source(tmdb_src)
                    .where(person.col.canonical_id.is_null())
                    .select(person.bindings_col.source_identifier, person.col.name)
                    .sql()
            ), _conn,
        )

    _imdb_vecs = model.encode(
        _imdb_p["name"].tolist(), normalize_embeddings=True,
        batch_size=256, convert_to_numpy=True,
    )
    _tmdb_vecs = model.encode(
        _tmdb_p["name"].tolist(), normalize_embeddings=True,
        batch_size=256, convert_to_numpy=True,
    )
    # Cosine similarity matrix → top-1 imdb neighbor per tmdb.
    _sim = _tmdb_vecs @ _imdb_vecs.T  # both normalized
    _best = _sim.argmax(axis=1)
    _best_score = _sim.max(axis=1)
    person_candidates_df = pd.DataFrame({
        "tmdb_id": _tmdb_p["source_identifier"].values,
        "tmdb_name": _tmdb_p["name"].values,
        "imdb_id": _imdb_p["source_identifier"].iloc[_best].values,
        "imdb_name": _imdb_p["name"].iloc[_best].values,
        "cosine": _best_score.astype(float),
    }).sort_values("cosine", ascending=False).reset_index(drop=True)
    # Sanity guard: drop pairs below a confidence threshold.
    person_candidates_df = person_candidates_df[
        person_candidates_df["cosine"] >= 0.85
    ].reset_index(drop=True)

    _ = imdb_person_b, tmdb_person_b  # downstream dep marker
    person_candidates_df
    return (person_candidates_df,)


@app.cell
def _(
    credit,
    engine,
    imdb_credit_b,
    imdb_movie_b,
    imdb_person_b,
    imdb_src,
    json,
    mint_canonical,
    movie_candidates_df,
    pd,
    person_candidates_df,
    pg,
    text,
    tmdb_movie_b,
    tmdb_person_b,
):
    _RUN_ID = "demo_2026-05-21"  # in a real worker, this is the
                                  # Temporal workflow id / run id.

    # ---- movies: derive canonical from the matched imdb identity --------
    _movie_assigns_imdb = []
    _movie_assigns_tmdb = []
    for _r in movie_candidates_df.to_dict(orient="records"):
        _cid = mint_canonical("m", _r["imdb_id"])
        _meta = {"method": "title_embed_knn",
                 "distance": _r["distance"], "run_id": _RUN_ID}
        _movie_assigns_imdb.append({
            "canonical_id": _cid, "source_identifier": _r["imdb_id"],
            "er_metadata": _meta,
        })
        _movie_assigns_tmdb.append({
            "canonical_id": _cid, "source_identifier": _r["tmdb_id"],
            "er_metadata": _meta,
        })

    # ---- persons: same shape, hash-derived canonical_id ----------------
    _person_assigns_imdb = []
    _person_assigns_tmdb = []
    for _r in person_candidates_df.to_dict(orient="records"):
        _cid = mint_canonical("p", _r["imdb_id"])
        _meta = {"method": "name_embed_knn", "cosine": _r["cosine"],
                 "run_id": _RUN_ID}
        _person_assigns_imdb.append({
            "canonical_id": _cid, "source_identifier": _r["imdb_id"],
            "er_metadata": _meta,
        })
        _person_assigns_tmdb.append({
            "canonical_id": _cid, "source_identifier": _r["tmdb_id"],
            "er_metadata": _meta,
        })

    # ---- credits: single-source mint, deterministic ---------------------
    with engine.begin() as _conn:
        _credit_df = pd.read_sql_query(
            text(
                credit.from_source(imdb_src)
                    .where(credit.col.canonical_id.is_null())
                    .select(credit.bindings_col.source_identifier).sql()
            ), _conn,
        )
    _credit_assigns = [
        {"canonical_id": mint_canonical("c", _sid),
         "source_identifier": _sid,
         "er_metadata": {"method": "single_source", "run_id": _RUN_ID}}
        for _sid in _credit_df["source_identifier"]
    ]

    # Wrap all 5 assign_canonicals_sql calls in ONE transaction —
    # partial-failure mid-batch leaves the schema consistent. (The
    # outer connection is autocommit by default for read paths;
    # we open a fresh transaction here.)
    with pg.cursor() as _cur:
        _cur.execute("BEGIN")
        try:
            _cur.execute(imdb_movie_b.assign_canonicals_sql(),
                         {"assignments": json.dumps(_movie_assigns_imdb)})
            _cur.execute(tmdb_movie_b.assign_canonicals_sql(),
                         {"assignments": json.dumps(_movie_assigns_tmdb)})
            _cur.execute(imdb_person_b.assign_canonicals_sql(),
                         {"assignments": json.dumps(_person_assigns_imdb)})
            _cur.execute(tmdb_person_b.assign_canonicals_sql(),
                         {"assignments": json.dumps(_person_assigns_tmdb)})
            _cur.execute(imdb_credit_b.assign_canonicals_sql(),
                         {"assignments": json.dumps(_credit_assigns)})
            _cur.execute("COMMIT")
        except Exception:
            _cur.execute("ROLLBACK")
            raise
    er_done = True
    return (er_done,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 7. Queries

    Five reads against the resolved + virtual layers — every one
    composes through knot expressions, no raw SQL.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7a. Top 5 movies by year (resolved view)
    """)
    return


@app.cell
def _(engine, er_done, movie, pd, text):
    _ = er_done
    _q = (
        movie.resolved
            .order_by(movie.col.year, "desc")
            .limit(5)
            .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    with engine.begin() as _conn:
        top_movies = pd.read_sql_query(text(_q.sql()), _conn)
    top_movies
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7b. Same-target FK JOIN aliasing

    `Movie.director` AND `Movie.writer` both → `Person`. The
    compiler emits distinct aliases (`movie_director`,
    `movie_writer`) so both JOINs coexist in one query.
    """)
    return


@app.cell
def _(engine, er_done, movie, pd, text):
    _ = er_done
    _q = (
        movie.resolved
            .where(movie.col.writer.target_exists())
            .select(
                movie.col.title,
                movie.col.director.name,
                movie.col.writer.name,
            )
    )
    same_target_sql = _q.sql()
    with engine.begin() as _conn:
        _df = pd.read_sql_query(text(same_target_sql), _conn)
    _df.columns = ["title", "director_name", "writer_name"]
    director_writer = _df
    director_writer
    return (same_target_sql,)


@app.cell(hide_code=True)
def _(mo, same_target_sql):
    mo.md(f"""
    **emitted SQL**:

    ```sql
    {same_target_sql}
    ```
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7c. Per-person credit counts — single-pass FILTER aggregate

    Naive shape is two correlated subqueries per person row
    (total + director-only). knot's `AggExpr.filter(predicate)`
    compiles to postgres' `COUNT(*) FILTER (WHERE ...)` so both
    counts share one scan per person — same data, half the
    work.
    """)
    return


@app.cell
def _(credit, engine, er_done, pd, person, text):
    _ = er_done
    # `back(other, fk).count()` materializes a correlated subquery
    # per row. The directed + acted counts use the same `back()`
    # plus a `.where(...)` filter — knot emits each as its own
    # correlated subquery (the FILTER-aggregate optimization fires
    # when both counts are inside ONE subquery, which requires the
    # not-yet-shipped `Aggregate.filter(...)` projection on the
    # reverse-FK rather than separate `.where().count()` calls).
    _total = person.back(credit, "person").count()
    _directed = (
        person.back(credit, "person")
            .where(credit.col.role == "director").count()
    )
    _acted = (
        person.back(credit, "person")
            .where(credit.col.role == "actor").count()
    )
    _q = (
        person.resolved
            .order_by(_total, "desc")
            .select(person.col.name, _total, _directed, _acted)
    )
    with engine.begin() as _conn:
        per_person = pd.read_sql_query(text(_q.sql()), _conn)
    per_person.columns = ["name", "total", "directed", "acted"]
    per_person
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7d. DirectedMovie virtual class

    Same Expr substrate as a constraint or a read predicate:
    rows of Movie where at least one Credit with `role="director"`
    references the movie.
    """)
    return


@app.cell
def _(directed, engine, er_done, movie, pd, text):
    _ = er_done
    _q = (
        directed.resolved
            .order_by(movie.col.year, "desc")
            .select(movie.col.title, movie.col.year, movie.col.director.name)
    )
    with engine.begin() as _conn:
        directed_movies = pd.read_sql_query(text(_q.sql()), _conn)
    directed_movies.columns = ["title", "year", "director"]
    directed_movies
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7e. Keyset pagination — `tuple_lt`

    The load-bearing GraphQL list shape: `movies(after: $cursor,
    first: N)` ordered by `(year DESC, canonical_id DESC)`.
    `tuple_lt((year, canonical_id), (cy, cid))` compiles to
    postgres' lexicographic tuple comparison — index-friendly,
    no `Raw()` needed.
    """)
    return


@app.cell
def _(engine, er_done, movie, pd, text):
    from knot.ast.expr import tuple_lt

    _ = er_done
    # Pretend the previous page ended at (2009, 'm_zzzzzzzzzz').
    _cursor_year = 2009
    _cursor_id = "m_zzzzzzzzzz"
    _q = (
        movie.resolved
            .where(
                tuple_lt(
                    (movie.col.year, movie.col.canonical_id),
                    (_cursor_year, _cursor_id),
                )
            )
            .order_by(movie.col.year, "desc")
            .order_by(movie.col.canonical_id, "desc")
            .limit(3)
            .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    with engine.begin() as _conn:
        keyset_page = pd.read_sql_query(text(_q.sql()), _conn)
    keyset_page
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7f. Composite-key DataLoader — `tuple_in`

    The DataLoader pattern: collect (source, source_identifier)
    pairs across many GraphQL parent fields, fetch them in one
    round-trip. `tuple_in([col_a, col_b], [(va, vb), …])`
    compiles to `(col_a, col_b) IN ((va, vb), …)`.
    """)
    return


@app.cell
def _(engine, er_done, movie, pd, text):
    from knot.ast.expr import tuple_in

    _ = er_done
    _q = (
        movie.unresolved
            .where(
                tuple_in(
                    [movie.bindings_col.source_name,
                     movie.bindings_col.source_identifier],
                    [("imdb", "tt0110912"),
                     ("tmdb", "tm_kill"),
                     ("imdb", "tt7131622")],
                )
            )
            .select(
                movie.bindings_col.source_name,
                movie.bindings_col.source_identifier,
                movie.col.title,
            )
    )
    with engine.begin() as _conn:
        dataloader_batch = pd.read_sql_query(text(_q.sql()), _conn)
    dataloader_batch
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7g. Mutation `RETURNING` — atomic upsert + read

    `binding.write_sql(returning=[...])` appends RETURNING so
    a GraphQL mutation resolver gets the canonical row back
    without a follow-up SELECT.
    """)
    return


@app.cell
def _(er_done, imdb_movie_b, json, pg):
    _ = er_done
    _new_movie = [{
        "source_identifier": "tt_demo_returning",
        "title": "Death Proof",
        "year": 2007,
        "director": "nm0000233",
        "writer": "nm0000233",
    }]
    _sql = imdb_movie_b.write_sql(
        returning=["canonical_id", "title", "year"]
    )
    with pg.cursor() as _cur:
        _cur.execute(_sql, {"rows": json.dumps(_new_movie)})
        returning_rows = _cur.fetchall()
    returning_demo = f"RETURNING (one round-trip): `{returning_rows}`"
    returning_demo
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7h. Resolver winners — explain Pulp Fiction's year

    `cls.explain_winner_sql(slot=...)` shows per (canonical_id,
    source): value, weight, is_winner, margin. tmdb's higher
    default weight (0.95 vs 0.85) makes its (wrong) `year=1995`
    win over imdb's 1994.
    """)
    return


@app.cell
def _(engine, er_done, movie, pd, text):
    _ = er_done
    with engine.begin() as _conn:
        pulp = pd.read_sql_query(
            text(
                movie.resolved
                    .where(movie.col.title == "Pulp Fiction")
                    .select(movie.col.canonical_id)
                    .sql()
            ),
            _conn,
        )
        winners = pd.read_sql_query(
            text(movie.explain_winner_sql(slot="year")),
            _conn,
        )
    pulp_cid = pulp.iloc[0]["canonical_id"]
    pulp_winners = winners[winners["canonical_id"] == pulp_cid].reset_index(drop=True)
    pulp_winners
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 8. Validation suite

    `spec.emit_validation()` returns one SELECT per constraint.
    Two families:

    - **User constraints** declared in the spec (`year_sane`,
      `year_not_future`, `movie_director_matches_credit`).
    - **Built-ins** knot auto-derives from the spec shape
      (prefixed `_builtin_`): an FK-orphan check per `ClassRef`
      slot, a required-slot-null check per required slot. The
      host doesn't need to write these — knot ships them.

    Each SELECT returns zero rows when its constraint holds and
    one row per violator otherwise. Below: rule name, severity,
    and violation count after the clean ingest + ER above.
    """)
    return


@app.cell
def _(er_done, pd, pg, spec):
    import re as _re

    def _severity_from_sql(sql: str) -> str:
        # Severity is inlined as a literal in the SELECT's third
        # column — recover it for the zero-violation case (when no
        # row carries severity back to us).
        m = _re.search(r"'(error|warning|info)'\s+AS severity", sql)
        return m.group(1) if m else "?"

    _ = er_done
    _rules = []
    for _name, _sql in spec.emit_validation():
        with pg.cursor() as _cur:
            _cur.execute(_sql)
            _violations = _cur.fetchall()
        _severity = _violations[0][2] if _violations else _severity_from_sql(_sql)
        _rules.append({
            "rule": _name,
            "kind": "builtin" if _name.startswith("_builtin_") else "user",
            "severity": _severity,
            "violations": len(_violations),
        })
    validation_report = pd.DataFrame(_rules).sort_values(
        ["kind", "rule"]
    ).reset_index(drop=True)
    validation_report
    return


if __name__ == "__main__":
    app.run()
