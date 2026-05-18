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
    # knot — 01: deploy the **v1** spec (movies domain)

    Walk-through arc:

    1. **01_deploy** (this notebook) — deploy the v1 spec
    2. 02_ingest — ingest imdb movies
    3. 03_migrate — compose in games + podcasts + tv + webscraped,
       run Atlas to apply
    4. 04_ingest_more — ingest the 55 bindings the migration enabled
    5. 05_er — compute Movie embeddings, run ER, watch the resolver fill

    The spec is the package `movies_spec/`, structured like a
    FastAPI app:

    | file | what it owns |
    |---|---|
    | `base.py`       | the `Spec` object — three lines, no classes |
    | `person.py`     | the shared `Person` class + all its slots |
    | `movies.py`     | Movie + MovieCredit + DirectedMovie + 3 movie sources |
    | `games.py`      | Studio + Platform + Game + Release + GameCredit + 3 srcs |
    | `podcasts.py`   | Podcast + PodcastEpisode + PodcastCredit + 3 srcs |
    | `tv.py`         | Show + Season + TVEpisode + TVCredit + tvdb + cross-bind |
    | `webscraped.py` | Mention class + 4 low-trust scraper sources |
    | `full.py`       | composes the migration-time domains onto v1 |

    Loading `movies_spec` gives you the v1: `base + person + movies`.
    The other domains stay opt-in until 03_migrate imports
    `movies_spec.full`.
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
