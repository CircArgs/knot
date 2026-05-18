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
    # knot — 01: deploy

    Build a spec → see the SQL knot generates → execute it → look at the
    tables. Subsequent notebooks (02_ingest, 03_query, ...) cover the
    runtime concerns.
    """)
    return


@app.cell
def _():
    import uuid

    import psycopg

    return psycopg, uuid


@app.cell
def _():
    # The spec lives in ``movies_spec.py`` next to this notebook — same
    # pattern as a real deployment, where the workers + service API all
    # import a shared spec module. See that file for the actual class +
    # source + binding declarations.
    from movies_spec import spec

    spec
    return (spec,)


@app.cell
def _(psycopg, uuid):
    # Host plumbing — psycopg connection + a fresh per-run schema name
    # so re-running the notebook never collides with prior runs. We don't
    # create the schema here: ``Spec.ddl()`` emits ``CREATE SCHEMA IF NOT
    # EXISTS`` as its first statement.
    pg = psycopg.connect(
        host="localhost",
        port=5433,
        user="knot",
        password="knot",
        dbname="knot",
        autocommit=True,
    )
    schema = f"knot_play_{uuid.uuid4().hex[:8]}"
    schema
    return pg, schema


@app.cell
def _(schema, spec):
    # The canonical target schema for the spec, as one SQL script.
    # Nothing executes yet — just the text. Notice the order: schema
    # → weight table → canonical tables → bindings tables → indexes
    # → FK alters → resolved views → all-sources views.
    #
    # For migrations against a live DB, you wouldn't pg.execute this
    # directly — you'd pipe it through sqldef (or Atlas, dbmate, …)
    # to get a reconciling diff. See 03_migration for that loop.
    print(spec.ddl(schema=schema))
    return


@app.cell
def _(pg, schema, spec):
    # First deploy against an empty schema: just run the script. Every
    # statement is idempotent (IF NOT EXISTS / CREATE OR REPLACE), so
    # re-running is a no-op.
    pg.execute(spec.ddl(schema=schema))
    return


@app.cell
def _(pg, schema):
    # What landed? Ask postgres directly via information_schema —
    # describe-style introspection, not a knot read. For Person + Movie
    # we expect:
    #   - 1 invariant table: source_weight
    #   - 2 canonical tables: person, movie
    #   - 2 bindings tables: person_bindings, movie_bindings
    #   - 2 resolved views: person_resolved, movie_resolved
    #   - 2 all-sources views: person_all_sources, movie_all_sources
    with pg.cursor() as cur:
        cur.execute(
            """
            SELECT t.table_name, t.table_type,
                   c.column_name, c.data_type, c.is_nullable
            FROM information_schema.tables t
            JOIN information_schema.columns c
              USING (table_schema, table_name)
            WHERE t.table_schema = %s
            ORDER BY t.table_type, t.table_name, c.ordinal_position
            """,
            (schema,),
        )
        rows = cur.fetchall()

    current = None
    for name, kind, col, dtype, nullable in rows:
        if name != current:
            print(f"\n{kind:11s}  {name}")
            current = name
        null = "" if nullable == "YES" else " NOT NULL"
        print(f"               {col:24s} {dtype}{null}")
    return


if __name__ == "__main__":
    app.run()
