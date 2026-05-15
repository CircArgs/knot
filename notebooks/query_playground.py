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
    # knot — query playground

    End-to-end walkthrough:

    1. Build a `Spec` (Movie / Person / Credit with FK slots)
    2. Compile DDL + deploy to a throwaway schema in the live postgres on `:5433`
    3. Ingest sample data through `emit_batch_write`
    4. **Play with queries** using the read substrate

    Restart the kernel to start fresh — each run gets a unique schema and
    drops it on cleanup.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 1. Spec
    """)
    return


@app.cell
def _():
    from knot import Spec, this, types

    spec = Spec(id="movies_play", version="0.1")

    person = spec.add_class("Person", description="A real human.")
    person.slot("canonical_id", types.TEXT, identifier=True)
    person.slot("name", types.TEXT, required=True)
    person.slot("birth_country", types.TEXT)

    movie = spec.add_class("Movie", description="A theatrical release.")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)

    credit = spec.add_class("Credit", description="A person's role in a movie.")
    credit.slot("canonical_id", types.TEXT, identifier=True)
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    credit.slot("person", person)

    # Single source for simplicity. Multi-source resolution + trust
    # arbitration still happens — there's just one contributor here.
    imdb = spec.add_source("imdb", description="IMDb canonical.")
    spec.bind(imdb, person, identifier=person["canonical_id"], accuracy=0.95)
    spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.95)
    spec.bind(imdb, credit, identifier=credit["canonical_id"], accuracy=0.95)

    errs = spec.validate()
    assert not errs, errs
    return credit, movie, person, spec, this


@app.cell
def _(mo, spec):
    mo.md(f"""
    **Classes:** {", ".join(c.name for c in spec.classes)}

    **Source bindings:** {len(spec.source_bindings)} — {", ".join(f"{b.source.name}→{b.class_.name}" for b in spec.source_bindings)}
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 2. Deploy DDL to postgres
    """)
    return


@app.cell
def _():
    import uuid

    import psycopg

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
    return pg, schema


@app.cell
def _(mo, pg, schema, spec):
    from knot.compile import emit_ddl, emit_trust_seed

    # DDL — canonical + bindings tables, indexes, FKs, resolved views.
    with pg.cursor() as cur:
        for stmt in emit_ddl(spec, schema=schema):
            cur.execute(stmt)

    # Trust seed — upsert per-(source, class) accuracy into source_accuracy.
    with pg.cursor() as cur:
        for sql, params in emit_trust_seed(spec, schema=schema):
            cur.execute(sql, params)

    mo.md(f"Schema **`{schema}`** deployed.")
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 3. Ingest sample data
    """)
    return


@app.cell
def _():
    person_rows = [
        {
            "source_identifier": "nm_tarantino",
            "canonical_id": "p_tarantino",
            "name": "Quentin Tarantino",
            "birth_country": "USA",
        },
        {
            "source_identifier": "nm_kurosawa",
            "canonical_id": "p_kurosawa",
            "name": "Akira Kurosawa",
            "birth_country": "Japan",
        },
        {
            "source_identifier": "nm_miyazaki",
            "canonical_id": "p_miyazaki",
            "name": "Hayao Miyazaki",
            "birth_country": "Japan",
        },
        {
            "source_identifier": "nm_scorsese",
            "canonical_id": "p_scorsese",
            "name": "Martin Scorsese",
            "birth_country": "USA",
        },
        {
            "source_identifier": "nm_ozu",
            "canonical_id": "p_ozu",
            "name": "Yasujirō Ozu",
            "birth_country": "Japan",
        },
        {
            "source_identifier": "nm_thurman",
            "canonical_id": "p_thurman",
            "name": "Uma Thurman",
            "birth_country": "USA",
        },
        {
            "source_identifier": "nm_mifune",
            "canonical_id": "p_mifune",
            "name": "Toshirō Mifune",
            "birth_country": "Japan",
        },
    ]

    movie_rows = [
        {
            "source_identifier": "tt_pulpfiction",
            "canonical_id": "m_pulpfiction",
            "title": "Pulp Fiction",
            "year": 1994,
            "director": "p_tarantino",
        },
        {
            "source_identifier": "tt_killbill1",
            "canonical_id": "m_killbill1",
            "title": "Kill Bill: Vol. 1",
            "year": 2003,
            "director": "p_tarantino",
        },
        {
            "source_identifier": "tt_oncetime",
            "canonical_id": "m_oncetime",
            "title": "Once Upon a Time in Hollywood",
            "year": 2019,
            "director": "p_tarantino",
        },
        {
            "source_identifier": "tt_django",
            "canonical_id": "m_djangounchained",
            "title": "Django Unchained",
            "year": 2012,
            "director": "p_tarantino",
        },
        {
            "source_identifier": "tt_inglourious",
            "canonical_id": "m_inglourious",
            "title": "Inglourious Basterds",
            "year": 2009,
            "director": "p_tarantino",
        },
        {
            "source_identifier": "tt_reservoir",
            "canonical_id": "m_reservoirdogs",
            "title": "Reservoir Dogs",
            "year": 1992,
            "director": "p_tarantino",
        },
        {
            "source_identifier": "tt_7samurai",
            "canonical_id": "m_sevensamurai",
            "title": "Seven Samurai",
            "year": 1954,
            "director": "p_kurosawa",
        },
        {
            "source_identifier": "tt_rashomon",
            "canonical_id": "m_rashomon",
            "title": "Rashomon",
            "year": 1950,
            "director": "p_kurosawa",
        },
        {
            "source_identifier": "tt_yojimbo",
            "canonical_id": "m_yojimbo",
            "title": "Yojimbo",
            "year": 1961,
            "director": "p_kurosawa",
        },
        {
            "source_identifier": "tt_spirited",
            "canonical_id": "m_spiritedaway",
            "title": "Spirited Away",
            "year": 2001,
            "director": "p_miyazaki",
        },
        {
            "source_identifier": "tt_totoro",
            "canonical_id": "m_totoro",
            "title": "My Neighbor Totoro",
            "year": 1988,
            "director": "p_miyazaki",
        },
        {
            "source_identifier": "tt_tokyo",
            "canonical_id": "m_tokyostory",
            "title": "Tokyo Story",
            "year": 1953,
            "director": "p_ozu",
        },
        {
            "source_identifier": "tt_taxi",
            "canonical_id": "m_taxidriver",
            "title": "Taxi Driver",
            "year": 1976,
            "director": "p_scorsese",
        },
        {
            "source_identifier": "tt_goodfellas",
            "canonical_id": "m_goodfellas",
            "title": "Goodfellas",
            "year": 1990,
            "director": "p_scorsese",
        },
    ]

    credit_rows = [
        {
            "source_identifier": "c1",
            "canonical_id": "c1",
            "role": "actor",
            "movie": "m_pulpfiction",
            "person": "p_thurman",
        },
        {
            "source_identifier": "c2",
            "canonical_id": "c2",
            "role": "actor",
            "movie": "m_killbill1",
            "person": "p_thurman",
        },
        {
            "source_identifier": "c3",
            "canonical_id": "c3",
            "role": "actor",
            "movie": "m_sevensamurai",
            "person": "p_mifune",
        },
        {
            "source_identifier": "c4",
            "canonical_id": "c4",
            "role": "actor",
            "movie": "m_yojimbo",
            "person": "p_mifune",
        },
        {
            "source_identifier": "c5",
            "canonical_id": "c5",
            "role": "actor",
            "movie": "m_rashomon",
            "person": "p_mifune",
        },
    ]
    return credit_rows, movie_rows, person_rows


@app.cell
def _(
    credit,
    credit_rows,
    movie,
    movie_rows,
    person,
    person_rows,
    pg,
    schema,
    spec,
):
    from knot.compile import emit_batch_write
    from knot.compile.data_io import ClassWrites

    imdb_person_b = next(
        b for b in spec.source_bindings if b.source.name == "imdb" and b.class_ is person
    )
    imdb_movie_b = next(
        b for b in spec.source_bindings if b.source.name == "imdb" and b.class_ is movie
    )
    imdb_credit_b = next(
        b for b in spec.source_bindings if b.source.name == "imdb" and b.class_ is credit
    )

    bw = emit_batch_write(
        spec,
        [
            ClassWrites(binding=imdb_person_b, rows=person_rows, use_mappings=False),
            ClassWrites(binding=imdb_movie_b, rows=movie_rows, use_mappings=False),
            ClassWrites(binding=imdb_credit_b, rows=credit_rows, use_mappings=False),
        ],
        schema=schema,
        enforce=False,
    )

    # emit_batch_write returns a multi-statement transactional script;
    # psycopg's prepared-statement path rejects multi-statement with
    # params, so split on the blank-line separator knot uses between
    # statements and execute each with the same params dict.
    with pg.cursor() as cur:
        for stmt in bw.sql.split("\n\n"):
            s = stmt.strip()
            if s:
                cur.execute(s, bw.params)

    counts = {}
    with pg.cursor() as cur:
        for c in (person, movie, credit):
            cur.execute(f"SELECT COUNT(*) FROM {schema}.{c.name.lower()}_resolved")
            counts[c.name] = cur.fetchone()[0]
    counts
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 4. Query playground

    The `qf(q)` helper compiles a knot `Query` to SQL and runs it against
    the deployed schema. Returns rows as a list of dicts. `qsql(q)` returns
    the compiled SQL without running it — handy for understanding the
    compilation.

    Substrate cheatsheet:

    - `Class.where(predicate)` — filter
    - `Class.col.<slot>` — slot ref; for FKs, navigable: `Movie.col.director.name`
    - `Class.col.<fk> == this.OtherClass` — outer-scope correlation
    - `predicate.any()` / `.none()` / `.count()` / `.all(cond)` — set quantifiers
    - `Class.select(*refs).order_by(ref, "desc").limit(N).offset(M)` — terminal shape
    """)
    return


@app.cell
def _(pg, schema, spec):
    from knot.compile import compile_query

    def qf(q):
        """Compile + run a knot Query; return list of dict rows."""
        sql, params = compile_query(q, spec=spec, schema=schema)
        with pg.cursor() as cur:
            cur.execute(sql, params or None)
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def qsql(q):
        """Just show the compiled SQL without executing."""
        s, _ = compile_query(q, spec=spec, schema=schema)
        return s

    return qf, qsql


@app.cell
def _(mo):
    mo.md(r"""
    ### Example queries (edit cells and re-run)
    """)
    return


@app.cell
def _(movie, qf):
    # Q1 — 5 most recent movies
    qf(movie.order_by(movie.col.year, "desc").limit(5).select(movie.col.title, movie.col.year))
    return


@app.cell
def _(movie, qf):
    # Q2 — 1990s movies with their directors
    qf(
        movie.where(movie.col.year >= 1990)
        .where(movie.col.year < 2000)
        .order_by(movie.col.year)
        .select(movie.col.title, movie.col.year, movie.col.director.name)
    )
    return


@app.cell
def _(movie, person, qf, this):
    # Q3 — Persons who have directed at least one movie
    qf(
        person.where((movie.col.director == this.Person).any()).select(
            person.col.name, person.col.birth_country
        )
    )
    return


@app.cell
def _(movie, person, qf, this):
    # Q4 — Directors with more than 2 movies
    qf(
        person.where((movie.col.director == this.Person).count() > 2).select(
            person.col.name, person.col.birth_country
        )
    )
    return


@app.cell
def _(movie, person, qf, this):
    # Q5 — People who have NEVER directed a movie
    qf(
        person.where((movie.col.director == this.Person).none()).select(
            person.col.name, person.col.birth_country
        )
    )
    return


@app.cell
def _(movie, qf):
    # Q6 — Movies whose director was born in Japan
    qf(
        movie.where(movie.col.director.birth_country == "Japan")
        .order_by(movie.col.year)
        .select(movie.col.title, movie.col.year, movie.col.director.name)
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ### Inspect the compiled SQL
    """)
    return


@app.cell
def _(movie, qsql):
    # Print the SQL for any query without running it.
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
    ## 5. Cleanup (run this when you're done)

    Drops the playground schema and closes the connection. Re-run the
    notebook from cell 1 to start fresh.
    """)
    return


@app.cell
def _(schema):
    # pg.execute(f"DROP SCHEMA {schema} CASCADE")
    # pg.close()
    f"To clean up: uncomment the lines above and re-run. Schema is `{schema}`."
    return


if __name__ == "__main__":
    app.run()
