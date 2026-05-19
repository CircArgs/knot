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
app = marimo.App()


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # knot

    a reflective ontology compiler

    *typed Python spec → postgres DDL · resolved views · SCD2 writes · query SQL*
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
    _src = (mo.notebook_dir() / "media_spec/person.py").read_text()
    mo.md(f"`media_spec/person.py`\n\n```python\n{_src}\n```")
    return


@app.cell(hide_code=True)
def _(mo):
    _src = (mo.notebook_dir() / "media_spec/movies.py").read_text()
    mo.md(f"`media_spec/movies.py`\n\n```python\n{_src}\n```")
    return


@app.cell(hide_code=True)
def _(mo):
    _src = (mo.notebook_dir() / "media_spec/base.py").read_text()
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

    SCD2 + raw_payload. Every source's claim is preserved with
    validity bounds; unmodeled extras land in `raw_payload`
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

    close_out, insert = imdb_movie_b.write_sql()
    mo.md(
        f"`imdb_movie_b.write_sql()` returns two templates, both bound "
        f"to `%(rows)s::jsonb` — knot never sees the rows.\n\n"
        f"**close_out:**\n```sql\n{close_out}\n```\n\n"
        f"**insert:**\n```sql\n{insert}\n```"
    )
    return close_out, imdb_movie_b, insert


@app.cell
def _(close_out, insert, pg, rows_df):
    import json

    payload = rows_df.to_json(orient="records")
    with pg.cursor() as _cur:
        _cur.execute(close_out, {"rows": payload})
        _cur.execute(insert, {"rows": payload})
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
def _(engine, movie, pd, pg):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer("all-MiniLM-L6-v2")
    rows = pd.read_sql_query(
        f"SELECT source_name, source_identifier, valid_from, title "
        f"FROM {movie.bindings_table_name} "
        f"WHERE source_name = 'imdb' AND title_embedding IS NULL",
        engine,
    )
    vecs = model.encode(rows["title"].tolist(), normalize_embeddings=True)
    with pg.cursor() as _cur:
        for (_sn, _si, _vf, __), _v in zip(
            rows.itertuples(index=False), vecs, strict=False
        ):
            _cur.execute(
                f"UPDATE {movie.bindings_table_name} SET title_embedding = "
                f"%(v)s::vector(384) WHERE source_name = %(sn)s "
                f"AND source_identifier = %(si)s AND valid_from = %(vf)s",
                {"v": str(_v.tolist()), "sn": _sn, "si": _si, "vf": _vf},
            )
    print(f"embedded {len(rows)} imdb titles")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## entity resolution

    For a real ER stage you'd ingest a second source and k-NN
    cross-source via the HNSW index. For this slide deck we
    just mint canonicals for the imdb rows so the resolved
    view has something to show.
    """)
    return


@app.cell
def _(engine, imdb_movie_b, json, movie, pd, pg):
    import uuid

    imdb_rows = pd.read_sql_query(
        f"SELECT source_identifier FROM {movie.bindings_table_name} "
        f"WHERE source_name = 'imdb' AND canonical_id IS NULL",
        engine,
    )
    assign = imdb_movie_b.assign_canonical_sql()
    with pg.cursor() as _cur:
        for _si in imdb_rows["source_identifier"]:
            _cur.execute(
                assign,
                {
                    "canonical_id": f"m_{uuid.uuid4().hex[:10]}",
                    "source_identifier": _si,
                    "er_metadata": json.dumps({"method": "mint"}),
                },
            )
    # Upsert a runtime weight so the resolver has something > 0 for imdb.
    with pg.cursor() as _cur:
        upsert = imdb_movie_b.upsert_weight_sql()
        for slot in ("title", "year", "director", "runtime_minutes"):
            _cur.execute(upsert, {"slot_name": slot, "weight": 0.85})
    print(f"minted {len(imdb_rows)} canonical_ids + set imdb weights")
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
    ## that's the loop

    - **typed Python spec** (`Spec`, `OntologyClass`, `Source`, `SourceBinding`)
    - → **canonical SQL** (`spec.ddl()`)
    - → **schema deployed** (host or Atlas)
    - → **SCD2 ingest** (`binding.write_sql()`)
    - → **ER + weights at runtime** (`binding.assign_canonical_sql()`, `binding.upsert_weight_sql()`)
    - → **read substrate** (`cls.resolved`, `cls.from_source(s)`, `cls.all_sources`)

    knot is a compiler. The host composes.
    """)
    return


if __name__ == "__main__":
    app.run()
