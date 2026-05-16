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
    import json
    import uuid

    import psycopg

    return json, psycopg, uuid


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
        host="localhost", port=5433,
        user="knot", password="knot", dbname="knot",
        autocommit=True,
    )
    schema = f"knot_play_{uuid.uuid4().hex[:8]}"
    pg.execute(spec.init_sql(schema=schema))
    schema
    return pg, schema


@app.cell
def _(json):
    # Load real sample data from disk — imdb's movies.json. Each row
    # carries:
    #   * `source_identifier` — imdb's own key (e.g. "tt1838941").
    #     This is the only stable identity imdb knows about; knot's
    #     cross-source `canonical_id` doesn't exist yet — ER assigns
    #     it later (see 03_er).
    #   * the class slots (`title`, `year`, `director`) as native values.
    #   * extras (`imdb_rating`, `num_votes`, `box_office_usd`, …) that
    #     aren't in the spec — they ride along in the row dict and land
    #     in `raw_payload jsonb` on the binding row, recoverable later
    #     without re-fetching from imdb.
    #
    # In a real worker this loader would be a pandas DataFrame from a
    # CSV/parquet, a Kafka pull, an HTTP fetch — anything that yields a
    # list[dict]. knot only cares about the dict shape.
    from pathlib import Path

    DATA = Path("../data/movies/imdb/movies.json")
    rows = json.loads(DATA.read_text())
    print(f"loaded {len(rows)} rows; first one:")
    rows[0]
    return (rows,)


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
def _(close_out, insert, json, pg, rows):
    # Run both statements with the rows bound as a single jsonb param.
    # For an autocommit connection each cur.execute commits independently;
    # wrap in pg.transaction() if you want atomic close_out + insert.
    payload = json.dumps(rows)
    with pg.cursor() as _cur:
        _cur.execute(close_out, {'rows': payload})
        _cur.execute(insert, {'rows': payload})
    return


@app.cell
def _(imdb, movie, pg, schema):
    # Verify via ``movie.bindings.from_source(imdb)``:
    #   - ``movie.bindings`` repoints the Query at the raw bindings
    #     layer (the resolved view filters NULL canonical_id, which
    #     is everything we just wrote — ER hasn't run yet).
    #   - ``.from_source(imdb)`` adds ``WHERE source_name = 'imdb'``,
    #     scoping to this one source's claims.
    q = (
        movie.bindings.from_source(imdb)
        .order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.canonical_id, movie.col.title, movie.col.year)
    )
    sql, params = q.sql(schema=schema)
    with pg.cursor() as _cur:
        _cur.execute(sql, params or None)
        cols = [d.name for d in _cur.description]
        for row in _cur.fetchall():
            print(dict(zip(cols, row)))
    return


if __name__ == "__main__":
    app.run()
