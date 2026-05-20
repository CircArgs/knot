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
    # knot — 03: extend the spec, migrate the live DB

    The team wants knot to cover more than movies: video games,
    podcasts, TV, and the messy webscraped mentions feed. Each
    new domain is a file in `media_spec/` — `games.py`,
    `podcasts.py`, `tv.py`, `webscraped.py`. Activating them is
    one import: `import media_spec.full`.

    That import IS the migration. It mutates the same shared
    `spec` object the v1 notebooks (`01_deploy`, `02_ingest`) ran
    against, growing it from **4 classes / 3 sources / 9 bindings**
    (v1, movies only) to **17 classes / 14 sources / 56 bindings**
    (full CKG).

    knot doesn't own the migration runtime — `Spec.ddl()` emits
    the canonical target schema, and we hand it to
    [Atlas](https://atlasgo.io/) to produce reconciling SQL
    against the live DB.

    ```
    spec change (Python)  →  Spec.ddl()  →  atlas diff/apply  →  live DB
    ```

    Same posture as the rest of the library: knot emits, the host
    composes with the tools it already trusts.
    """)
    return


@app.cell
def _():
    import subprocess
    from pathlib import Path

    import pandas as pd
    from _demo import SCHEMA, connect, reset_atlas_dev

    return Path, SCHEMA, connect, pd, reset_atlas_dev, subprocess


@app.cell
def _():
    # The spec as it stood after 01_deploy: base entities only, one
    # source, no embeddings.
    from media_spec import spec

    print("BEFORE — classes:", list(spec.classes))
    print("BEFORE — sources:", list(spec.sources))
    print("BEFORE — Movie slots:", [s.name for s in spec.classes["Movie"].slots])
    return (spec,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Compose in `media_spec.full`

    This single line is the entire spec change. The module mutates
    the shared `spec` object — adds the `title_embedding VECTOR(384)`
    slot to `Movie`, adds the `tmdb` source plus its bindings. No
    fork, no v1/v2 duplication; one spec that grew.
    """)
    return


@app.cell
def _(spec):
    import media_spec.full  # noqa: F401  — imported for side effect

    print("AFTER — classes:", list(spec.classes))
    print("AFTER — sources:", list(spec.sources))
    print("AFTER — Movie slots:", [s.name for s in spec.classes["Movie"].slots])
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ### Visualize the spec shape

    Class graph: 17 concrete classes + 1 virtual (DirectedMovie),
    FK edges by slot name. Persons sit at the centre — every
    domain's `Credit` class has an FK to it, since "a person who
    directed a movie" and "a person who hosted a podcast" are the
    same identity in the canonical knowledge graph.
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
    ### Source/binding graph

    14 sources fanning out across the classes. tmdb + imdb show up
    in both movies and tv (they're cross-domain catalog sources);
    Person is the most-bound class (every domain's source publishes
    Person rows).
    """)
    return


@app.cell
def _(mo, spec):
    from _viz import binding_graph

    mo.mermaid(binding_graph(spec))
    return


@app.cell
def _(connect, reset_atlas_dev):
    # Live postgres + Atlas dev DB. Atlas needs a clean throwaway DB
    # to render the desired schema into; reset it each run.
    pg, engine = connect()
    reset_atlas_dev()
    return engine, pg


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Emit the new target schema → file Atlas can read

    `include_views=False` because Atlas community doesn't manage
    views, and knot's `_resolved` / `_all_sources` views use
    `FILTER (WHERE …)` aggregates that some schema-diff parsers
    don't accept. We rebuild views in a separate step after Atlas
    finishes (idempotent `CREATE OR REPLACE`).
    """)
    return


@app.cell
def _(Path, spec):
    target_sql = Path("/tmp/knot_target.sql")
    target_sql.write_text(spec.ddl(include_views=False))
    print(f"wrote {target_sql} ({target_sql.stat().st_size} bytes)")
    return (target_sql,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## `atlas schema diff` — read the proposed migration

    Atlas introspects the live DB (`--from`), loads the target SQL
    into the dev DB to render the desired state (`--to` +
    `--dev-url`), and prints the reconciling SQL. Nothing is
    applied yet — review step.
    """)
    return


@app.cell
def _(SCHEMA, subprocess, target_sql):
    diff = subprocess.run(
        [
            "atlas",
            "schema",
            "diff",
            "--from",
            "postgres://knot:knot@localhost:5433/knot?sslmode=disable",
            "--to",
            f"file://{target_sql}",
            "--dev-url",
            "postgres://knot:knot@localhost:5433/atlas_dev?sslmode=disable",
            "-s",
            SCHEMA,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    print(diff.stdout)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## `atlas schema apply` — land the change

    `--auto-approve` for the demo. In production you'd review the
    diff interactively, or commit it to a versioned-migrations
    directory and let CI apply on merge.
    """)
    return


@app.cell
def _(SCHEMA, subprocess, target_sql):
    result = subprocess.run(
        [
            "atlas",
            "schema",
            "apply",
            "--url",
            "postgres://knot:knot@localhost:5433/knot?sslmode=disable",
            "--to",
            f"file://{target_sql}",
            "--dev-url",
            "postgres://knot:knot@localhost:5433/atlas_dev?sslmode=disable",
            "-s",
            SCHEMA,
            "--auto-approve",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    print(result.stdout)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Rebuild views via knot

    Atlas community didn't touch views. `Spec.ddl()` is idempotent
    throughout (`CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE
    VIEW`), so re-executing the full thing is safe — the table
    statements are no-ops since Atlas brought them in line, and
    the views get rebuilt against the new shape.
    """)
    return


@app.cell
def _(pg, spec):
    pg.execute(spec.ddl())
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Confirm — final shape

    Tables (now including the new vector column on movie / movie_bindings),
    indexes (including the HNSW index Atlas built), views regenerated.
    Existing imdb data from 02_ingest is preserved through the migration.
    """)
    return


@app.cell
def _(SCHEMA, engine, pd):
    pd.read_sql_query(
        """
        SELECT table_name AS name, table_type AS kind
        FROM information_schema.tables WHERE table_schema = %(schema)s
        UNION ALL
        SELECT viewname, 'VIEW' FROM pg_views WHERE schemaname = %(schema)s
        ORDER BY 2, 1
        """,
        engine,
        params={"schema": SCHEMA},
    )
    return


@app.cell
def _(SCHEMA, engine, pd):
    pd.read_sql_query(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = %(schema)s AND table_name = 'movie_bindings'
        ORDER BY ordinal_position
        """,
        engine,
        params={"schema": SCHEMA},
    )
    return


@app.cell
def _(SCHEMA, engine, pd):
    # HNSW index Atlas built from the spec's vector slot.
    pd.read_sql_query(
        """
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE schemaname = %(schema)s AND tablename = 'movie_bindings'
        ORDER BY indexname
        """,
        engine,
        params={"schema": SCHEMA},
    )
    return


@app.cell
def _(engine, pd, spec):
    # Imdb data survived the migration — `canonical_id` still NULL
    # (ER hasn't run), `title_embedding` NULL (embedding worker
    # hasn't run; both happen in 05_er). Counts are derived via
    # cls.from_source per source — knot's read API doesn't yet
    # express COUNT-as-projection, so the totals come from pandas.
    _movie = spec.classes["Movie"]
    _stats = []
    for _src in spec.sources.values():
        if _src.name == "_user_corrections":
            continue
        _df = pd.read_sql_query(_movie.from_source(_src).sql(), engine)
        if _df.empty:
            continue
        _stats.append({
            "source": _src.name,
            "rows": len(_df),
            "embedded": int(_df["title_embedding"].notna().sum()),
            "resolved": int(_df["canonical_id"].notna().sum()),
        })
    pd.DataFrame(_stats)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## What this didn't show (honest caveats)

    - **Rename detection.** Atlas can't tell a rename from a
      drop-and-add. Use Atlas's `--declare-renames` HCL, or write
      a pre-migration script. Standard autogen blind spot.
    - **Backfills.** Adding a `NOT NULL` column to a non-empty
      table needs an `UPDATE` first. Atlas surfaces the
      destructive op; the host owns the backfill.
    - **Vector dim/metric changes.** `VECTOR(384) → VECTOR(768)`
      is a column-type change Atlas detects, but every existing
      embedding has to be recomputed by the embedding worker
      before the new column is meaningful.
    - **Versioned migrations / down migrations / drift detection.**
      Atlas supports all of these via `atlas migrate` (versioned
      migration directory). This notebook uses `schema apply`
      for simplicity; production probably wants versioned.

    The point isn't that this demo handles every case — it's
    that the story is honest. knot emits the schema, Atlas
    reconciles, the team owns the policies.
    """)
    return


if __name__ == "__main__":
    app.run()
