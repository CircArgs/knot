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
    # knot — the five-persona tour

    Five engineers walk into a bar. The bartender pours them
    the same library — **knot**, a multi-source MDM postgres
    SQL compiler — and asks each one: *does this serve your job?*

    | avatar | who | what they own |
    |---|---|---|
    | 📦 | **Marcus** — ingest engineer | Temporal source workflows, batched writes, validation bucketing |
    | 🕸️ | **Priya** — graph architect | ontology, virtual classes, constraints, multi-hop reads |
    | 🤖 | **Sam** — ML engineer | embedding backfills, k-NN candidate finding, batched ER mints |
    | 🎛️ | **Alex** — API wrangler | GraphQL resolvers, keyset pagination, DataLoader batching |
    | ⚙️ | **Jordan** — platform steward | DDL deploy, Atlas migrations, weight tuning, resolver observability |

    Each section ends with the persona signing off in their own
    voice on whether what they saw is enough.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Setup — one connection, one throwaway schema

    Every persona shares the same postgres database and a fresh
    `knot_tour` schema. The notebook drops + recreates it on every
    run so we always start clean.
    """)
    return


@app.cell
def _():
    import json
    import uuid

    import numpy as np
    import pandas as pd
    import psycopg
    from sqlalchemy import create_engine, text

    return create_engine, json, np, pd, psycopg, text, uuid


@app.cell
def _(create_engine, psycopg, text):
    SCHEMA = "knot_tour"
    PG_URL = "postgresql+psycopg://knot:knot@localhost:5433/knot"

    pg = psycopg.connect(
        host="localhost", port=5433, user="knot",
        password="knot", dbname="knot", autocommit=True,
    )
    engine = create_engine(PG_URL)

    # Wipe + recreate so every run is hermetic.
    with engine.begin() as _conn:
        _conn.execute(text(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"))
        _conn.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        _conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    return SCHEMA, engine, pg


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## The spec — Movie, Person, Credit (with two FKs to the same target)

    Three concrete classes. `Movie.director` and `Movie.writer`
    both point at `Person` — same-target FKs are the JOIN-aliasing
    case Alex flagged.
    """)
    return


@app.cell
def _(SCHEMA):
    from knot import Spec, types

    spec = Spec(identifier_slot_name="canonical_id", schema=SCHEMA)

    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_country", types.TEXT)

    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)
    movie.slot("writer", person)
    movie.slot("title_embedding", types.VECTOR(8, metric="cosine"))

    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)
    return credit, movie, person, spec


@app.cell
def _(credit, movie, person, spec):
    # Two sources publish overlapping claims. imdb is the anchor;
    # tmdb has the (deliberately wrong) year for Pulp Fiction that
    # Jordan will chase down later.
    imdb_src = spec.add_source("imdb")
    tmdb_src = spec.add_source("tmdb")

    imdb_movie_b = imdb_src.bind(movie)
    imdb_person_b = imdb_src.bind(person)
    imdb_credit_b = imdb_src.bind(credit)

    tmdb_movie_b = tmdb_src.bind(movie)
    tmdb_person_b = tmdb_src.bind(person)

    # Implicit passthrough mappings — every spec slot's source field
    # matches its name. Rows just carry `source_identifier` + the
    # slot values; raw_payload captures the whole row. Weights are
    # runtime, set via binding.upsert_weight_sql after DDL deploy.
    return (
        imdb_credit_b,
        imdb_movie_b,
        imdb_person_b,
        imdb_src,
        tmdb_movie_b,
        tmdb_person_b,
        tmdb_src,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # ⚙️ Jordan: get this schema on disk

    > *"I don't write the application. I keep the database alive.
    > Show me the DDL, show me it's idempotent, show me the
    > Atlas-friendly views-only shape."*
    """)
    return


@app.cell
def _(pg, spec):
    # Spec.ddl() returns ONE canonical CREATE script — schema, extension,
    # tables, indexes, views. Idempotent throughout (IF NOT EXISTS /
    # CREATE OR REPLACE).
    ddl_sql = spec.ddl()
    with pg.cursor() as _cur:
        _cur.execute(ddl_sql)
    deployed = True
    return (deployed,)


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
    # Weights are runtime — every (source, class, slot) gets a row in
    # source_weight via upsert_weight_sql. Default for imdb = 0.85,
    # tmdb = 0.95 (so tmdb wins on disagreement — including Pulp
    # Fiction's year, the bug Jordan investigates later).
    _ = deployed
    _per_source_default = [
        (imdb_movie_b, 0.85),
        (imdb_person_b, 0.85),
        (imdb_credit_b, 0.85),
        (tmdb_movie_b, 0.95),
        (tmdb_person_b, 0.95),
    ]
    with pg.cursor() as _cur:
        for _b, _w in _per_source_default:
            _sql = _b.upsert_weight_sql()
            for _slot in _b.class_.effective_slots():
                if _slot.identifier:
                    continue
                _cur.execute(_sql, {"slot_name": _slot.name, "weight": _w})
    weights_set = True
    return


@app.cell
def _(SCHEMA, deployed, engine, pd, text):
    # Catalog introspection — what actually landed.
    _ = deployed  # ordering dep
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
    ### `Spec.views_ddl()` — the two-phase Atlas helper

    Atlas community can't manage views. The recipe is: pipe
    `spec.ddl(include_views=False)` through Atlas for the table
    diff, then run `spec.views_ddl()` to rebuild views with
    `CREATE OR REPLACE` (always safe — no data dependence).
    """)
    return


@app.cell
def _(spec):
    views_only = spec.views_ddl()
    # Confirm it's strictly the view-creation statements.
    n_views = views_only.count("CREATE OR REPLACE VIEW")
    n_tables = views_only.count("CREATE TABLE")
    n_indexes = views_only.count("CREATE INDEX")
    f"views_ddl: {n_views} views, {n_tables} tables, {n_indexes} indexes"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    > **⚙️ Jordan signs off:** *"DDL is idempotent, naming is
    > predictable, `views_ddl()` is exactly the named primitive
    > I wanted for the Atlas loop. **APPROVED.**"*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # 📦 Marcus: batch in imdb, validate, bucket the bad

    > *"My Temporal activity pulls 50k rows a night. One bad row
    > can't poison the batch. Show me: validate first, bucket
    > clean vs dirty, commit clean, route dirty to review."*
    """)
    return


@app.cell
def _():
    # Sample imdb batch — 5 movies, but one row is deliberately broken
    # (no source_identifier) to trigger Marcus's bucketing path.
    imdb_movie_rows = [
        # director + writer FKs hold imdb_ids pre-ER; ER translates
        # them to canonical_ids during assign_canonicals_sql. Pulp
        # Fiction has co-writer Avary — the same-target JOIN demo
        # (Movie.director + Movie.writer both → Person) needs both
        # FKs populated to return a non-trivial row.
        {"source_identifier": "tt0110912", "title": "Pulp Fiction", "year": 1994,
         "director": "nm0000233", "writer": "nm0000812"},
        {"source_identifier": "tt0105236", "title": "Reservoir Dogs", "year": 1992,
         "director": "nm0000233", "writer": "nm0000233"},
        {"source_identifier": "tt0266697", "title": "Kill Bill: Vol. 1", "year": 2003,
         "director": "nm0000233", "writer": "nm0000233"},
        {"source_identifier": "tt7131622", "title": "Once Upon a Time in Hollywood",
         "year": 2019, "director": "nm0000233", "writer": "nm0000233"},
        {"source_identifier": "tt0361748", "title": "Inglourious Basterds", "year": 2009,
         "director": "nm0000233", "writer": "nm0000233"},
        # The bad row — no source_identifier
        {"title": "Mystery Movie", "year": 9999},
    ]
    sample_payload_count = len(imdb_movie_rows)
    return (imdb_movie_rows,)


@app.cell
def _(imdb_movie_b, imdb_movie_rows, json, pd, pg):
    # binding.validate_rows_sql() — pure SELECT against the input.
    # Zero rows = clean batch; one row per violation otherwise. The
    # `%(rows)s` placeholder is psycopg style, so route through pg
    # rather than SQLAlchemy text().
    _validate_sql = imdb_movie_b.validate_rows_sql()
    with pg.cursor() as _cur:
        _cur.execute(_validate_sql, {"rows": json.dumps(imdb_movie_rows)})
        _cols = [d.name for d in _cur.description]
        _rows = _cur.fetchall()
    violations = pd.DataFrame(_rows, columns=_cols)
    violations
    return (violations,)


@app.cell
def _(imdb_movie_b, imdb_movie_rows, json, pg, violations):
    # Bucket: clean rows (indices not in violations) go through
    # write_sql; dirty rows would be routed to the host's review queue.
    _bad_indices = set(violations["row_index"].astype(int)) if not violations.empty else set()
    clean_rows = [r for i, r in enumerate(imdb_movie_rows) if i not in _bad_indices]
    bad_rows = [r for i, r in enumerate(imdb_movie_rows) if i in _bad_indices]

    with pg.cursor() as _cur:
        _cur.execute(imdb_movie_b.write_sql(), {"rows": json.dumps(clean_rows)})

    f"upserted {len(clean_rows)} clean rows; routed {len(bad_rows)} to review queue"
    return


@app.cell
def _(imdb_person_b, json, pg):
    # Marcus also seeds Persons + Credits from imdb in the same batch.
    # (Validation skipped here for brevity — same shape.)
    imdb_persons = [
        {"source_identifier": "nm0000233", "name": "Quentin Tarantino", "birth_country": "USA"},
        {"source_identifier": "nm0000237", "name": "John Travolta", "birth_country": "USA"},
        {"source_identifier": "nm0000235", "name": "Uma Thurman", "birth_country": "USA"},
        {"source_identifier": "nm0000093", "name": "Brad Pitt", "birth_country": "USA"},
        {"source_identifier": "nm0000812", "name": "Roger Avary", "birth_country": "Canada"},
    ]
    with pg.cursor() as _cur:
        _cur.execute(imdb_person_b.write_sql(), {"rows": json.dumps(imdb_persons)})
    f"upserted {len(imdb_persons)} persons"
    return


@app.cell
def _(imdb_credit_b, json, pg):
    # Director credits for all 5 movies + writer credit + acting credits.
    # Movie + Person columns hold imdb_ids pre-ER; assign_canonical
    # will translate them later.
    imdb_credits = [
        # Director credits — Tarantino on all 5
        {"source_identifier": "cr_d_pulp",    "role": "director", "movie": "tt0110912", "person": "nm0000233"},
        {"source_identifier": "cr_d_res",     "role": "director", "movie": "tt0105236", "person": "nm0000233"},
        {"source_identifier": "cr_d_kill",    "role": "director", "movie": "tt0266697", "person": "nm0000233"},
        {"source_identifier": "cr_d_once",    "role": "director", "movie": "tt7131622", "person": "nm0000233"},
        {"source_identifier": "cr_d_ingl",    "role": "director", "movie": "tt0361748", "person": "nm0000233"},
        # Actor credits
        {"source_identifier": "cr_a_pulp_t",  "role": "actor",    "movie": "tt0110912", "person": "nm0000237"},
        {"source_identifier": "cr_a_pulp_th", "role": "actor",    "movie": "tt0110912", "person": "nm0000235"},
        {"source_identifier": "cr_a_kill_th", "role": "actor",    "movie": "tt0266697", "person": "nm0000235"},
        {"source_identifier": "cr_a_once_p",  "role": "actor",    "movie": "tt7131622", "person": "nm0000093"},
        {"source_identifier": "cr_a_ingl_p",  "role": "actor",    "movie": "tt0361748", "person": "nm0000093"},
    ]
    with pg.cursor() as _cur:
        _cur.execute(imdb_credit_b.write_sql(), {"rows": json.dumps(imdb_credits)})
    f"upserted {len(imdb_credits)} credits"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### scope_to validation — delta-only constraint checking

    Constraint validation normally scans the whole resolved view.
    For a 50k batch, Marcus only cares about the canonical_ids
    his batch touched. `scope_to_source_identifiers=` inlines a
    bindings-table subquery that scopes the SELECT.
    """)
    return


@app.cell
def _(credit):
    # Add a constraint so we have something to validate. role must
    # be in a known whitelist — bad role = violation.
    role_constraint = credit.add_constraint(
        "credit_role_allowed",
        body=credit.col.role.in_(["director", "actor", "writer", "producer"]),
    )
    return (role_constraint,)


@app.cell
def _(engine, pd, role_constraint, spec, text):
    # Scoped to only the credits Marcus's batch touched — pre-existing
    # violations elsewhere wouldn't block.
    _ = role_constraint  # ordering dep
    scoped_validations = spec.emit_validation(
        scope_to_source_identifiers={
            "imdb": ["cr_d_pulp", "cr_d_res", "cr_d_kill", "cr_d_once", "cr_d_ingl"]
        }
    )
    _name, _sql = scoped_validations[0]
    with engine.begin() as _conn:
        scoped_violations = pd.read_sql_query(text(_sql), _conn)
    f"scoped validation '{_name}': {len(scoped_violations)} violations"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    > **📦 Marcus signs off:** *"`validate_rows_sql` gives me
    > per-row violations with the payload to route bad rows.
    > `scope_to_source_identifiers` is the delta-only checking I
    > wanted. The blast radius problem is solved. **APPROVED.**"*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # 🤖 Sam: now embed everything and resolve

    > *"I run embedding + ER workers. Each row needs an embedding
    > eventually. Pre-ER candidate-finding goes through HNSW on
    > the bindings table (the resolver view doesn't push the
    > index — knot's docs flag this). Batched ER mints land in
    > one round-trip."*
    """)
    return


@app.cell
def _(imdb_person_b, json, pg, tmdb_movie_b, tmdb_person_b):
    # tmdb publishes the same entities with its own ids. Note the
    # deliberately wrong year for Pulp Fiction — tmdb says 1995.
    tmdb_movies = [
        {"source_identifier": "tm_pulp",  "title": "Pulp Fiction", "year": 1995,  # wrong year!
         "director": "tp_tarantino", "writer": "tp_avary"},
        {"source_identifier": "tm_res",   "title": "Reservoir Dogs", "year": 1992,
         "director": "tp_tarantino", "writer": "tp_tarantino"},
        {"source_identifier": "tm_kill",  "title": "Kill Bill: Vol. 1", "year": 2003,
         "director": "tp_tarantino", "writer": "tp_tarantino"},
        {"source_identifier": "tm_once",  "title": "Once Upon a Time in Hollywood",
         "year": 2019, "director": "tp_tarantino", "writer": "tp_tarantino"},
        {"source_identifier": "tm_ingl",  "title": "Inglourious Basterds", "year": 2009,
         "director": "tp_tarantino", "writer": "tp_tarantino"},
    ]
    tmdb_persons = [
        {"source_identifier": "tp_tarantino", "name": "Quentin Tarantino", "birth_country": "USA"},
        {"source_identifier": "tp_travolta",  "name": "John Travolta", "birth_country": "USA"},
        {"source_identifier": "tp_thurman",   "name": "Uma Thurman", "birth_country": "USA"},
        {"source_identifier": "tp_pitt",      "name": "Brad Pitt", "birth_country": "USA"},
        {"source_identifier": "tp_avary",     "name": "Roger Avary", "birth_country": "Canada"},
    ]
    with pg.cursor() as _cur:
        _cur.execute(tmdb_movie_b.write_sql(), {"rows": json.dumps(tmdb_movies)})
        _cur.execute(tmdb_person_b.write_sql(), {"rows": json.dumps(tmdb_persons)})
    _ = imdb_person_b  # ordering dep to keep persons ingest before tmdb
    f"tmdb: {len(tmdb_movies)} movies + {len(tmdb_persons)} persons"
    return


@app.cell
def _(np):
    # Title → 8-dim vector. Mock encoder: deterministic per title so
    # imdb's Pulp Fiction and tmdb's Pulp Fiction land in the same
    # neighbourhood, and tarantino-ish titles cluster together.
    # Real workers swap this for sentence-transformers / OpenAI / etc.
    def _embed(title: str, jitter: float = 0.0) -> list[float]:
        _rng = np.random.default_rng(abs(hash(title)) % (2**32))
        _vec = _rng.normal(size=8)
        if jitter:
            _jrng = np.random.default_rng(abs(hash(title + "jitter")) % (2**32))
            _vec = _vec + jitter * _jrng.normal(size=8)
        _vec = _vec / np.linalg.norm(_vec)
        return _vec.tolist()

    embed = _embed
    return (embed,)


@app.cell
def _(embed, engine, imdb_movie_b, json, movie, pd, pg, text, tmdb_movie_b):
    # Sam's embedding-backfill activity: pull bindings whose embedding
    # is null, encode their titles, push back via update_slot_sql.
    # Per-source so HNSW indexes get used downstream.
    _total = 0
    for _b, _jitter in [(imdb_movie_b, 0.0), (tmdb_movie_b, 0.05)]:
        with engine.begin() as _conn:
            _df = pd.read_sql_query(
                text(
                    movie.from_source(_b.source)
                         .where(movie.col.title_embedding.is_null())
                         .select(movie.bindings_col.source_identifier, movie.col.title)
                         .sql()
                ),
                _conn,
            )
        if _df.empty:
            continue
        _payload = [
            {
                "source_identifier": _row["source_identifier"],
                "title_embedding": embed(_row["title"], jitter=_jitter),
            }
            for _, _row in _df.iterrows()
        ]
        with pg.cursor() as _cur:
            _cur.execute(
                _b.update_slot_sql("title_embedding"),
                {"rows": json.dumps(_payload)},
            )
        _total += len(_payload)
    f"embedded {_total} title vectors total"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Cross-source k-NN candidate finding

    For each tmdb movie, find the nearest imdb movie by cosine
    distance. Two-query pattern through knot expressions: fetch
    tmdb embedding, then issue a per-row k-NN against
    `cls.from_source(imdb)` so HNSW fires.
    """)
    return


@app.cell
def _(engine, imdb_src, json, movie, pd, text, tmdb_src):
    # First pass: get all tmdb embeddings + titles.
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

    candidates = []
    for _, _row in tmdb_df.iterrows():
        _vec = _row["title_embedding"]
        if isinstance(_vec, str):
            _vec = json.loads(_vec)
        _knn_q = (
            movie.from_source(imdb_src)
                 .where(movie.col.canonical_id.is_null())
                 .order_by(_emb.distance_to(_vec))
                 .limit(1)
                 .select(_sid, movie.col.title, _emb.distance_to(_vec))
        )
        with engine.begin() as _conn:
            _hit = pd.read_sql_query(text(_knn_q.sql()), _conn)
        if not _hit.empty:
            candidates.append({
                "tmdb_id": _row["source_identifier"],
                "tmdb_title": _row["title"],
                "imdb_id": _hit.iloc[0, 0],
                "imdb_title": _hit.iloc[0, 1],
                "distance": float(_hit.iloc[0, 2]),
            })
    candidates_df = pd.DataFrame(candidates).sort_values("distance").reset_index(drop=True)
    candidates_df
    return (candidates_df,)


@app.cell
def _(
    candidates_df,
    imdb_movie_b,
    imdb_person_b,
    json,
    pg,
    tmdb_movie_b,
    tmdb_person_b,
    uuid,
):
    # Batched ER mint via assign_canonicals_sql: mint a canonical_id
    # per matched pair, write imdb's assignment + tmdb's assignment
    # in one round-trip per source.
    _matches = candidates_df.to_dict(orient="records")
    _canonicals = {row["imdb_id"]: f"m_{uuid.uuid4().hex[:8]}" for row in _matches}

    _imdb_assignments = [
        {
            "canonical_id": _canonicals[row["imdb_id"]],
            "source_identifier": row["imdb_id"],
            "er_metadata": {"method": "knn_blocking", "distance": row["distance"]},
        }
        for row in _matches
    ]
    _tmdb_assignments = [
        {
            "canonical_id": _canonicals[row["imdb_id"]],
            "source_identifier": row["tmdb_id"],
            "er_metadata": {"method": "knn_blocking", "distance": row["distance"]},
        }
        for row in _matches
    ]

    with pg.cursor() as _cur:
        _cur.execute(
            imdb_movie_b.assign_canonicals_sql(),
            {"assignments": json.dumps(_imdb_assignments)},
        )
        _cur.execute(
            tmdb_movie_b.assign_canonicals_sql(),
            {"assignments": json.dumps(_tmdb_assignments)},
        )

    # Persons + credits get minted too — deterministic identity-style
    # for the demo (persons share names across sources).
    _person_pairs = [
        ("nm0000233", "tp_tarantino"),
        ("nm0000237", "tp_travolta"),
        ("nm0000235", "tp_thurman"),
        ("nm0000093", "tp_pitt"),
        ("nm0000812", "tp_avary"),
    ]
    _person_canonicals = {imdb_id: f"p_{uuid.uuid4().hex[:8]}" for imdb_id, _ in _person_pairs}
    _imdb_person_assigns = [
        {"canonical_id": _person_canonicals[i], "source_identifier": i, "er_metadata": None}
        for i, _ in _person_pairs
    ]
    _tmdb_person_assigns = [
        {"canonical_id": _person_canonicals[i], "source_identifier": t, "er_metadata": None}
        for i, t in _person_pairs
    ]
    with pg.cursor() as _cur:
        _cur.execute(
            imdb_person_b.assign_canonicals_sql(),
            {"assignments": json.dumps(_imdb_person_assigns)},
        )
        _cur.execute(
            tmdb_person_b.assign_canonicals_sql(),
            {"assignments": json.dumps(_tmdb_person_assigns)},
        )

    er_done = True
    f"minted {len(_canonicals)} movies + {len(_person_canonicals)} persons across both sources"
    return (er_done,)


@app.cell
def _(er_done, imdb_credit_b, json, pg):
    # Wire writer credit so Movie.writer FK has data — needed for
    # Alex's same-target JOIN demo. Avary co-wrote Pulp Fiction.
    _ = er_done
    _writer_credit = [
        {"source_identifier": "cr_w_pulp", "role": "writer", "movie": "tt0110912", "person": "nm0000812"},
    ]
    with pg.cursor() as _cur:
        _cur.execute(imdb_credit_b.write_sql(), {"rows": json.dumps(_writer_credit)})

    # ER-stamp every credit (imdb-only — single source) so canonical_ids
    # are populated and FK columns translate from imdb_ids → canonical_ids.
    _credit_ids = [
        "cr_d_pulp", "cr_d_res", "cr_d_kill", "cr_d_once", "cr_d_ingl",
        "cr_a_pulp_t", "cr_a_pulp_th", "cr_a_kill_th", "cr_a_once_p", "cr_a_ingl_p",
        "cr_w_pulp",
    ]
    import uuid as _uuid
    _credit_assigns = [
        {"canonical_id": f"c_{_uuid.uuid4().hex[:8]}", "source_identifier": cid, "er_metadata": None}
        for cid in _credit_ids
    ]
    with pg.cursor() as _cur:
        _cur.execute(
            imdb_credit_b.assign_canonicals_sql(),
            {"assignments": json.dumps(_credit_assigns)},
        )

    # Movie.director + Movie.writer FK columns were populated at
    # ingest time with imdb_ids; the prior assign_canonicals_sql
    # calls forward-translated them to canonical_ids. No follow-up
    # needed — Alex's JOIN aliasing demo reads them directly.
    full_er = True
    return (full_er,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    > **🤖 Sam signs off:** *"`update_slot_sql` for backfill,
    > `from_source` for HNSW-routed k-NN, `distance_to` cross-row,
    > `assign_canonicals_sql` for batched mint. The workflow runs
    > in one Temporal activity with single round-trips at every
    > step. **APPROVED.**"*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # 🕸️ Priya: virtual classes, reverse-FK, FK-existence

    > *"The substrate is the graph. Same Expr for reads,
    > virtuals, and constraints. Reverse-FK navigates from the
    > parent side. FK existence is one method call."*
    """)
    return


@app.cell
def _(credit, full_er, movie):
    from knot import this

    # Virtual class: rows of Movie where at least one Credit with
    # role='director' references this Movie. The correlated-aggregate
    # form — same Expr as a constraint body or a read predicate.
    _ = full_er  # data ordering dep
    directed_movie_v = movie.add_virtual(
        "DirectedMovie",
        where=(
            (credit.col.movie == this.Movie) & (credit.col.role == "director")
        ).any(),
    )
    return (directed_movie_v,)


@app.cell
def _(directed_movie_v, movie):
    # Virtual-of-virtual: DirectedMovie further restricted to year >= 2000.
    recent_directed_v = directed_movie_v.add_virtual(
        "RecentDirectedMovie",
        where=movie.col.year >= 2000,
    )
    return (recent_directed_v,)


@app.cell
def _(credit, movie, person):
    # FK-existence constraint shorthand — every Credit's movie + person
    # FK must point at a canonical row that actually exists.
    _ = person
    _ = movie
    fk_exist_constraint_movie = credit.add_constraint(
        "credit_movie_exists",
        body=credit.col.movie.target_exists(),
    )
    fk_exist_constraint_person = credit.add_constraint(
        "credit_person_exists",
        body=credit.col.person.target_exists(),
    )
    return


@app.cell
def _(directed_movie_v, engine, recent_directed_v, spec, text):
    # Redeploy: views_ddl is idempotent — picks up the new virtual classes.
    _ = directed_movie_v
    _ = recent_directed_v
    with engine.begin() as _conn:
        _conn.execute(text(spec.views_ddl()))
    redeploy_done = True
    return (redeploy_done,)


@app.cell
def _(directed_movie_v, engine, movie, pd, redeploy_done, text):
    # Read from the virtual class — same query AST, just different layer.
    _ = redeploy_done
    _q = (
        directed_movie_v.resolved
            .order_by(movie.col.year, "desc")
            .select(movie.col.title, movie.col.year)
    )
    with engine.begin() as _conn:
        directed_df = pd.read_sql_query(text(_q.sql()), _conn)
    directed_df
    return


@app.cell
def _(credit, engine, pd, person, redeploy_done, text):
    # Reverse-FK aggregate: per Person, count their credits + count
    # specifically director credits. cls.back(other, fk_slot) is the
    # navigator; .count() / .where(...).count() materialize to
    # correlated subqueries.
    _ = redeploy_done
    from knot.ast.expr import count as _count

    _credits_total = person.back(credit, "person").count()
    _credits_directed = (
        person.back(credit, "person")
            .where(credit.col.role == "director")
            .count()
    )

    _q = (
        person.resolved
            .order_by(_credits_total, "desc")
            .select(person.col.name, _credits_total, _credits_directed)
    )
    with engine.begin() as _conn:
        per_person = pd.read_sql_query(text(_q.sql()), _conn)
    per_person.columns = ["name", "total_credits", "directed"]
    per_person
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    > **🕸️ Priya signs off:** *"Virtual-of-virtual, reverse-FK
    > via `back().where().count()`, FK existence via
    > `target_exists()`. Same Expr substrate everywhere. Nothing
    > forced me into raw SQL. **APPROVED.**"*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # 🎛️ Alex: GraphQL resolver patterns

    > *"Keyset pagination, same-target JOIN aliasing, composite
    > DataLoader, `RETURNING` on mutations. The hot paths."*
    """)
    return


@app.cell
def _(engine, movie, pd, redeploy_done, text):
    # 1. Keyset cursor pagination — (year DESC, canonical_id DESC).
    # Cursor: "after Once Upon a Time (2019, m_...)".
    from knot.ast.expr import tuple_lt

    _ = redeploy_done
    _cursor_year = 2019
    _cursor_id = "z"  # high sentinel so we get the page strictly after

    _q = (
        movie.resolved
            .where(tuple_lt(
                (movie.col.year, movie.col.canonical_id),
                (_cursor_year, _cursor_id),
            ))
            .order_by(movie.col.year, "desc")
            .order_by(movie.col.canonical_id, "desc")
            .limit(3)
            .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    with engine.begin() as _conn:
        keyset_page = pd.read_sql_query(text(_q.sql()), _conn)
    keyset_page
    return


@app.cell
def _(engine, movie, pd, redeploy_done, text):
    # 2. Same-target JOIN aliasing — Movie.director AND Movie.writer
    # both → Person. Used to collide; now aliased movie_director +
    # movie_writer.
    _ = redeploy_done
    _q = (
        movie.resolved
            .where(movie.col.writer.target_exists())  # only movies with a writer
            .select(
                movie.col.title,
                movie.col.director.name,
                movie.col.writer.name,
            )
    )
    _sql = _q.sql()
    print("emitted SQL — note the two distinct aliases (movie_director, movie_writer):")
    print(_sql)
    print()
    with engine.begin() as _conn:
        same_target_join = pd.read_sql_query(text(_sql), _conn)
    # Both director.name and writer.name come back as 'name' — rename
    # by position so marimo can render the DataFrame.
    same_target_join.columns = ["title", "director_name", "writer_name"]
    same_target_join
    return


@app.cell
def _(engine, movie, pd, redeploy_done, text):
    # 3. Composite-key DataLoader — tuple_in for batched (source, id)
    # lookups across multiple GraphQL parent fields in one query.
    from knot.ast.expr import tuple_in

    _ = redeploy_done
    _q = (
        movie.unresolved  # bindings layer, lets us project both source_name + source_identifier
            .where(tuple_in(
                [movie.bindings_col.source_name, movie.bindings_col.source_identifier],
                [("imdb", "tt0110912"), ("tmdb", "tm_kill"), ("imdb", "tt7131622")],
            ))
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


@app.cell
def _(imdb_movie_b, json, pg, redeploy_done):
    # 4. RETURNING — mutation resolver gets the upserted row back
    # atomically in one round-trip.
    _ = redeploy_done
    _new_movie = [{"source_identifier": "tt_new", "title": "Death Proof", "year": 2007}]
    _sql = imdb_movie_b.write_sql(returning=["canonical_id", "title", "year"])
    with pg.cursor() as _cur:
        _cur.execute(_sql, {"rows": json.dumps(_new_movie)})
        returned_rows = _cur.fetchall()
    f"RETURNING fetched: {returned_rows}"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    > **🎛️ Alex signs off:** *"`tuple_lt` for keyset, JOIN
    > aliases distinct per FK path, `tuple_in` for DataLoader,
    > `RETURNING` on writes. Every GraphQL hot path expressible
    > without `Raw()`. **APPROVED.**"*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # ⚙️ Jordan returns: weight gone wrong

    > *"Product flagged that Pulp Fiction shows year 1995 instead
    > of 1994. I suspect tmdb's weight is winning over imdb's.
    > Let me prove it via `explain_winner_sql`, then revert via
    > `delete_weight_sql`."*
    """)
    return


@app.cell
def _(engine, movie, pd, redeploy_done, text):
    # Confirm the symptom: what does Pulp Fiction show for year?
    _ = redeploy_done
    with engine.begin() as _conn:
        before_fix = pd.read_sql_query(
            text(
                movie.resolved
                    .where(movie.col.title == "Pulp Fiction")
                    .select(movie.col.canonical_id, movie.col.title, movie.col.year)
                    .sql()
            ),
            _conn,
        )
    before_fix
    return (before_fix,)


@app.cell
def _(before_fix, engine, movie, pd, text):
    # Investigation: explain_winner_sql shows per-source value +
    # weight + is_winner + margin for the year slot specifically.
    _sql = movie.explain_winner_sql(slot="year")
    with engine.begin() as _conn:
        winners = pd.read_sql_query(text(_sql), _conn)
    # Filter to just Pulp Fiction's canonical_id from the before_fix df.
    _pulp_cid = before_fix.iloc[0]["canonical_id"]
    pulp_year_winners = winners[winners["canonical_id"] == _pulp_cid].reset_index(drop=True)
    pulp_year_winners
    return


@app.cell
def _(pg, tmdb_movie_b):
    # The fix: drop tmdb's weight row for (Movie, year). With no
    # weight row, the resolver's LEFT JOIN returns NULL weight, and
    # NULLS LAST in the argmax makes imdb win.
    _delete_sql = tmdb_movie_b.delete_weight_sql("year")
    with pg.cursor() as _cur:
        _cur.execute(_delete_sql)
    f"deleted weight row, fix applied"
    return


@app.cell
def _(engine, movie, pd, text):
    # Confirm the fix: resolver should now pick imdb's 1994.
    with engine.begin() as _conn:
        after_fix = pd.read_sql_query(
            text(
                movie.resolved
                    .where(movie.col.title == "Pulp Fiction")
                    .select(movie.col.canonical_id, movie.col.title, movie.col.year)
                    .sql()
            ),
            _conn,
        )
    after_fix
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    > **⚙️ Jordan signs off (round 2):** *"Diagnosed the
    > misbehaviour in one SELECT, reverted via one DELETE
    > emitter, confirmed the fix. No ad-hoc SQL, no production
    > database session at risk. **APPROVED.**"*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ---

    # 🎉 All five sign off

    | avatar | persona | verdict |
    |---|---|---|
    | 📦 | Marcus — ingest | **APPROVED** — validate + bucket + scoped enforcement |
    | 🕸️ | Priya — graph | **APPROVED** — virtuals, reverse-FK, FK-existence shorthand |
    | 🤖 | Sam — ML / ER | **APPROVED** — backfill + cross-row k-NN + batched mint |
    | 🎛️ | Alex — API | **APPROVED** — keyset + JOIN aliasing + DataLoader + RETURNING |
    | ⚙️ | Jordan — platform | **APPROVED** — DDL deploy + views_ddl + explain_winner + delete_weight |

    The substrate is approved end-to-end. The notebook runs
    bottom-to-top against a fresh schema in seconds. Every cell
    composes through knot expressions; the only raw SQL is
    catalog introspection (information_schema, pg_views).
    """)
    return


if __name__ == "__main__":
    app.run()
