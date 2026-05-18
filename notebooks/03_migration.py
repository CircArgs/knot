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
    # knot — 03: schema evolution via Atlas

    knot is a *compiler* — `Spec.ddl()` returns the canonical target
    schema as one idempotent script. **Migrations against a live DB
    are someone else's problem**: knot hands you the schema you want,
    you pipe it through your team's existing schema-diff tool
    ([Atlas](https://atlasgo.io/) is the tested default; see
    `CLAUDE.md` §"Schema deployment" for alternatives) to get the
    reconciling SQL.

    This notebook walks through the loop end-to-end:

    1. Build spec v1, deploy to a fresh schema.
    2. Mutate the spec in place to grow a slot, a class, and a vector
       column.
    3. Hand v2's DDL to `atlas schema diff` and read the SQL it
       proposes.
    4. `atlas schema apply` to land the schema change.
    5. Re-run `Spec.ddl()` to rebuild views (Atlas community doesn't
       manage them — `CREATE OR REPLACE` is unconditional and cheap).
    6. Confirm the new shape with `\\d`.

    The whole pitch: **what about migrations** has the same answer as
    **what about connections**. knot doesn't own either; the host
    composes knot's output with the tools the team already trusts.
    """)
    return


@app.cell
def _():
    import subprocess
    from pathlib import Path

    import pandas as pd
    import psycopg

    return Path, pd, psycopg, subprocess


@app.cell
def _(psycopg):
    # Connect to the live postgres + ensure Atlas's dev DB exists.
    # Atlas needs a clean throwaway DB to render the desired schema
    # state; one CREATE DATABASE is the whole setup cost.
    pg = psycopg.connect(
        host="localhost",
        port=5433,
        user="knot",
        password="knot",
        dbname="knot",
        autocommit=True,
    )
    with psycopg.connect(
        host="localhost",
        port=5433,
        user="knot",
        password="knot",
        dbname="postgres",
        autocommit=True,
    ) as admin:
        admin.execute("DROP DATABASE IF EXISTS atlas_dev")
        admin.execute("CREATE DATABASE atlas_dev")

    SCHEMA = "knot_demo"
    pg.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
    SCHEMA
    return SCHEMA, pg


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## v1 — Person only

    The starting spec is deliberately small: one class, one slot. The
    point is to demonstrate the *change*, not the spec.
    """)
    return


@app.cell
def _():
    from knot import Spec, types

    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    spec
    return person, spec, types


@app.cell
def _(SCHEMA, pg, spec):
    # Deploy v1: idempotent CREATE script, executed directly. This is
    # the "first deploy" path — no diff tool needed, just run it.
    pg.execute(spec.ddl(schema=SCHEMA))
    return


@app.cell
def _(SCHEMA, pd, pg):
    # What landed?
    pd.read_sql_query(
        "SELECT table_name, table_type FROM information_schema.tables "
        "WHERE table_schema = %(schema)s "
        "UNION ALL "
        "SELECT viewname, 'VIEW' FROM pg_views WHERE schemaname = %(schema)s "
        "ORDER BY 2, 1",
        pg,
        params={"schema": SCHEMA},
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## v2 — mutate the spec in place

    We grow `Person` with a `birth_country` slot, add a new `Movie`
    class with an FK to `Person`, and stick a `title_embedding`
    `VECTOR(384)` slot on `Movie`. Pure spec edit — no SQL touched
    by hand.
    """)
    return


@app.cell
def _(person, spec, types):
    # Mutate in place — this is the "real" migration story: one spec
    # object grows over time. There's no `spec_v2` rebuild.
    person.slot("birth_country", types.TEXT)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)
    movie.slot("title_embedding", types.VECTOR(384))
    movie
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Emit v2 DDL → write to a file Atlas can read

    `include_views=False` because Atlas community doesn't manage
    views, and knot's `_resolved` / `_all_sources` views use
    `FILTER (WHERE …)` that some sqldef parsers don't accept. We
    handle views ourselves in the last step.
    """)
    return


@app.cell
def _(Path, SCHEMA, spec):
    target_sql = Path("/tmp/knot_target.sql")
    target_sql.write_text(spec.ddl(schema=SCHEMA, include_views=False))
    print(f"wrote {target_sql} ({target_sql.stat().st_size} bytes)")
    return (target_sql,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## `atlas schema diff` — read the proposed migration

    Atlas introspects the live DB (`--from`), loads the target SQL
    into the throwaway dev DB to render the desired state (`--to` +
    `--dev-url`), and prints the reconciling SQL. Nothing is applied
    yet — this is the review step.
    """)
    return


@app.cell
def _(SCHEMA, subprocess, target_sql):
    diff_cmd = [
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
    ]
    diff = subprocess.run(diff_cmd, capture_output=True, text=True, check=True)
    print(diff.stdout)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## `atlas schema apply` — land the change

    Same args, `--auto-approve` because this is a demo. In a real
    deployment you'd review the diff first, run apply interactively,
    or commit the diff to a versioned-migrations directory.
    """)
    return


@app.cell
def _(SCHEMA, subprocess, target_sql):
    apply_cmd = [
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
    ]
    apply_result = subprocess.run(apply_cmd, capture_output=True, text=True, check=True)
    print(apply_result.stdout)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Rebuild views via knot

    Atlas community didn't touch views. `Spec.ddl()` is idempotent
    throughout (`CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE
    VIEW`), so re-executing the full thing is safe — the table
    statements are no-ops since Atlas already brought them in line,
    and the views get rebuilt against the new schema shape.
    """)
    return


@app.cell
def _(SCHEMA, pg, spec):
    pg.execute(spec.ddl(schema=SCHEMA))
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Confirm — final shape

    Tables, columns, indexes (including the HNSW index on the new
    vector slot), and the regenerated views.
    """)
    return


@app.cell
def _(SCHEMA, pd, pg):
    relations = pd.read_sql_query(
        "SELECT table_name AS name, table_type AS kind "
        "FROM information_schema.tables WHERE table_schema = %(schema)s "
        "UNION ALL "
        "SELECT viewname, 'VIEW' FROM pg_views WHERE schemaname = %(schema)s "
        "ORDER BY 2, 1",
        pg,
        params={"schema": SCHEMA},
    )
    relations
    return


@app.cell
def _(SCHEMA, pd, pg):
    # movie_bindings columns — note the new title_embedding vector(384).
    pd.read_sql_query(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = %(schema)s AND table_name = 'movie_bindings' "
        "ORDER BY ordinal_position",
        pg,
        params={"schema": SCHEMA},
    )
    return


@app.cell
def _(SCHEMA, pd, pg):
    # movie_bindings indexes — including the HNSW Atlas built.
    pd.read_sql_query(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = %(schema)s AND tablename = 'movie_bindings' "
        "ORDER BY indexname",
        pg,
        params={"schema": SCHEMA},
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## What this demo doesn't show (honest caveats)

    - **Rename detection.** Atlas can't tell a rename from a
      drop-and-add; if you rename a slot in the spec, the diff will
      look like "drop old column, add new column" with data loss.
      Use Atlas's `--declare-renames` or write a `pre-migration` HCL
      script. Same blind spot every autogen has.
    - **Backfills.** Adding a `NOT NULL` column to a non-empty table
      needs an `UPDATE` first. Atlas surfaces the destructive op;
      the host owns the backfill.
    - **Vector dim/metric changes.** If you change `VECTOR(384)` →
      `VECTOR(768)`, Atlas correctly detects the column-type
      change — but every existing row's embedding has to be
      recomputed by your embedding worker before the migration is
      meaningful. The schema change is mechanical; the data
      migration is your problem.
    - **Versioned migrations / down migrations / drift detection
      over time.** Atlas supports all of these via its
      `atlas migrate` subcommand (versioned-migrations directory).
      This notebook uses `schema apply` for simplicity; production
      probably wants the versioned-migrations workflow.

    The point isn't that this demo handles every case — it's that
    the *story* is honest: knot emits the schema, Atlas reconciles,
    your team owns the policies. Same shape as "knot emits SQL,
    your host opens connections."
    """)
    return


if __name__ == "__main__":
    app.run()
