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
    # knot — 02: ingest imdb data

    The schema is up (deployed by `01_deploy`). The base spec
    declares an `imdb` source bound to `Movie`. Now we feed real
    rows through `imdb_movie_b.write_sql()` and verify they land
    in `movie_bindings`.

    Each row's `canonical_id` is **NULL** at ingest — ER hasn't
    run yet. The resolver view filters NULL canonical_ids out, so
    `movie.resolved` is empty until 05_er.
    """)
    return


@app.cell
def _():
    import pandas as pd
    from _demo import connect

    return connect, pd


@app.cell
def _():
    # Same shared spec module as 01_deploy. We only need the imdb
    # binding for ingest — the spec import surface stays minimal.
    from media_spec import imdb, imdb_movie_b, movie

    return imdb, imdb_movie_b, movie


@app.cell
def _(connect):
    # SCHEMA + creds come from ``_demo`` — every notebook in the arc
    # shares the same throwaway schema and the same postgres.
    pg, engine = connect()
    return engine, pg


@app.cell
def _(mo, pd):
    # imdb's catalog as a DataFrame. Each row carries:
    #   * `source_identifier` — imdb's own key (e.g. "tt1838941").
    #     The only stable identity imdb knows about; knot's
    #     cross-source `canonical_id` doesn't exist yet — ER assigns
    #     it in 05_er.
    #   * the class slots (`title`, `year`, `director`) as native values.
    #   * extras (`imdb_rating`, `num_votes`, `box_office_usd`, …) that
    #     aren't in the spec — they ride along in the row dict and
    #     land in `raw_payload jsonb` on the binding row, recoverable
    #     later without re-fetching from imdb.
    raw_df = pd.read_json(mo.notebook_dir() / "../data/movies/imdb/movies.json")
    print(f"loaded {len(raw_df)} rows")
    raw_df.head()
    return (raw_df,)


@app.cell
def _(imdb_movie_b):
    # `binding.write_sql()` returns one upsert SQL template — INSERT
    # ... ON CONFLICT (source_name, source_identifier) DO UPDATE SET ...
    # — referencing a single `%(rows)s::jsonb` parameter. knot never
    # touches the rows; the host's connector binds them at execute time.
    write_sql = imdb_movie_b.write_sql()
    print(write_sql)
    return (write_sql,)


@app.cell
def _(pg, raw_df, write_sql):
    # Run the upsert with the rows bound as one jsonb param. raw_df →
    # JSON via DataFrame.to_json("records") gives the JSON array shape
    # `jsonb_array_elements` expects.
    payload = raw_df.to_json(orient="records")
    with pg.cursor() as cur:
        cur.execute(write_sql, {"rows": payload})
    return


@app.cell
def _(engine, imdb, movie, pd):
    # Verify via ``movie.from_source(imdb)`` — one source's claims
    # about Movie. This is a Query over the raw bindings layer (one
    # row per source_identifier) scoped to ``source_name = 'imdb'``.
    # The ``resolved`` layer would be empty here: ER hasn't run yet,
    # so every row's ``canonical_id`` is still NULL.
    q = (
        movie.from_source(imdb)
        .order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    pd.read_sql_query(q.sql(), engine)
    return


if __name__ == "__main__":
    app.run()
