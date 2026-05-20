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

    The spec is the package `media_spec/`, structured like a
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

    Loading `spec` gives you the v1: `base + person + movies`.
    The other domains stay opt-in until 03_migrate imports
    `media_spec.full`.
    """)
    return


@app.cell
def _():
    import pandas as pd
    from _demo import SCHEMA, connect

    return SCHEMA, connect, pd


@app.cell
def _():
    # ``from media_spec import spec`` → spec object with the BASE
    # entities only. media_spec.full has not been imported, so the
    # spec doesn't know about tmdb or the embedding slot yet.
    from media_spec import spec

    print("classes:", list(spec.classes))
    print("sources:", list(spec.sources))
    print("Movie slots:", [s.name for s in spec.classes["Movie"].slots])
    spec
    return (spec,)


@app.cell
def _(SCHEMA, connect):
    # ``_demo.connect()`` returns (psycopg, sqlalchemy engine). SCHEMA
    # is the fixed throwaway schema name every notebook uses; they
    # chain. 01 drops + recreates so re-runs are clean.
    pg, engine = connect()
    pg.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    return engine, pg


@app.cell
def _(spec):
    # The canonical target schema for the base spec, as one SQL script.
    # Nothing executes yet — just the text. Notice the order: schema →
    # weight table → canonical tables → bindings tables → indexes →
    # FK alters → resolved views → all-sources views.
    #
    # No CREATE EXTENSION vector — the base spec has no vector slot.
    print(spec.ddl())
    return


@app.cell
def _(pg, spec):
    # First deploy against an empty schema: execute directly. Every
    # statement is idempotent (CREATE TABLE IF NOT EXISTS / CREATE OR
    # REPLACE VIEW), so re-running is a no-op.
    pg.execute(spec.ddl())
    return


@app.cell
def _(SCHEMA, engine, pd):
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


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Visualize the v1 spec

    Classes + FK relationships. Rectangles = concrete classes (have
    a table); hexagons = virtual subclasses (a SQL view over the
    parent table). Edges are FK slots, labelled with the slot name.
    """)
    return


@app.cell
def _(mo, spec):
    from _viz import class_graph

    mo.mermaid(class_graph(spec))
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Bindings view

    Which sources publish which classes. Stadiums = sources;
    dotted edges = "this source publishes this class." For v1
    there's only `imdb` bound to `Movie`; in 03_migrate this view
    fills out across 14 sources × 11 concrete classes.
    """)
    return


@app.cell
def _(mo, spec):
    from _viz import binding_graph

    mo.mermaid(binding_graph(spec))
    return


if __name__ == "__main__":
    app.run()
