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
    # knot — 04: ingest the new source (tmdb)

    The spec extension landed in 03_migrate: `tmdb` is now a real
    source on the spec, and `movie_bindings` has space for tmdb
    rows. Same write path as 02_ingest, different binding object.

    After this notebook, `movie_bindings` has rows from both
    sources, all with `canonical_id IS NULL` — ready for ER in
    05_er.
    """)
    return


@app.cell
def _():
    import pandas as pd
    import psycopg
    from sqlalchemy import create_engine

    return create_engine, pd, psycopg


@app.cell
def _():
    # `tmdb_movie_b` is only in scope after composing the .full
    # extension. The import is the apply.
    import movies_spec.full  # noqa: F401  — registers tmdb with the spec
    from movies_spec import movie
    from movies_spec.full import tmdb, tmdb_movie_b

    return movie, tmdb, tmdb_movie_b


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
def _(pd):
    # tmdb's catalog — same shape contract as imdb (each row carries a
    # source_identifier + the class slots + whatever extras the source
    # produces; extras land in raw_payload). Different field names
    # (tmdb_id, popularity, vote_average) are fine — they ride in
    # raw_payload and don't conflict with the spec.
    tmdb_df = pd.read_json("../data/movies/tmdb/movies.json")
    print(f"loaded {len(tmdb_df)} rows")
    tmdb_df.head()
    return (tmdb_df,)


@app.cell
def _(SCHEMA, pg, tmdb_df, tmdb_movie_b):
    # Same two-statement write path: close out any prior open row for
    # each source_identifier, then insert the new ones. tmdb_movie_b
    # writes only the slots it knows about; extras land in raw_payload.
    close_out, insert = tmdb_movie_b.write_sql(schema=SCHEMA)
    payload = tmdb_df.to_json(orient="records")
    with pg.cursor() as cur:
        cur.execute(close_out, {"rows": payload})
        cur.execute(insert, {"rows": payload})
    return


@app.cell
def _(SCHEMA, engine, pd, pg):
    # Verify per-source counts. Both sources should show up; nothing
    # is resolved (canonical_id NULL on every row), no embeddings yet.
    pd.read_sql_query(
        f"""
        SELECT source_name,
               COUNT(*) AS rows,
               COUNT(canonical_id) AS resolved,
               COUNT(title_embedding) AS embedded
        FROM {SCHEMA}.movie_bindings
        GROUP BY source_name
        ORDER BY source_name
        """,
        engine,
    )
    return


@app.cell
def _(SCHEMA, movie, engine, pd, pg, tmdb):
    # Top 10 newest tmdb claims — same Query shape 02_ingest used for
    # imdb, just `from_source(tmdb)` instead.
    q = (
        movie.from_source(tmdb)
        .order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    pd.read_sql_query(q.sql(schema=SCHEMA), engine)
    return


if __name__ == "__main__":
    app.run()
