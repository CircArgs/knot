"""knot — show-and-tell deck. One end-to-end story:

  define classes → get SQL → apply → load data → ingest →
  grow the spec → migrate → query → embed → ER → resolved view fills

Designed for slide view (toggle in the marimo UI). Each cell is
intentionally short — one idea, one slide. The deck reads its own
spec files so the meta-point is visible: this is all just Python
and SQL, nothing hidden.
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(layout_file="layouts/00_demo.marimo.slides.json")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # knot

    a reflective ontology compiler

    *typed Python spec → postgres DDL · resolved views · upserts + ER · query SQL*
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## the contract

    - **knot is a compiler.** Every method returns a SQL string.
    - **Pure Python library.** No HTTP, no connection pool, no scheduler.
    - **Postgres-only today.** Other dialects = sibling dispatch tables.
    - **No migration runtime.** `Spec.ddl()` emits the target;
      you pipe it through Atlas / sqldef / dbmate.
    - **The host owns connections, transactions, ER policy.**
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## just define some classes
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    from _demo import read_source

    _src = read_source(mo.notebook_dir() / "media_spec/person.py")
    mo.md(f"`media_spec/person.py`\n\n```python\n{_src}\n```")
    return (read_source,)


@app.cell(hide_code=True)
def _(mo, read_source):
    _src = read_source(mo.notebook_dir() / "media_spec/movies.py")
    mo.md(f"`media_spec/movies.py`\n\n```python\n{_src}\n```")
    return


@app.cell(hide_code=True)
def _(mo, read_source):
    _src = read_source(mo.notebook_dir() / "media_spec/base.py")
    mo.md(
        f"`media_spec/base.py` — the package root creates `spec` once "
        f"and composes the domain parts:\n\n```python\n{_src}\n```"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## what you get
    """)
    return


@app.cell
def _():
    from media_spec import spec

    return (spec,)


@app.cell(hide_code=True)
def _(mo, spec):
    mo.md(f"""
    - **classes:** {", ".join(spec.classes)}
    - **sources:** {", ".join(spec.sources)}
    - **bindings:** {len(spec.source_bindings)}
    - **constraints:** {len(spec.constraints)}
    - **schema:** `{spec.schema}`
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### class graph
    """)
    return


@app.cell
def _(mo, spec):
    from _viz import class_graph

    mo.mermaid(class_graph(spec))
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## ask for the SQL
    """)
    return


@app.cell(hide_code=True)
def _(mo, spec):
    ddl = spec.ddl()
    mo.md(
        f"`spec.ddl()` returns the full target schema as one idempotent "
        f"script ({len(ddl):,} chars across {ddl.count(';')} statements):\n\n"
        f"```sql\n{ddl[:1800]}\n…\n```"
    )
    return (ddl,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## apply to a live DB
    """)
    return


@app.cell
def _():
    from _demo import SCHEMA, connect

    pg, engine = connect()
    pg.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    return SCHEMA, engine, pg


@app.cell
def _(ddl, pg):
    pg.execute(ddl)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### what landed
    """)
    return


@app.cell
def _(SCHEMA, engine):
    import pandas as pd

    pd.read_sql_query(
        """
        SELECT table_name AS name, table_type AS kind
        FROM information_schema.tables WHERE table_schema = %(s)s
        UNION ALL
        SELECT viewname, 'VIEW' FROM pg_views WHERE schemaname = %(s)s
        ORDER BY 2, 1
        """,
        engine,
        params={"s": SCHEMA},
    )
    return (pd,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## a bindings table

    One row per `(source, source_id)` — upserts on the PK, no SCD2
    history. ER stamps `canonical_id` + `er_metadata`; everything
    else comes from the source. Unmodeled extras land in `raw_payload`
    verbatim.
    """)
    return


@app.cell
def _(SCHEMA, engine, pd):
    pd.read_sql_query(
        """
        SELECT column_name, data_type, is_nullable
        FROM information_schema.columns
        WHERE table_schema = %(s)s AND table_name = 'movie_bindings'
        ORDER BY ordinal_position
        """,
        engine,
        params={"s": SCHEMA},
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## load some real data
    """)
    return


@app.cell
def _(mo, pd):
    rows_df = pd.read_json(mo.notebook_dir() / "../data/movies/imdb/movies.json")
    rows_df.head()
    return (rows_df,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## ask the binding for write SQL
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    from media_spec import imdb_movie_b

    write_sql = imdb_movie_b.write_sql()
    mo.md(
        f"`imdb_movie_b.write_sql()` returns one upsert template bound "
        f"to `%(rows)s::jsonb` — knot never sees the rows.\n\n"
        f"```sql\n{write_sql}\n```"
    )
    return (write_sql,)


@app.cell
def _(pg, rows_df, write_sql):
    import json

    payload = rows_df.to_json(orient="records")
    with pg.cursor() as _cur:
        _cur.execute(write_sql, {"rows": payload})
    return (json,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### what landed (queried via knot's `from_source`)
    """)
    return


@app.cell
def _(engine, pd):
    from media_spec import imdb, movie

    _q = (
        movie.from_source(imdb)
        .order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.title, movie.col.year)
    )
    pd.read_sql_query(_q.sql(), engine)
    return (movie,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## ingest the other sources + credits

    Same shape, one binding at a time: each `(source, class)` pair has a
    feed file; loop over `spec.source_bindings`, render
    `binding.write_sql()`, run it. Same write path, no per-source code.
    """)
    return


@app.cell
def _(json, mo, pg, spec):
    # Skip imdb→Movie (already ingested above). Skip any binding that
    # has no data feed (we only ship imdb/tmdb/rt × movies/persons/credits
    # in data/movies/).
    _data_root = mo.notebook_dir() / "../data/movies"
    _file_for = {"Movie": "movies.json", "Person": "persons.json",
                 "MovieCredit": "credits.json"}
    _summary = []
    for _b in spec.source_bindings:
        if _b.source.name == "imdb" and _b.class_.name == "Movie":
            continue  # already done
        if _b.class_.name not in _file_for:
            continue  # class without a v1-domain feed
        _path = _data_root / _b.source.name / _file_for[_b.class_.name]
        if not _path.exists():
            continue
        _rows = json.loads(_path.read_text())
        _payload = json.dumps(_rows)
        with pg.cursor() as _cur:
            _cur.execute(_b.write_sql(), {"rows": _payload})
        _summary.append({"source": _b.source.name,
                         "class": _b.class_.name, "rows": len(_rows)})
    print(f"ingested {sum(s['rows'] for s in _summary)} rows across "
          f"{len(_summary)} bindings")
    return


@app.cell(hide_code=True)
def _(SCHEMA, engine, pd):
    pd.read_sql_query(
        f"""
        SELECT source_name AS source,
               'Movie' AS class, COUNT(*) AS rows
        FROM {SCHEMA}.movie_bindings GROUP BY source_name
        UNION ALL
        SELECT source_name, 'Person', COUNT(*)
        FROM {SCHEMA}.person_bindings GROUP BY source_name
        UNION ALL
        SELECT source_name, 'MovieCredit', COUNT(*)
        FROM {SCHEMA}.moviecredit_bindings GROUP BY source_name
        ORDER BY class, source
        """,
        engine,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## grow the spec

    Add a file. Compose it in. The spec object grows.
    """)
    return


@app.cell(hide_code=True)
def _(mo, spec):
    before = {
        "classes": len(spec.classes),
        "sources": len(spec.sources),
        "bindings": len(spec.source_bindings),
    }
    import media_spec.full  # noqa: F401

    after = {
        "classes": len(spec.classes),
        "sources": len(spec.sources),
        "bindings": len(spec.source_bindings),
    }
    mo.md(
        f"""
        |          | before | after |
        |----------|-------:|------:|
        | classes  | {before["classes"]} | **{after["classes"]}** |
        | sources  | {before["sources"]} | **{after["sources"]}** |
        | bindings | {before["bindings"]} | **{after["bindings"]}** |
        """
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### class graph (full)
    """)
    return


@app.cell
def _(mo, spec):
    from _viz import class_graph as _cg

    mo.mermaid(_cg(spec))
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## migrate

    ```bash
    atlas schema diff \
      --from postgres://…/knot \
      --to file://target.sql \
      --dev-url postgres://…/atlas_dev \
      -s knot_demo
    ```

    knot emits the target; Atlas reconciles. knot's posture is
    the same as "we don't open connections" — the schema-diff
    is someone else's job.
    """)
    return


@app.cell
def _(spec):
    from pathlib import Path as _P

    target = _P("/tmp/knot_demo_target.sql")
    target.write_text(spec.ddl(include_views=False))
    return (target,)


@app.cell
def _(SCHEMA, target):
    import subprocess

    from _demo import reset_atlas_dev

    reset_atlas_dev()
    result = subprocess.run(
        [
            "atlas",
            "schema",
            "apply",
            "--url",
            "postgres://knot:knot@localhost:5433/knot?sslmode=disable",
            "--to",
            f"file://{target}",
            "--dev-url",
            "postgres://knot:knot@localhost:5433/atlas_dev?sslmode=disable",
            "-s",
            SCHEMA,
            "--auto-approve",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    print(result.stdout[:1500])
    return


@app.cell
def _(pg, spec):
    # Rebuild views after Atlas — community edition doesn't manage
    # views, so we re-execute the full DDL (CREATE OR REPLACE is
    # idempotent on tables, rebuilds views in place).
    pg.execute(spec.ddl())
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## read substrate
    """)
    return


@app.cell(hide_code=True)
def _(mo, movie):
    _q = movie.resolved.order_by(movie.col.year, "desc").limit(5)
    mo.md(
        f"```python\nmovie.resolved.order_by(movie.col.year, 'desc').limit(5)\n```\n\n"
        f"compiles to:\n\n```sql\n{_q.sql()}\n```"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## embeddings

    A worker fills `title_embedding` for every unembedded row.
    knot owns the schema (`vector(384)` + HNSW); the encoder
    choice is host policy.
    """)
    return


@app.cell
def _(engine, movie, pd, pg, spec):
    import json as _json

    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    # Embed every unembedded title across every source — fully through
    # knot:
    #   cls.from_source(s).where(col.is_null())     → unembedded rows
    #   binding.update_slot_sql("title_embedding")  → batched UPDATE
    _per_source = {}
    for _src_name in ("imdb", "tmdb", "rottentomatoes"):
        _src = spec.sources[_src_name]
        _binding = movie.binding_for(_src)
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
        _per_source[_src_name] = len(_df)
    print(f"embedded {sum(_per_source.values())} titles total: {_per_source}")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## entity resolution — cross-source k-NN

    Three sources have ingested the same movies under different ids:
    imdb's `tt001`, tmdb's `552`, rt's `pulp_fiction_1994`. The ER
    worker mints canonicals for imdb (the "anchor"), then for every
    tmdb / rt binding finds its nearest imdb neighbor via the HNSW
    title-embedding index. Close enough → reuse imdb's canonical_id.
    Far enough → mint a new one. Option-3's forward translation
    rewrites every FK column (`director`, `movie`, `person`) in the
    same atomic statement; the backward fan-out catches orphan
    credits referencing not-yet-ER'd movies.
    """)
    return


@app.cell
def _(SCHEMA, engine, json, movie, pd, pg, spec):
    import uuid

    # Phase 1 — mint canonicals for every imdb binding. Imdb is the
    # anchor; every other source will be k-NN matched against it.
    _imdb = spec.sources["imdb"]
    for _cls_name in ("Person", "Movie", "MovieCredit"):
        _cls = spec.classes[_cls_name]
        _b = _cls.binding_for(_imdb)
        _q = (
            _cls.unresolved
            .where(_cls.bindings_col.source_name == "imdb")
            .select(_cls.bindings_col.source_identifier)
        )
        _unresolved = pd.read_sql_query(_q.sql(), engine)
        _assign = _b.assign_canonical_sql()
        _prefix = _cls_name.lower()[:3]
        with pg.cursor() as _cur:
            for _si in _unresolved["source_identifier"]:
                _cur.execute(
                    _assign,
                    {
                        "canonical_id": f"{_prefix}_{uuid.uuid4().hex[:10]}",
                        "source_identifier": _si,
                        "er_metadata": json.dumps({"method": "anchor"}),
                    },
                )
        print(f"  imdb {_cls_name}: minted {len(_unresolved)} canonicals")

    # Phase 2 — k-NN match tmdb + rt movies against imdb via the HNSW
    # title_embedding index. CROSS JOIN LATERAL drives per-row k-NN;
    # the resolver's per-(source, class, slot) weights pick winners
    # at read time.
    _knn_sql = f"""
        SELECT other.source_name, other.source_identifier,
               nn.canonical_id AS imdb_canonical,
               nn.distance
        FROM {SCHEMA}.movie_bindings AS other
        CROSS JOIN LATERAL (
            SELECT canonical_id, title_embedding <=> other.title_embedding AS distance
            FROM {SCHEMA}.movie_bindings
            WHERE source_name='imdb'
              AND title_embedding IS NOT NULL
              AND canonical_id IS NOT NULL
            ORDER BY title_embedding <=> other.title_embedding
            LIMIT 1
        ) AS nn
        WHERE other.source_name IN ('tmdb','rottentomatoes')
          AND other.title_embedding IS NOT NULL
          AND other.canonical_id IS NULL
    """
    _candidates = pd.read_sql_query(_knn_sql, engine)

    _THRESHOLD = 0.20  # cosine distance; tuned by labeled data in real life
    _matched = 0
    for _src in ("tmdb", "rottentomatoes"):
        _b = movie.binding_for(spec.sources[_src])
        _assign = _b.assign_canonical_sql()
        _src_cand = _candidates[_candidates["source_name"] == _src]
        with pg.cursor() as _cur:
            for _row in _src_cand.itertuples(index=False):
                _is_match = _row.distance <= _THRESHOLD
                _cid = _row.imdb_canonical if _is_match else f"mov_{uuid.uuid4().hex[:10]}"
                _cur.execute(
                    _assign,
                    {
                        "canonical_id": _cid,
                        "source_identifier": _row.source_identifier,
                        "er_metadata": json.dumps({
                            "method": "knn_match" if _is_match else "knn_mint",
                            "distance": float(_row.distance),
                        }),
                    },
                )
                _matched += int(_is_match)
        print(f"  {_src} Movie: {len(_src_cand)} candidates, {_matched} matched ≤ {_THRESHOLD}")

    # Phase 3 — mint canonicals for every other unresolved binding
    # (tmdb/rt persons + credits, which don't have title embeddings).
    # The ER worker would normally k-NN these too against name
    # embeddings; we're keeping the slide deck honest, not exhaustive.
    for _src in ("tmdb", "rottentomatoes"):
        for _cls_name in ("Person", "MovieCredit"):
            _cls = spec.classes[_cls_name]
            _b = _cls.binding_for(spec.sources[_src])
            _q = (
                _cls.unresolved
                .where(_cls.bindings_col.source_name == _src)
                .select(_cls.bindings_col.source_identifier)
            )
            _unresolved = pd.read_sql_query(_q.sql(), engine)
            _assign = _b.assign_canonical_sql()
            _prefix = _cls_name.lower()[:3]
            with pg.cursor() as _cur:
                for _si in _unresolved["source_identifier"]:
                    _cur.execute(
                        _assign,
                        {
                            "canonical_id": f"{_prefix}_{uuid.uuid4().hex[:10]}",
                            "source_identifier": _si,
                            "er_metadata": json.dumps({"method": "knn_skip_mint"}),
                        },
                    )

    # Set per-source runtime weights so the resolver has a defined
    # argmax — imdb > tmdb > rt for simple cases.
    _WEIGHTS = {"imdb": 0.85, "tmdb": 0.70, "rottentomatoes": 0.55}
    with pg.cursor() as _cur:
        for _b in spec.source_bindings:
            if _b.source.name not in _WEIGHTS:
                continue
            _upsert = _b.upsert_weight_sql()
            for _slot in _b.class_.effective_slots():
                if _slot.identifier:
                    continue
                _cur.execute(_upsert,
                             {"slot_name": _slot.name, "weight": _WEIGHTS[_b.source.name]})
    print(f"set runtime weights for {len(_WEIGHTS)} sources")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## the resolved view fills

    Same `movie.resolved` query as before — now it returns
    rows.
    """)
    return


@app.cell
def _(engine, movie, pd):
    pd.read_sql_query(
        movie.resolved.order_by(movie.col.year, "desc").limit(10).sql(),
        engine,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## per-source provenance — `cls.all_sources`

    Same `canonical_id` per row, but each slot becomes a jsonb of
    `{source_name: {value, weight}}` so the UI can show "imdb says
    runtime=120, tmdb says 121, rt agrees with imdb". The resolved
    view's argmax winner is one of these.
    """)
    return


@app.cell
def _(engine, movie, pd):
    pd.read_sql_query(movie.all_sources.limit(5).sql(), engine)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## virtual class — `DirectedMovie`

    `movie.add_virtual("DirectedMovie", where=((movie_credit.col.movie == this.Movie) & (movie_credit.col.role == "director")).any())`
    compiles to a filtered view over `movie_resolved` — movies that
    have a "director" credit. The FK chain (`movie_credit.movie =
    movie.canonical_id`) only works because option-3 ER stamped both
    sides with canonical-ids.
    """)
    return


@app.cell(hide_code=True)
def _(SCHEMA, engine, mo, pd):
    # Show DirectedMovie's view body (the SQL knot emits). pg_views is
    # postgres metadata — catalog introspection is the one carve-out
    # to the "demos use knot expressions only" rule.
    _ddl = pd.read_sql_query(
        f"SELECT definition FROM pg_views "
        f"WHERE schemaname='{SCHEMA}' AND viewname='directedmovie'",
        engine,
    ).iloc[0]["definition"]
    mo.md(f"**view body knot emitted:**\n```sql\n{_ddl}\n```")
    return


@app.cell
def _(SCHEMA, engine, pd):
    # First 5 movies that pass the DirectedMovie filter — rendered as
    # a DataFrame so marimo formats it natively (no tabulate needed).
    pd.read_sql_query(
        f"SELECT canonical_id, title, year FROM {SCHEMA}.directedmovie "
        f"ORDER BY year DESC LIMIT 5",
        engine,
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## constraint validation — `year_sane`

    `movie.add_constraint("year_sane", body=movie.col.year >= 1888)`
    compiles to a SELECT that returns offending canonical_ids. Inject
    a 1700 movie binding (bypassing ingest), run validation, watch the
    SELECT find it. The host decides what to do — block, page, route
    to a review queue.
    """)
    return


@app.cell
def _(engine, json, movie, pd, pg, spec):
    import uuid as _uuid

    # Inject a single bad row through knot's own write + ER path.
    # In production this would never happen — the source's ingest
    # would surface the violation on a scheduled validator sweep.
    _imdb_movie_b = movie.binding_for(spec.sources["imdb"])
    _src_id = f"tt_bad_{_uuid.uuid4().hex[:6]}"
    _bad_id = f"mov_bad_{_uuid.uuid4().hex[:6]}"
    with pg.cursor() as _cur:
        _cur.execute(
            _imdb_movie_b.write_sql(),
            {"rows": json.dumps([{
                "source_identifier": _src_id,
                "title": "A Trip to Nowhere",
                "year": 1700,
            }])},
        )
        _cur.execute(
            _imdb_movie_b.assign_canonical_sql(),
            {
                "canonical_id": _bad_id,
                "source_identifier": _src_id,
                "er_metadata": json.dumps({"method": "ringer"}),
            },
        )

    # Run every constraint's validation SELECT — host policy decides
    # what to do with returned rows. Here we just surface them.
    _rules = dict(spec.emit_validation())
    pd.read_sql_query(_rules["year_sane"], engine)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## what knot emits for ER

    `assign_canonical_sql()` is one atomic statement — a chain of
    writable CTEs that does four things at once: stamp the binding's
    canonical_id, translate this row's own FK columns from source-id
    to canonical-id (forward), fan out to every referencing class's
    bindings to rewrite their FK columns where they held the just-
    stamped source-id (backward), and register the canonical in the
    identity table. Recanonicalize cascades canonical-id rewrites to
    every referencing binding.
    """)
    return


@app.cell(hide_code=True)
def _(mo, spec):
    _movie = spec.classes["Movie"]
    _b = _movie.binding_for(spec.sources["imdb"])
    _sql = _b.assign_canonical_sql()
    mo.md(
        f"`imdb_movie_b.assign_canonical_sql()`:\n\n```sql\n{_sql}\n```"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## that's the loop

    - **typed Python spec** (`Spec`, `OntologyClass`, `Source`, `SourceBinding`)
    - → **canonical SQL** (`spec.ddl()`)
    - → **schema deployed** (host or Atlas)
    - → **upsert ingest** (`binding.write_sql()`)
    - → **ER + weights at runtime** (`binding.assign_canonical_sql()`, `binding.upsert_weight_sql()`)
    - → **read substrate** (`cls.resolved`, `cls.all_sources`, `cls.from_source(s)`, `cls.unresolved`)
    - → **constraints + virtuals** (`spec.emit_validation()`, virtual class views)

    knot is a compiler. The host composes.
    """)
    return


if __name__ == "__main__":
    app.run()
