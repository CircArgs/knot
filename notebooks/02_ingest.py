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
    # knot — 02: ingest

    Bind a source to a class. See the write SQL knot emits. Bind row
    data to it via the connector. Verify what landed.
    """)
    return


@app.cell
def _():
    import uuid

    import pandas as pd
    import psycopg

    return pd, psycopg, uuid


@app.cell
def _():
    # Same shared spec as 01_deploy — imports the spec, the Movie class,
    # the imdb Source, and the imdb→Movie binding from ``movies_spec.py``.
    # Real deployments share this exact import pattern: workers, the
    # service API, the ER pipeline all import from the same spec module.
    from movies_spec import imdb, imdb_movie_b, movie, spec

    imdb_movie_b
    return imdb, imdb_movie_b, movie, spec


@app.cell
def _(psycopg, spec, uuid):
    # Host plumbing + deploy. Schema name is a throwaway per-run id.
    pg = psycopg.connect(
        host="localhost",
        port=5433,
        user="knot",
        password="knot",
        dbname="knot",
        autocommit=True,
    )
    schema = f"knot_play_{uuid.uuid4().hex[:8]}"
    pg.execute(spec.ddl(schema=schema))
    schema
    return pg, schema


@app.cell
def _(pd):
    # Load real sample data from disk — imdb's movies.json. Each row
    # carries:
    #   * `source_identifier` — imdb's own key (e.g. "tt1838941").
    #     This is the only stable identity imdb knows about; knot's
    #     cross-source `canonical_id` doesn't exist yet — ER assigns
    #     it later (see 04_er).
    #   * the class slots (`title`, `year`, `director`) as native values.
    #   * extras (`imdb_rating`, `num_votes`, `box_office_usd`, …) that
    #     aren't in the spec — they ride along in the row dict and land
    #     in `raw_payload jsonb` on the binding row, recoverable later
    #     without re-fetching from imdb.
    raw_df = pd.read_json("../data/movies/imdb/movies.json")
    print(f"loaded {len(raw_df)} rows")
    raw_df.head()
    return (raw_df,)


@app.cell
def _(imdb_movie_b, schema):
    # `binding.write_sql()` returns two SQL templates — both reference a
    # single `%(rows)s::jsonb` parameter. knot never touches the rows;
    # the host's connector binds them at execute time.
    close_out, insert = imdb_movie_b.write_sql(schema=schema)
    print("--- close_out ---")
    print(close_out)
    print("\n--- insert ---")
    print(insert)
    return close_out, insert


@app.cell
def _(close_out, insert, pg, raw_df):
    # Run both statements with the rows bound as a single jsonb param.
    # raw_df → JSON via DataFrame.to_json (records orientation = a JSON
    # array of dicts, which is what jsonb_array_elements expects).
    # For an autocommit connection each cur.execute commits independently;
    # wrap in pg.transaction() if you want atomic close_out + insert.
    payload = raw_df.to_json(orient="records")
    with pg.cursor() as _cur:
        _cur.execute(close_out, {"rows": payload})
        _cur.execute(insert, {"rows": payload})
    return


@app.cell
def _(imdb, movie, pd, pg, schema):
    # Verify via ``movie.from_source(imdb)`` — one source's claims about
    # Movie. This is a Query over the raw bindings layer (one row per
    # source_identifier) scoped to ``source_name = 'imdb'``. The
    # ``resolved`` layer would be empty here: ER hasn't run yet, so
    # every row's ``canonical_id`` is still NULL.
    q = (
        movie.from_source(imdb)
        .order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    pd.read_sql_query(q.sql(schema=schema), pg)
    return


if __name__ == "__main__":
    app.run()
