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
       The argmax resolves per-slot winners by weight.
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
    return DATA, load, pg, psycopg, schema


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

    spec = Spec(id="movies_play", version="0.1")

    person = spec.add_class("Person")
    person.slot("canonical_id", types.TEXT, identifier=True)
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_country", types.TEXT)
    person.slot("birth_year", types.INTEGER)

    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)
    movie.slot("director", person)  # FK to Person

    # A constraint isn't a postgres CHECK — it's a rule the spec
    # compiles to a validation SELECT. The host runs it whenever it
    # wants (after a write, periodically, on-demand). The first
    # non-trivial domain rule on Movie: no film predates the Lumière
    # screenings of 1888.
    movie.add_constraint(
        "year_sane",
        body=movie.col.year >= 1888,
        message="Movie.year predates the invention of film.",
    )

    imdb = spec.add_source("imdb")
    imdb.bind(person).set_default_weight(0.85)
    imdb.bind(movie).set_default_weight(0.85)

    spec.validate()
    return Spec, imdb, movie, person, spec, types


@app.cell
def _(mo, pg, schema, spec):
    # One call. ``init_sql`` validates the spec, then emits a single SQL
    # script — DDL + bindings + indexes + FKs + resolved views + weight
    # seed. With no query_fn it assumes an empty schema.
    pg.execute(spec.init_sql(schema=schema))
    mo.md(f"Stage 1 deployed in **`{schema}`**.")
    return


@app.cell
def _(load, movie, person, pg, schema, spec):
    import json as _json

    # Ingest from imdb only. ``binding.write_sql()`` returns
    # ``(close_out_sql, insert_sql)`` — both reference ``%(rows)s::jsonb``.
    # The host binds the rows; knot never touches the row data.
    def write_to(binding, rows):
        close_out, insert = binding.write_sql(schema=schema)
        payload = _json.dumps(rows)
        with pg.cursor() as cur:
            cur.execute(close_out, {"rows": payload})
            cur.execute(insert, {"rows": payload})

    movie_b = next(b for b in spec.source_bindings if b.class_.name == "Movie")
    person_b = next(b for b in spec.source_bindings if b.class_.name == "Person")

    write_to(person_b, load("imdb", "persons"))
    write_to(movie_b, load("imdb", "movies"))

    # Sanity counts via knot Queries — no raw SQL.
    def _count(cls):
        sql, params = cls.select(cls.col.canonical_id).sql(schema=schema)
        with pg.cursor() as cur:
            cur.execute(sql, params or None)
            return len(cur.fetchall())

    f"Stage 1 ingest: {_count(person)} persons, {_count(movie)} movies (imdb only)."
    return (write_to,)


@app.cell
def _(movie, pg, schema, spec):
    # Smoke test — five most recent movies + their directors.
    q = (
        movie.order_by(movie.col.year, "desc")
        .limit(5)
        .select(movie.col.title, movie.col.year, movie.col.director.name)
    )
    sql, params = q.sql(schema=schema)
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
    churn, just a couple of weight-seed `INSERT`s for the new source. Then
    ingest tmdb's overlapping rows and watch the resolver pick winners
    per-slot.
    """)
    return


@app.cell
def _(movie, person, spec):
    # Evolve the SAME spec in-place. No duplication — just add tmdb as a
    # second source and bind it to the existing Movie + Person classes.
    # ``spec_at_stage2`` is the same object as ``spec`` under a new
    # name so marimo cells downstream can declare a dependency on the
    # post-evolution state. (Marimo doesn't track in-place mutation;
    # the rename makes the ordering explicit.)
    tmdb = spec.add_source("tmdb")
    tmdb.bind(person).set_default_weight(0.75)
    tmdb.bind(movie).set_default_weight(0.75)
    spec.validate()
    spec_at_stage2 = spec
    return spec_at_stage2, tmdb


@app.cell
def _(mo, pg, schema, spec_at_stage2):
    # Diff against the live DB — only the weight-seed deltas for tmdb
    # should appear, plus idempotent CREATE OR REPLACE VIEW for the
    # resolved views. The DDL for tables/indexes is already in place.
    def query_fn(sql: str, params: tuple) -> list:
        with pg.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    delta_sql = spec_at_stage2.init_sql(query_fn=query_fn, schema=schema)
    op_count = len([line for line in delta_sql.split(";\n\n") if line.strip()])
    pg.execute(delta_sql)
    mo.md(f"Stage 2 evolve: ran **{op_count}** delta ops.")
    return (query_fn,)


@app.cell
def _(load, movie, pg, schema, spec_at_stage2, write_to):
    # Ingest tmdb's overlapping rows. Many of these are about the SAME
    # canonical Movies and Persons that imdb already wrote — but with
    # tmdb's own source_identifier and occasionally different field values.
    tmdb_person_b = next(
        b
        for b in spec_at_stage2.source_bindings
        if b.source.name == "tmdb" and b.class_.name == "Person"
    )
    tmdb_movie_b = next(
        b
        for b in spec_at_stage2.source_bindings
        if b.source.name == "tmdb" and b.class_.name == "Movie"
    )

    write_to(tmdb_person_b, load("tmdb", "persons"))
    write_to(tmdb_movie_b, load("tmdb", "movies"))

    sql, params = movie.select(movie.col.canonical_id).sql(schema=schema)
    with pg.cursor() as cur:
        cur.execute(sql, params or None)
        resolved_count = len(cur.fetchall())
    f"Stage 2 ingest done. {resolved_count} resolved movies (imdb + tmdb merged)."
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### See the weights at work

    Two sources, one weighted higher (imdb 0.85 > tmdb 0.75). Where
    their values disagree, the per-slot resolver picks imdb's value
    automatically. The bindings table still preserves both. Weights
    are opaque floats — calibration is up to whatever produced them.
    """)
    return


@app.cell
def _(movie, pg, schema):
    # First 5 movies + their resolver-picked year — imdb wins at default
    # weights (imdb 0.85 > tmdb 0.75).
    q = movie.limit(5).select(movie.col.canonical_id, movie.col.title, movie.col.year)
    sql, params = q.sql(schema=schema)
    with pg.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.cell
def _(mo):
    mo.md(r"""
    ### Operator tunes weights at runtime

    No spec edit, no redeploy. Just `UPDATE source_weight` (runtime
    config table, not knot's compiled output) and the resolver picks
    up the new value on the next query.
    """)
    return


@app.cell
def _(pg, schema):
    # Bump tmdb's weight on `year` above imdb's. This UPDATE targets
    # knot's runtime config table — the resolver argmax reads from it
    # at query time, so no recompile.
    with pg.cursor() as cur:
        cur.execute(
            f"UPDATE {schema}.source_weight SET weight = 0.95 "
            f"WHERE source_name = 'tmdb' AND class_name = 'Movie' "
            f"AND slot_name = 'year'"
        )
    return


@app.cell
def _(movie, pg, schema):
    # Same Query re-run — wherever imdb and tmdb disagreed on year,
    # the resolver's argmax now picks tmdb's value (weight 0.95 > 0.85).
    q = movie.limit(5).select(movie.col.canonical_id, movie.col.title, movie.col.year)
    sql, params = q.sql(schema=schema)
    with pg.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.cell
def _(mo):
    mo.md(r"""
    ### Provenance — every source's claim, side-by-side

    The resolved view picks one winner per slot; ``movie_all_sources``
    keeps the rest of the receipts. Same shape, one row per
    canonical_id, but each slot column is a jsonb keyed by
    source_name with ``{value, weight}`` payload. Audit UIs and
    GraphQL ``SlotValue`` projections read from this view; the
    resolved view stays cheap and skinny.
    """)
    return


@app.cell
def _(movie, pg, schema):
    from dataclasses import replace

    # Provenance lives in <class>_all_sources — same shape as the
    # resolved view but with jsonb-per-slot. Same Query AST, just
    # retargeted to the all_sources view via Query.target_suffix.
    q = movie.limit(5).select(
        movie.col.canonical_id, movie.col.year, movie.col.runtime_minutes
    )
    q = replace(q, target_suffix="_all_sources")
    sql, params = q.sql(schema=schema)
    with pg.cursor() as cur:
        cur.execute(sql, params or None)
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.cell
def _(mo):
    mo.md(r"""
    ### SCD2 history — see a closed-out row

    Sources update; the bindings table never overwrites. A re-ingest
    with a new value for the same ``(source_name, source_identifier)``
    closes the previous row (``valid_to`` set to ``now()``) and inserts
    a new one. Below: pick a movie, "correct" imdb's runtime, query
    the bindings to see both rows for that source_identifier.
    """)
    return


@app.cell
def _(pg, schema, spec_at_stage2, write_to):
    correction_b = next(
        b
        for b in spec_at_stage2.source_bindings
        if b.source.name == "imdb" and b.class_.name == "Movie"
    )
    # Re-ingest one imdb row with a deliberately-different runtime.
    write_to(
        correction_b,
        [
            {
                "canonical_id": "m_killbill1",
                "source_identifier": "tt2878306",
                "title": "Kill Bill: Vol. 1",
                "year": 2003,
                "runtime_minutes": 111,  # was 112 in the original ingest
                "director": "p_tarantino",
            }
        ],
    )

    # Direct introspection of knot's internal SCD2 layer to show the
    # close-out + insert pair. Query AST targets _resolved; the raw
    # _bindings table is plumbing, not a user-facing surface.
    with pg.cursor() as cur:
        cur.execute(
            f"""
            SELECT source_identifier, runtime_minutes,
                   valid_from, valid_to
            FROM {schema}.movie_bindings
            WHERE source_name = 'imdb'
              AND source_identifier = 'tt2878306'
            ORDER BY valid_from
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
    ``credit_resolved`` view, FK constraints, indexes, and weight-seed
    rows for the new (rottentomatoes, *, *) and (*, Credit, *) triples.
    Existing data is untouched.
    """)
    return


@app.cell
def _(movie, person, spec_at_stage2, types):
    # Evolve in-place again. Add the Credit reified relation, a
    # DirectedMovie virtual class derived from it, two more
    # constraints, and the rottentomatoes source with bindings for
    # all three classes. tmdb (from stage 2) also picks up a new
    # binding for Credit since the class didn't exist before.
    credit = spec_at_stage2.add_class(
        "Credit", description="A person's role on a movie."
    )
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)

    # Virtual class — derived membership, no table of its own. A movie
    # IS a DirectedMovie iff some Credit row exists with role='director'
    # pointing at it. The view sits on top of movie_resolved + the
    # has_any predicate, so it stays current with whatever the resolver
    # currently believes about each movie.
    directed_movie = movie.add_virtual(
        "DirectedMovie",
        where=movie.has_any(credit, role="director"),
    )

    # Two more constraints on top of stage 1's year_sane:

    # Enum-without-DDL. ``role`` is text in the schema; the vocabulary
    # lives in the constraint so it can be tuned without a schema
    # migration.
    credit.add_constraint(
        "role_in_vocabulary",
        body=credit.col.role.in_(
            ["director", "writer", "actor", "producer", "composer"]
        ),
        message="Credit.role outside the curated vocabulary.",
    )

    # Cross-class existence — every Movie must have at least one Credit
    # with role='director'. Compiles to a correlated EXISTS subquery
    # against credit_resolved. The kind of rule a postgres CHECK can't
    # express; it requires looking at another table.
    movie.add_constraint(
        "must_have_director",
        body=movie.has_any(credit, role="director"),
        message="Movie has no Credit with role='director'.",
    )

    # Cross-class aggregate — bound the cast size. Compiles to a
    # correlated subquery with COUNT(*). Demonstrates that constraints
    # can read scalar-aggregated state of related rows, not just
    # existence.
    from knot import Severity

    movie.add_constraint(
        "credit_count_sane",
        body=movie.has_count(credit) <= 50,
        message="Movie has more than 50 Credit rows — likely data quality issue.",
    )

    # Warning severity — informational, not blocking. The host can
    # treat severity=WARNING as "log but don't reject" and split
    # those rows into a separate review queue. Stage 1's `year_sane`,
    # `role_in_vocabulary`, `must_have_director` are all ERROR by
    # default; this one flags pre-1900 silent-era films for
    # operator attention without rejecting them.
    movie.add_constraint(
        "silent_era_flag",
        body=movie.col.year >= 1900,
        severity=Severity.WARNING,
        message="Pre-1900 film — flagged for review (not blocking).",
    )

    # New source — rottentomatoes — for all three classes.
    rt = spec_at_stage2.add_source("rottentomatoes")
    rt.bind(person).set_default_weight(0.70)
    rt.bind(movie).set_default_weight(0.70)
    rt.bind(credit).set_default_weight(0.70)

    # Existing sources need bindings for the new Credit class.
    _imdb = next(s for s in spec_at_stage2.sources if s.name == "imdb")
    _tmdb = next(s for s in spec_at_stage2.sources if s.name == "tmdb")
    _imdb.bind(credit).set_default_weight(0.85)
    _tmdb.bind(credit).set_default_weight(0.75)

    spec_at_stage2.validate()
    spec_at_stage3 = spec_at_stage2  # same object, post-stage-3 marker
    return credit, directed_movie, spec_at_stage3


@app.cell
def _(mo, pg, query_fn, schema, spec_at_stage3):
    delta_sql = spec_at_stage3.init_sql(query_fn=query_fn, schema=schema)
    op_count = len([line for line in delta_sql.split(";\n\n") if line.strip()])
    pg.execute(delta_sql)
    mo.md(
        f"Stage 3 evolve: ran **{op_count}** delta ops (new Credit class + rottentomatoes source)."
    )
    return


@app.cell
def _(credit, load, movie, person, pg, schema, spec_at_stage3, write_to):
    # Ingest the remaining data — rottentomatoes for everything, plus
    # credits from all three sources. Per binding; one binding.write_sql()
    # call per source × class.
    for source_name in ("imdb", "tmdb", "rottentomatoes"):
        # rottentomatoes is brand new — persons/movies from it.
        # imdb/tmdb persons/movies already ingested, but their credits
        # are new (Credit class wasn't in the spec until now).
        if source_name == "rottentomatoes":
            for entity in ("persons", "movies", "credits"):
                cls_name = entity[:-1].capitalize()
                b = next(
                    b
                    for b in spec_at_stage3.source_bindings
                    if b.source.name == source_name and b.class_.name == cls_name
                )
                write_to(b, load(source_name, entity))
        else:
            b = next(
                b
                for b in spec_at_stage3.source_bindings
                if b.source.name == source_name and b.class_.name == "Credit"
            )
            write_to(b, load(source_name, "credits"))

    # Counts via knot Queries — one Query per class.
    counts = {}
    for cls in (person, movie, credit):
        sql, params = cls.select(cls.col.canonical_id).sql(schema=schema)
        with pg.cursor() as cur:
            cur.execute(sql, params or None)
            counts[cls.name.lower()] = len(cur.fetchall())
    counts
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### Constraints — validation SELECTs against the resolved view

    Each constraint compiles to one SELECT that returns zero rows when
    the rule holds and one row per violating canonical_id otherwise.
    knot emits the SQL; **the host decides when to run it**. Three
    canonical placements:

    1. **In-transaction with each ingest** — write + validation in one
       `pg.transaction()`; raise on rows, the txn rolls back. Atomic
       enforcement at write time (the next stage demos this).
    2. **Scheduled sweep** — a Temporal cron / k8s CronJob runs all
       validations periodically against the live resolved views;
       violations open tickets / page oncall.
    3. **On-demand from an audit UI** — an operator opens a "data
       quality" page that runs validations and surfaces offending
       canonical_ids.

    Same SQL, three lifecycles. knot doesn't pick; the host does.
    """)
    return


@app.cell
def _(pg, schema, spec_at_stage3):
    # Run every constraint's SELECT and show count + severity.
    # emit_validation -> [(rule_id, sql), ...]; we cross-reference with
    # spec.constraints to surface severity (ERROR blocks, WARNING flags).
    severity_by_name = {c.name: c.severity.value for c in spec_at_stage3.constraints}
    report = {}
    with pg.cursor() as cur:
        for rule, sql in spec_at_stage3.emit_validation(schema=schema):
            cur.execute(sql)
            rows = cur.fetchall()
            report[rule] = {"severity": severity_by_name[rule], "violations": len(rows)}
    report


@app.cell
def _(mo):
    mo.md(r"""
    ### Reject bad writes by host-side enforcement

    knot doesn't bundle constraint checks into the write SQL — the host
    wraps `binding.write_sql()` + `spec.emit_validation()` in one
    transaction and rolls back if any violation row appears. Below:
    try to ingest a fake "Le Voyage dans la Lune from 1850" — predates
    film, so `year_sane` fires and nothing is written.
    """)
    return


@app.cell
def _(movie, pg, schema, spec_at_stage3):
    import json as _json

    imdb_movie_b = next(
        b
        for b in spec_at_stage3.source_bindings
        if b.source.name == "imdb" and b.class_.name == "Movie"
    )
    bad_batch = [
        {
            "canonical_id": "m_anachronism",
            "source_identifier": "tt-fake-0001",
            "title": "Le Voyage dans la Lune (anachronism)",
            "year": 1850,
            "runtime_minutes": 14,
            "director": "p_melies",
        }
    ]
    close_out, insert = imdb_movie_b.write_sql(schema=schema)
    payload = _json.dumps(bad_batch)
    # Host-side enforcement filters by severity — only ERROR rules
    # block the write; WARNING rules are run separately as
    # informational (this cell ignores them).
    severity_by_name = {c.name: c.severity.value for c in spec_at_stage3.constraints}
    error = None
    try:
        with pg.transaction(), pg.cursor() as cur:
            cur.execute(close_out, {"rows": payload})
            cur.execute(insert, {"rows": payload})
            for rule, vsql in spec_at_stage3.emit_validation(schema=schema):
                if severity_by_name[rule] != "error":
                    continue
                cur.execute(vsql)
                bad = cur.fetchall()
                if bad:
                    raise RuntimeError(f"constraint {rule!r} violated: {bad}")
    except RuntimeError as e:
        error = str(e).splitlines()[0]

    # Confirm nothing landed despite the attempt. Query the resolved
    # view (not the bindings table) — if the row leaked through, it'd
    # be visible here.
    check_q = movie.where(movie.col.canonical_id == "m_anachronism").select(
        movie.col.canonical_id
    )
    check_sql, check_params = check_q.sql(schema=schema)
    with pg.cursor() as cur:
        cur.execute(check_sql, check_params or None)
        leaked = len(cur.fetchall())
    {"raised": error, "leaked_into_resolved": leaked}


@app.cell
def _(mo):
    mo.md(r"""
    ### Virtual class — DirectedMovie

    A ``VirtualClass`` is a view derived from its base class plus a
    membership predicate. ``DirectedMovie = Movie WHERE
    movie.has_any(credit, role='director')``. ``init_sql`` materialized
    it as ``directedmovie`` during Stage 3. The view is read-only and
    always reflects the current state of the resolved layer.
    """)
    return


@app.cell
def _(pg, schema):
    # VirtualClass is materialized as ``<schema>.<lowername>`` (no
    # _resolved suffix — virtual views layer on top of the resolved
    # base). Query AST doesn't have a VirtualClass builder yet; the
    # view is a knot artifact so a direct SELECT against it is the
    # current way to read it.
    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, title, year FROM {schema}.directedmovie "
            f"ORDER BY year DESC LIMIT 10"
        )
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


@app.cell
def _(mo):
    mo.md(r"""
    ## Stage 4 — queries against the final state

    The substrate at full strength: predicates, FK walks, correlated
    aggregates. Edit any cell, run it, inspect the SQL.
    """)
    return


@app.cell
def _(pg, schema):
    def qf(q):
        """Compile + run a knot Query; return list of dict rows."""
        sql, params = q.sql(schema=schema)
        with pg.cursor() as cur:
            cur.execute(sql, params or None)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def qsql(q):
        """Show the compiled SQL without executing."""
        s, _ = q.sql(schema=schema)
        return s

    return qf, qsql


@app.cell
def _(movie, qf):
    # Q1 — 10 most recent movies + their directors (FK walk via .director.name)
    qf(
        movie.order_by(movie.col.year, "desc")
        .limit(10)
        .select(movie.col.title, movie.col.year, movie.col.director.name)
    )
    return


@app.cell
def _(movie, qf):
    # Q2 — Movies directed by someone born in the USA
    qf(
        movie.where(movie.col.director.birth_country == "USA")
        .order_by(movie.col.year)
        .select(movie.col.title, movie.col.year, movie.col.director.name)
    )
    return


@app.cell
def _(movie, person, qf):
    from knot import this

    # Q3 — Persons who have directed at least one movie
    qf(
        person.where((movie.col.director == this.Person).any())
        .order_by(person.col.name)
        .select(person.col.name, person.col.birth_country)
    )
    return (this,)


@app.cell
def _(movie, person, qf, this):
    # Q4 — Directors with more than 3 movies in the dataset
    qf(
        person.where((movie.col.director == this.Person).count() > 3)
        .order_by(person.col.name)
        .select(person.col.name, person.col.birth_country)
    )
    return


@app.cell
def _(movie, person, qf, this):
    # Q5 — People who appear in the dataset but never directed
    qf(
        person.where((movie.col.director == this.Person).none())
        .order_by(person.col.name)
        .limit(15)
        .select(person.col.name, person.col.birth_country)
    )
    return


@app.cell
def _(mo):
    mo.md(r"### Inspect a compiled query")
    return


@app.cell
def _(movie, qsql, this):
    print(
        qsql(
            movie.where(movie.col.director.birth_country == "Japan")
            .order_by(movie.col.year)
            .select(movie.col.title, movie.col.director.name)
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
