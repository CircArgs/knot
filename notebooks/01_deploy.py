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

    import pandas as pd
    import psycopg

    return pd, psycopg, uuid


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
def _(pd, pg, schema):
    # What landed? Ask postgres via information_schema. For Person +
    # Movie we expect: 1 invariant table (source_weight), 2 canonical
    # tables, 2 bindings tables, 2 resolved views, 2 all-sources views.
    introspect = pd.read_sql_query(
        """
        SELECT t.table_name, t.table_type,
               c.column_name, c.data_type, c.is_nullable
        FROM information_schema.tables t
        JOIN information_schema.columns c USING (table_schema, table_name)
        WHERE t.table_schema = %(schema)s
        ORDER BY t.table_type, t.table_name, c.ordinal_position
        """,
        pg,
        params={"schema": schema},
    )
    introspect
    return


if __name__ == "__main__":
    app.run()
