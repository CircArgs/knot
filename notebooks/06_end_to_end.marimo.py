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
    import uuid

    import pandas as pd
    import psycopg
    from sqlalchemy import create_engine, text

    return create_engine, json, pd, psycopg, text, uuid


@app.cell
def _(create_engine, psycopg, text):
    SCHEMA = "knot_tour"
    PG_URL = "postgresql+psycopg://knot:knot@localhost:5433/knot"

    pg = psycopg.connect(
        host="localhost", port=5433, user="knot",
        password="knot", dbname="knot", autocommit=True,
    )
    engine = create_engine(PG_URL)

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
    movie.slot("director", person)
    movie.slot("writer", person)
    # MiniLM-L6-v2 is 384-dim; cosine matches the metric used at
    # embed time + selects the right pgvector HNSW operator class.
    movie.slot("title_embedding", types.VECTOR(384, metric="cosine"))

    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
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
    movie.add_constraint("year_sane", body=movie.col.year >= 1888)
    credit.add_constraint(
        "credit_role_allowed",
        body=credit.col.role.in_(["director", "actor", "writer", "producer"]),
    )
    credit.add_constraint(
        "credit_movie_exists",
        body=credit.col.movie.target_exists(),
    )
    credit.add_constraint(
        "credit_person_exists",
        body=credit.col.person.target_exists(),
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
def _(
    deployed,
    imdb_credit_b,
    imdb_movie_b,
    imdb_person_b,
    pg,
    tmdb_movie_b,
    tmdb_person_b,
):
    _ = deployed
    _bindings_weights = [
        (imdb_movie_b, 0.85),
        (imdb_person_b, 0.85),
        (imdb_credit_b, 0.85),
        (tmdb_movie_b, 0.95),
        (tmdb_person_b, 0.95),
    ]
    with pg.cursor() as _cur:
        for _b, _w in _bindings_weights:
            _sql = _b.upsert_weight_sql()
            for _slot in _b.class_.effective_slots():
                if _slot.identifier:
                    continue
                _cur.execute(_sql, {"slot_name": _slot.name, "weight": _w})
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
    upsert. Clean rows commit; broken rows surface for review.
    """)
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
    rows = []
    for _b in spec.source_bindings:
        _path = paths[(_b.source.name, _b.class_.name)]
        if not _path.exists():
            rows.append({"source": _b.source.name, "class": _b.class_.name,
                         "raw": 0, "violations": 0, "upserted": 0,
                         "note": "no data file"})
            continue
        _records = json.loads(_path.read_text())

        # Validate, bucket clean vs broken.
        with pg.cursor() as _cur:
            _cur.execute(_b.validate_rows_sql(),
                         {"rows": json.dumps(_records)})
            _violations = _cur.fetchall()
        _bad = {v[0] for v in _violations}
        _clean = [r for i, r in enumerate(_records) if i not in _bad]

        with pg.cursor() as _cur:
            _cur.execute(_b.write_sql(), {"rows": json.dumps(_clean)})

        rows.append({"source": _b.source.name, "class": _b.class_.name,
                     "raw": len(_records), "violations": len(_bad),
                     "upserted": len(_clean), "note": ""})
    ingest_summary = pd.DataFrame(rows)
    ingest_done = True
    ingest_summary
    return (ingest_done,)


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
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    return (model,)


@app.cell
def _(
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
        _vecs = model.encode(_df["title"].tolist(), normalize_embeddings=True)
        _payload = [
            {"source_identifier": _sid, "title_embedding": _vec.tolist()}
            for _sid, _vec in zip(_df["source_identifier"], _vecs, strict=True)
        ]
        with pg.cursor() as _cur:
            _cur.execute(
                _b.update_slot_sql("title_embedding"),
                {"rows": json.dumps(_payload)},
            )
        _backfilled.append({"source": _b.source.name, "embedded": len(_payload)})
    embed_summary = pd.DataFrame(_backfilled)
    embeddings_done = True
    embed_summary
    return (embeddings_done,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## 6. Entity resolution

    For each tmdb movie, find the nearest imdb movie by cosine
    distance via `col.title_embedding.distance_to(target_vec)`.
    HNSW fires on the `from_source` layer — the resolver view's
    argmax-correlated-subquery blocks index push-down, so ER
    candidate-finding routes through the bindings layer.

    Mint shared canonical_ids per matched pair, then push them in
    one round-trip per source via
    `binding.assign_canonicals_sql()`. Persons get a deterministic
    name-match mint (toy demo — real ER would embed + cluster
    persons too).
    """)
    return


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
    candidates_df = pd.DataFrame(_matches).sort_values("distance").reset_index(drop=True)
    candidates_df
    return (candidates_df,)


@app.cell
def _(
    candidates_df,
    imdb_credit_b,
    imdb_movie_b,
    imdb_person_b,
    json,
    pg,
    tmdb_movie_b,
    tmdb_person_b,
    uuid,
):
    # Mint a canonical_id per matched movie pair; batch into one
    # round-trip per source.
    _movie_canonicals = {
        r["imdb_id"]: f"m_{uuid.uuid4().hex[:8]}"
        for r in candidates_df.to_dict(orient="records")
    }
    _imdb_movie_assigns = [
        {"canonical_id": _movie_canonicals[r["imdb_id"]],
         "source_identifier": r["imdb_id"],
         "er_metadata": {"method": "knn_cosine", "distance": r["distance"]}}
        for r in candidates_df.to_dict(orient="records")
    ]
    _tmdb_movie_assigns = [
        {"canonical_id": _movie_canonicals[r["imdb_id"]],
         "source_identifier": r["tmdb_id"],
         "er_metadata": {"method": "knn_cosine", "distance": r["distance"]}}
        for r in candidates_df.to_dict(orient="records")
    ]

    # Persons — deterministic name-match for the demo.
    _person_pairs = [
        ("nm0000233", "tp_tarantino"),
        ("nm0000237", "tp_travolta"),
        ("nm0000235", "tp_thurman"),
        ("nm0000093", "tp_pitt"),
        ("nm0000812", "tp_avary"),
    ]
    _person_canonicals = {i: f"p_{uuid.uuid4().hex[:8]}" for i, _ in _person_pairs}
    _imdb_person_assigns = [
        {"canonical_id": _person_canonicals[i], "source_identifier": i,
         "er_metadata": None}
        for i, _ in _person_pairs
    ]
    _tmdb_person_assigns = [
        {"canonical_id": _person_canonicals[i], "source_identifier": t,
         "er_metadata": None}
        for i, t in _person_pairs
    ]

    # Credits — single-source (imdb), straight mint.
    _credit_ids = [
        "cr_d_pulp", "cr_d_res", "cr_d_kill", "cr_d_once", "cr_d_ingl",
        "cr_a_pulp_t", "cr_a_pulp_th", "cr_a_kill_th", "cr_a_once_p",
        "cr_a_ingl_p", "cr_w_pulp",
    ]
    _credit_assigns = [
        {"canonical_id": f"c_{uuid.uuid4().hex[:8]}",
         "source_identifier": cid, "er_metadata": None}
        for cid in _credit_ids
    ]

    with pg.cursor() as _cur:
        _cur.execute(imdb_movie_b.assign_canonicals_sql(),
                     {"assignments": json.dumps(_imdb_movie_assigns)})
        _cur.execute(tmdb_movie_b.assign_canonicals_sql(),
                     {"assignments": json.dumps(_tmdb_movie_assigns)})
        _cur.execute(imdb_person_b.assign_canonicals_sql(),
                     {"assignments": json.dumps(_imdb_person_assigns)})
        _cur.execute(tmdb_person_b.assign_canonicals_sql(),
                     {"assignments": json.dumps(_tmdb_person_assigns)})
        _cur.execute(imdb_credit_b.assign_canonicals_sql(),
                     {"assignments": json.dumps(_credit_assigns)})
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
    mo.md(
        f"""
    **emitted SQL**:

    ```sql
    {same_target_sql}
    ```
        """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### 7c. Per-person credit counts (reverse-FK aggregate)

    `person.back(credit, "person").count()` materializes to a
    correlated `(SELECT COUNT(*) FROM credit_resolved WHERE …)`
    subquery — composes in `.select()` as a value expression.
    """)
    return


@app.cell
def _(credit, engine, er_done, pd, person, text):
    _ = er_done
    _total = person.back(credit, "person").count()
    _directed = (
        person.back(credit, "person")
            .where(credit.col.role == "director")
            .count()
    )
    _q = (
        person.resolved
            .order_by(_total, "desc")
            .select(person.col.name, _total, _directed)
    )
    with engine.begin() as _conn:
        per_person = pd.read_sql_query(text(_q.sql()), _conn)
    per_person.columns = ["name", "total_credits", "directed"]
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
    ### 7e. Resolver winners — explain Pulp Fiction's year

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


if __name__ == "__main__":
    app.run()
