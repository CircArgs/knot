import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md(r"""
    # knot — end-to-end walkthrough

    Four stages:

    1. **Basic spec.** Movie + Person, one source (imdb). Deploy. Ingest. Query.
    2. **Add a second source.** tmdb publishes some of the same Movies with
       different values. Spec evolves; ``init_sql`` emits only the delta.
       Trust resolves per-slot winners.
    3. **Evolve the spec.** Add the Credit reified relation + a third source
       (rottentomatoes). Spec grows; ``init_sql`` adds the new table without
       touching the existing data.
    4. **Final.** Full spec + all data. Run the queries we sketched on paper.

    Data is real (well, real-ish) — sample multi-source data lives in
    ``data/movies/{imdb,tmdb,rottentomatoes}/`` and gets loaded
    incrementally per stage. No hardcoded rows in this notebook.
    """)
    return


@app.cell
def _():
    import json
    import uuid
    from pathlib import Path

    import psycopg

    DATA = Path("/mnt/main/code/knot/data/movies")

    def load(source: str, entity: str) -> list[dict]:
        """Read ``data/movies/<source>/<entity>.json`` as a list of dicts."""
        return json.loads((DATA / source / f"{entity}.json").read_text())

    pg = psycopg.connect(
        host="localhost",
        port=5433,
        user="knot",
        password="knot",
        dbname="knot",
        autocommit=True,
    )
    schema = f"knot_play_{uuid.uuid4().hex[:8]}"
    pg.execute(f"CREATE SCHEMA {schema}")
    return DATA, load, pg, schema


@app.cell
def _(load, mo):
    # Quick sanity check on the data layout.
    counts = {
        ("imdb", "persons"): len(load("imdb", "persons")),
        ("imdb", "movies"): len(load("imdb", "movies")),
        ("imdb", "credits"): len(load("imdb", "credits")),
        ("tmdb", "persons"): len(load("tmdb", "persons")),
        ("tmdb", "movies"): len(load("tmdb", "movies")),
        ("tmdb", "credits"): len(load("tmdb", "credits")),
        ("rottentomatoes", "persons"): len(load("rottentomatoes", "persons")),
        ("rottentomatoes", "movies"): len(load("rottentomatoes", "movies")),
        ("rottentomatoes", "credits"): len(load("rottentomatoes", "credits")),
    }
    rows = "\n".join(f"| `{src}` | {ent} | {n} |" for (src, ent), n in counts.items())
    mo.md(
        "**Source data on disk:**\n\n| source | entity | rows |\n|---|---|---|\n" + rows
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Stage 1 — basic spec (Movie + Person, imdb only)
    """)
    return


@app.cell
def _():
    from knot import Spec, types

    spec_v1 = Spec(id="movies_play", version="0.1")

    person_v1 = spec_v1.add_class("Person")
    person_v1.slot("canonical_id", types.TEXT, identifier=True)
    person_v1.slot("name", types.TEXT, required=True)
    person_v1.slot("birth_country", types.TEXT)
    person_v1.slot("birth_year", types.INTEGER)

    movie_v1 = spec_v1.add_class("Movie")
    movie_v1.slot("canonical_id", types.TEXT, identifier=True)
    movie_v1.slot("title", types.TEXT, required=True)
    movie_v1.slot("year", types.INTEGER)
    movie_v1.slot("runtime_minutes", types.INTEGER)
    movie_v1.slot("director", person_v1)  # FK to Person

    imdb_v1 = spec_v1.add_source("imdb")
    imdb_v1.bind(person_v1, base_trust=0.85)
    imdb_v1.bind(movie_v1, base_trust=0.85)

    spec_v1.validate()
    return Spec, imdb_v1, movie_v1, person_v1, spec_v1, types


@app.cell
def _(mo, pg, schema, spec_v1):
    # One call. ``init_sql`` validates the spec, then emits a single SQL
    # script — DDL + bindings + indexes + FKs + resolved views + trust
    # seed. With no query_fn it assumes an empty schema.
    pg.execute(spec_v1.init_sql(schema=schema))
    mo.md(f"Stage 1 deployed in **`{schema}`**.")
    return


@app.cell
def _(load, pg, schema, spec_v1):
    from knot.compile import ClassWrites

    # Ingest from imdb only. Pass row dicts straight through.
    movie_b = next(b for b in spec_v1.source_bindings if b.class_.name == "Movie")
    person_b = next(b for b in spec_v1.source_bindings if b.class_.name == "Person")

    bw = spec_v1.emit_batch_write(
        [
            ClassWrites(binding=person_b, rows=load("imdb", "persons")),
            ClassWrites(binding=movie_b, rows=load("imdb", "movies")),
        ],
        schema=schema,
        enforce=False,
    )
    with pg.cursor() as cur:
        for sql, params in bw.statements:
            cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {schema}.person_resolved")
        person_count = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {schema}.movie_resolved")
        movie_count = cur.fetchone()[0]
    f"Stage 1 ingest: {person_count} persons, {movie_count} movies (imdb only)."
    return (ClassWrites,)


@app.cell
def _(movie_v1, pg, schema, spec_v1):
    # Smoke test — five most recent movies + their directors.
    q = (
        movie_v1.order_by(movie_v1.col.year, "desc")
        .limit(5)
        .select(movie_v1.col.title, movie_v1.col.year, movie_v1.col.director.name)
    )
    sql, params = spec_v1.compile_query(q, schema=schema)
    with pg.cursor() as cur:
        cur.execute(sql, params or None)
        rows = [dict(zip([d.name for d in cur.description], r)) for r in cur.fetchall()]
    rows
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Stage 2 — add tmdb as a second source

    Same spec shape, plus a `tmdb` source binding. ``init_sql(query_fn=…)``
    introspects the live DB and emits only the delta — no `CREATE TABLE`
    churn, just a couple of trust-seed `INSERT`s for the new source. Then
    ingest tmdb's overlapping rows and watch the resolver pick winners
    per-slot.
    """)
    return


@app.cell
def _(Spec, movie_v1, person_v1, spec_v1, types):
    # Build the v2 spec — same shape + tmdb source. Done as a fresh Spec
    # rather than mutating spec_v1 so the two are inspectable side-by-side.
    spec_v2 = Spec(id="movies_play", version="0.2")

    person_v2 = spec_v2.add_class("Person")
    person_v2.slot("canonical_id", types.TEXT, identifier=True)
    person_v2.slot("name", types.TEXT, required=True)
    person_v2.slot("birth_country", types.TEXT)
    person_v2.slot("birth_year", types.INTEGER)

    movie_v2 = spec_v2.add_class("Movie")
    movie_v2.slot("canonical_id", types.TEXT, identifier=True)
    movie_v2.slot("title", types.TEXT, required=True)
    movie_v2.slot("year", types.INTEGER)
    movie_v2.slot("runtime_minutes", types.INTEGER)
    movie_v2.slot("director", person_v2)

    # imdb already deployed and seeded; tmdb is new.
    imdb_v2 = spec_v2.add_source("imdb")
    imdb_v2.bind(person_v2, base_trust=0.85)
    imdb_v2.bind(movie_v2, base_trust=0.85)

    tmdb_v2 = spec_v2.add_source("tmdb")
    tmdb_v2.bind(person_v2, base_trust=0.75)
    tmdb_v2.bind(movie_v2, base_trust=0.75)

    spec_v2.validate()
    return movie_v2, person_v2, spec_v2, tmdb_v2


@app.cell
def _(mo, pg, schema, spec_v2):
    # Diff against the live DB — only the trust-seed deltas for tmdb
    # should appear, plus idempotent CREATE OR REPLACE VIEW for the
    # resolved views. The DDL for tables/indexes is already in place.
    def query_fn(sql: str, params: tuple) -> list:
        with pg.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    delta_sql = spec_v2.init_sql(query_fn=query_fn, schema=schema)
    op_count = len([line for line in delta_sql.split(";\n\n") if line.strip()])
    pg.execute(delta_sql)
    mo.md(f"Stage 2 evolve: ran **{op_count}** delta ops.")
    return (query_fn,)


@app.cell
def _(ClassWrites, load, pg, schema, spec_v2):
    # Ingest tmdb's overlapping rows. Many of these are about the SAME
    # canonical Movies and Persons that imdb already wrote — but with
    # tmdb's own source_identifier and occasionally different field values.
    person_b = next(
        b
        for b in spec_v2.source_bindings
        if b.source.name == "tmdb" and b.class_.name == "Person"
    )
    movie_b = next(
        b
        for b in spec_v2.source_bindings
        if b.source.name == "tmdb" and b.class_.name == "Movie"
    )

    bw = spec_v2.emit_batch_write(
        [
            ClassWrites(binding=person_b, rows=load("tmdb", "persons")),
            ClassWrites(binding=movie_b, rows=load("tmdb", "movies")),
        ],
        schema=schema,
        enforce=False,
    )
    with pg.cursor() as cur:
        for sql, params in bw.statements:
            cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {schema}.movie_bindings")
        binding_count = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {schema}.movie_resolved")
        resolved_count = cur.fetchone()[0]
    (
        f"Stage 2 ingest: {binding_count} movie bindings rows "
        f"(imdb + tmdb), {resolved_count} resolved movies."
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### See trust at work

    Two sources, one of them more trusted (imdb 0.85 > tmdb 0.75). Where
    their values disagree, the per-slot resolver picks imdb's value
    automatically. The bindings table still preserves both.
    """)
    return


@app.cell
def _(pg, schema):
    # Pick movies where imdb and tmdb disagree on year.
    with pg.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              i.canonical_id,
              i.year AS imdb_year,
              t.year AS tmdb_year,
              r.year AS resolved_year
            FROM {schema}.movie_bindings i
            JOIN {schema}.movie_bindings t
              ON t.canonical_id = i.canonical_id
            JOIN {schema}.movie_resolved r
              ON r.canonical_id = i.canonical_id
            WHERE i.source_name = 'imdb' AND t.source_name = 'tmdb'
              AND i.year <> t.year
              AND i.valid_to IS NULL AND t.valid_to IS NULL
            ORDER BY i.canonical_id
            LIMIT 10
            """
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.cell
def _(mo):
    mo.md(r"""
    ### Operator tunes trust at runtime

    No spec edit, no redeploy. Just `UPDATE source_trust` and the resolver
    picks up the new value on its next query.
    """)
    return


@app.cell
def _(pg, schema):
    with pg.cursor() as cur:
        # Bump tmdb's trust on `year` above imdb's. The next query against
        # movie_resolved.year will return tmdb's value where they disagree.
        cur.execute(
            f"UPDATE {schema}.source_trust SET trust = 0.95 "
            f"WHERE source_name = 'tmdb' AND class_name = 'Movie' "
            f"AND slot_name = 'year'"
        )
    return


@app.cell
def _(pg, schema):
    # Same query as above — resolved_year flips to tmdb's where they disagreed.
    with pg.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              i.canonical_id,
              i.year AS imdb_year,
              t.year AS tmdb_year,
              r.year AS resolved_year
            FROM {schema}.movie_bindings i
            JOIN {schema}.movie_bindings t
              ON t.canonical_id = i.canonical_id
            JOIN {schema}.movie_resolved r
              ON r.canonical_id = i.canonical_id
            WHERE i.source_name = 'imdb' AND t.source_name = 'tmdb'
              AND i.year <> t.year
              AND i.valid_to IS NULL AND t.valid_to IS NULL
            ORDER BY i.canonical_id
            LIMIT 10
            """
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.cell
def _(mo):
    mo.md(r"""
    ## Stage 3 — evolve the spec: add Credit + rottentomatoes

    The spec gains a reified relation: ``Credit`` records (movie, person,
    role) triples. We add ``rottentomatoes`` as a third source.

    ``init_sql(query_fn=…)`` emits exactly what's missing: a new
    ``credit`` canonical table, a ``credit_bindings`` table, a
    ``credit_resolved`` view, FK constraints, indexes, and trust-seed
    rows for the new (rottentomatoes, *, *) and (*, Credit, *) triples.
    Existing data is untouched.
    """)
    return


@app.cell
def _(Spec, types):
    spec_v3 = Spec(id="movies_play", version="0.3")

    person_v3 = spec_v3.add_class("Person")
    person_v3.slot("canonical_id", types.TEXT, identifier=True)
    person_v3.slot("name", types.TEXT, required=True)
    person_v3.slot("birth_country", types.TEXT)
    person_v3.slot("birth_year", types.INTEGER)

    movie_v3 = spec_v3.add_class("Movie")
    movie_v3.slot("canonical_id", types.TEXT, identifier=True)
    movie_v3.slot("title", types.TEXT, required=True)
    movie_v3.slot("year", types.INTEGER)
    movie_v3.slot("runtime_minutes", types.INTEGER)
    movie_v3.slot("director", person_v3)

    credit_v3 = spec_v3.add_class("Credit", description="A person's role on a movie.")
    credit_v3.slot("canonical_id", types.TEXT, identifier=True)
    credit_v3.slot("role", types.TEXT, required=True)
    credit_v3.slot("movie", movie_v3)
    credit_v3.slot("person", person_v3)

    imdb_v3 = spec_v3.add_source("imdb")
    imdb_v3.bind(person_v3, base_trust=0.85)
    imdb_v3.bind(movie_v3, base_trust=0.85)
    imdb_v3.bind(credit_v3, base_trust=0.85)

    tmdb_v3 = spec_v3.add_source("tmdb")
    tmdb_v3.bind(person_v3, base_trust=0.75)
    tmdb_v3.bind(movie_v3, base_trust=0.75)
    tmdb_v3.bind(credit_v3, base_trust=0.75)

    rt_v3 = spec_v3.add_source("rottentomatoes")
    rt_v3.bind(person_v3, base_trust=0.70)
    rt_v3.bind(movie_v3, base_trust=0.70)
    rt_v3.bind(credit_v3, base_trust=0.70)

    spec_v3.validate()
    return credit_v3, movie_v3, person_v3, spec_v3


@app.cell
def _(mo, pg, query_fn, schema, spec_v3):
    delta_sql = spec_v3.init_sql(query_fn=query_fn, schema=schema)
    op_count = len([line for line in delta_sql.split(";\n\n") if line.strip()])
    pg.execute(delta_sql)
    mo.md(
        f"Stage 3 evolve: ran **{op_count}** delta ops (new Credit class + rottentomatoes source)."
    )
    return


@app.cell
def _(ClassWrites, load, pg, schema, spec_v3):
    # Ingest the remaining data — rottentomatoes for everything, plus
    # credits from all three sources.
    writes = []
    for source_name in ("imdb", "tmdb", "rottentomatoes"):
        # rottentomatoes is brand new — persons/movies from it.
        # imdb/tmdb persons/movies already ingested, but their credits
        # are new (Credit class wasn't in the spec until now).
        if source_name == "rottentomatoes":
            for entity in ("persons", "movies", "credits"):
                cls_name = entity[:-1].capitalize()
                b = next(
                    b
                    for b in spec_v3.source_bindings
                    if b.source.name == source_name and b.class_.name == cls_name
                )
                writes.append(ClassWrites(binding=b, rows=load(source_name, entity)))
        else:
            b = next(
                b
                for b in spec_v3.source_bindings
                if b.source.name == source_name and b.class_.name == "Credit"
            )
            writes.append(ClassWrites(binding=b, rows=load(source_name, "credits")))

    bw = spec_v3.emit_batch_write(writes, schema=schema, enforce=False)
    with pg.cursor() as cur:
        for sql, params in bw.statements:
            cur.execute(sql, params)

    with pg.cursor() as cur:
        counts = {}
        for cls in ("person", "movie", "credit"):
            cur.execute(f"SELECT count(*) FROM {schema}.{cls}_resolved")
            counts[cls] = cur.fetchone()[0]
    counts
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Stage 4 — queries against the final state

    The substrate at full strength: predicates, FK walks, correlated
    aggregates. Edit any cell, run it, inspect the SQL.
    """)
    return


@app.cell
def _(pg, schema, spec_v3):
    def qf(q):
        """Compile + run a knot Query; return list of dict rows."""
        sql, params = spec_v3.compile_query(q, schema=schema)
        with pg.cursor() as cur:
            cur.execute(sql, params or None)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def qsql(q):
        """Show the compiled SQL without executing."""
        s, _ = spec_v3.compile_query(q, schema=schema)
        return s

    return qf, qsql


@app.cell
def _(movie_v3, qf):
    # Q1 — 10 most recent movies + their directors (FK walk via .director.name)
    qf(
        movie_v3.order_by(movie_v3.col.year, "desc")
        .limit(10)
        .select(movie_v3.col.title, movie_v3.col.year, movie_v3.col.director.name)
    )
    return


@app.cell
def _(movie_v3, qf):
    # Q2 — Movies directed by someone born in the USA
    qf(
        movie_v3.where(movie_v3.col.director.birth_country == "USA")
        .order_by(movie_v3.col.year)
        .select(movie_v3.col.title, movie_v3.col.year, movie_v3.col.director.name)
    )
    return


@app.cell
def _(movie_v3, person_v3, qf):
    from knot import this

    # Q3 — Persons who have directed at least one movie
    qf(
        person_v3.where((movie_v3.col.director == this.Person).any())
        .order_by(person_v3.col.name)
        .select(person_v3.col.name, person_v3.col.birth_country)
    )
    return (this,)


@app.cell
def _(movie_v3, person_v3, qf, this):
    # Q4 — Directors with more than 3 movies in the dataset
    qf(
        person_v3.where((movie_v3.col.director == this.Person).count() > 3)
        .order_by(person_v3.col.name)
        .select(person_v3.col.name, person_v3.col.birth_country)
    )
    return


@app.cell
def _(movie_v3, person_v3, qf, this):
    # Q5 — People who appear in the dataset but never directed
    qf(
        person_v3.where((movie_v3.col.director == this.Person).none())
        .order_by(person_v3.col.name)
        .limit(15)
        .select(person_v3.col.name, person_v3.col.birth_country)
    )
    return


@app.cell
def _(mo):
    mo.md(r"### Inspect a compiled query")
    return


@app.cell
def _(movie_v3, qsql, this):
    print(
        qsql(
            movie_v3.where(movie_v3.col.director.birth_country == "Japan")
            .order_by(movie_v3.col.year)
            .select(movie_v3.col.title, movie_v3.col.director.name)
        )
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Cleanup

    Uncomment to drop the schema when you're done. Re-running the notebook
    from the top picks a fresh schema name.
    """)
    return


@app.cell
def _(pg, schema):
    # pg.execute(f"DROP SCHEMA {schema} CASCADE")
    # pg.close()
    f"To clean up: uncomment the lines above. Schema is `{schema}`."
    return


if __name__ == "__main__":
    app.run()
