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
    # knot — 01: deploy the **base** spec

    Walk-through arc:

    1. **01_deploy** (this notebook) — deploy the base spec
    2. 02_ingest — ingest imdb data
    3. 03_migrate — extend the spec to add tmdb + embeddings, run Atlas
    4. 04_ingest_tmdb — ingest the new source
    5. 05_er — compute embeddings, run ER, watch the resolver view fill

    The spec is the package `movies_spec/`:

    - `base.py` — Person + Movie + the imdb source binding. **Loaded
      by default** when you `from movies_spec import ...`.
    - `full.py` — adds the tmdb source and a `VECTOR(384)` slot for
      ER blocking. **Opt-in**: importing this module mutates the
      shared spec object — that's the migration story (a new file
      contributes to the same spec; `Spec.ddl()` reflects the new
      shape; the migration tool reconciles).

    This notebook only imports the base.
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
    # ``from movies_spec import spec`` → spec object with the BASE
    # entities only. movies_spec.full has not been imported, so the
    # spec doesn't know about tmdb or the embedding slot yet.
    from movies_spec import spec

    print("classes:", [c.name for c in spec.classes])
    print("sources:", [s.name for s in spec.sources])
    print("Movie slots:", [s.name for s in spec.classes[1].slots])
    spec
    return (spec,)


@app.cell
def _(create_engine, psycopg):
    # Fixed schema name `knot_demo` shared by every notebook in the
    # arc — they chain. 01 drops + recreates so re-runs are clean;
    # 02..05 assume the schema exists from the previous step.
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
    pg.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    SCHEMA
    return SCHEMA, engine, pg


@app.cell
def _(SCHEMA, spec):
    # The canonical target schema for the base spec, as one SQL script.
    # Nothing executes yet — just the text. Notice the order: schema →
    # weight table → canonical tables → bindings tables → indexes →
    # FK alters → resolved views → all-sources views.
    #
    # No CREATE EXTENSION vector — the base spec has no vector slot.
    print(spec.ddl(schema=SCHEMA))
    return


@app.cell
def _(SCHEMA, pg, spec):
    # First deploy against an empty schema: execute directly. Every
    # statement is idempotent (CREATE TABLE IF NOT EXISTS / CREATE OR
    # REPLACE VIEW), so re-running is a no-op.
    pg.execute(spec.ddl(schema=SCHEMA))
    return


@app.cell
def _(SCHEMA, engine, pd, pg):
    # What landed?
    pd.read_sql_query(
        """
        SELECT table_name, table_type
        FROM information_schema.tables
        WHERE table_schema = %(schema)s
        UNION ALL
        SELECT viewname, 'VIEW'
        FROM pg_views WHERE schemaname = %(schema)s
        ORDER BY 2, 1
        """,
        engine,
        params={"schema": SCHEMA},
    )
    return


if __name__ == "__main__":
    app.run()
