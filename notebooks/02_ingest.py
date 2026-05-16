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
    # and the imdb→Movie binding from ``movies_spec.py``. Real deployments
    # share this exact import pattern: workers, the service API, the ER
    # pipeline all import from the same spec module.
    from movies_spec import imdb_movie_b, movie, spec

    imdb_movie_b
    return imdb_movie_b, movie, spec


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
def _():
    # A batch of two rows from imdb. Each row carries:
    #   * `source_identifier` — imdb's own ID for the movie (their key).
    #   * `canonical_id` — knot's cross-source identity. Pre-assigned
    #     here (synchronous ER); a separate notebook will cover the
    #     async path where canonical_id starts NULL and gets assigned
    #     by an ER worker later.
    #   * the class slots (`title`, `year`) as native values.
    rows = [
        {
            "source_identifier": "tt0110912",
            "canonical_id": "m_pulpfiction",
            "title": "Pulp Fiction",
            "year": 1994,
        },
        {
            "source_identifier": "tt2878306",
            "canonical_id": "m_killbill1",
            "title": "Kill Bill: Vol. 1",
            "year": 2003,
        },
    ]
    rows
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
def _(movie, pg, schema):
    # Verify via a knot Query against the resolved view — what the
    # user-facing read API sees. Each cur.fetchone()-shape result row
    # is the merged (this notebook: single-source = pass-through)
    # per-canonical_id state.
    q = movie.order_by(movie.col.year).select(movie.col.canonical_id, movie.col.title, movie.col.year)
    sql, params = q.sql(schema=schema)
    with pg.cursor() as _cur:
        _cur.execute(sql, params or None)
        cols = [d.name for d in _cur.description]
        for row in _cur.fetchall():
            print(dict(zip(cols, row)))
    return


if __name__ == "__main__":
    app.run()
