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
    import psycopg
    from sqlalchemy import create_engine

    return create_engine, pd, psycopg


@app.cell
def _():
    # Same shared spec module as 01_deploy. We only need the imdb
    # binding for ingest — the spec import surface stays minimal.
    from movies_spec import imdb, imdb_movie_b, movie

    return imdb, imdb_movie_b, movie


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
    SCHEMA = "knot_demo"  # set up by 01_deploy
    SCHEMA
    return SCHEMA, engine, pg


@app.cell
def _(pd):
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
    raw_df = pd.read_json("../data/movies/imdb/movies.json")
    print(f"loaded {len(raw_df)} rows")
    raw_df.head()
    return (raw_df,)


@app.cell
def _(SCHEMA, imdb_movie_b):
    # `binding.write_sql()` returns two SQL templates — both reference
    # a single `%(rows)s::jsonb` parameter. knot never touches the
    # rows; the host's connector binds them at execute time.
    close_out, insert = imdb_movie_b.write_sql(schema=SCHEMA)
    print("--- close_out ---")
    print(close_out)
    print("\n--- insert ---")
    print(insert)
    return close_out, insert


@app.cell
def _(close_out, insert, pg, raw_df):
    # Run both statements with the rows bound as one jsonb param.
    # raw_df → JSON via DataFrame.to_json("records") gives the JSON
    # array shape `jsonb_array_elements` expects.
    payload = raw_df.to_json(orient="records")
    with pg.cursor() as cur:
        cur.execute(close_out, {"rows": payload})
        cur.execute(insert, {"rows": payload})
    return


@app.cell
def _(SCHEMA, imdb, movie, engine, pd, pg):
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
    pd.read_sql_query(q.sql(schema=SCHEMA), engine)
    return


if __name__ == "__main__":
    app.run()
