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
    # knot — 04: ingest everything the migration enabled

    `03_migrate` extended the spec to **14 sources × 16 concrete
    classes → 56 source-bindings**. `02_ingest` covered just one
    (imdb → Movie). Now we feed the other 55.

    The ingest loop is data-driven: walk `spec.source_bindings`,
    map each one to its data file, run the same `write_sql()` pair
    we used in 02. Same shape a real Temporal workflow uses — one
    binding per worker, one feed per binding.

    Domain breakdown:

    | domain     | sources                                                   | classes                                                          |
    |------------|-----------------------------------------------------------|------------------------------------------------------------------|
    | movies     | imdb · tmdb · rottentomatoes                              | Movie · Person · MovieCredit                                     |
    | games      | igdb · giant_bomb · steam                                 | Studio · Platform · Game · Release · Person · GameCredit         |
    | podcasts   | apple_podcasts · spotify · listennotes                    | Podcast · PodcastEpisode · Person · PodcastCredit                |
    | tv         | tvdb (new) · tmdb · imdb (reused)                         | Show · Season · TVEpisode · TVCredit (+ Person on tvdb)          |
    | webscraped | reddit · letterboxd · fan_wiki · blog_review_aggregator   | Mention (low-trust; most fields preserved in raw_payload)        |
    """)
    return


@app.cell
def _():
    import pandas as pd
    from _demo import SCHEMA, connect

    return SCHEMA, connect, pd


@app.cell
def _():
    # Compose .full — registers every domain's classes + sources +
    # bindings on the shared spec object.
    import media_spec.full  # noqa: F401
    from media_spec import spec

    return (spec,)


@app.cell
def _(connect):
    pg, engine = connect()
    return engine, pg


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Build the ingest plan

    Two small lookups map (source, class) to a data-file path:

      - `DOMAIN_FOR_SOURCE` — which top-level folder each source lives in
      - `DATA_FILE_FOR_CLASS` — the filename the test fixture uses
        for each class (`MovieCredit` / `GameCredit` / `PodcastCredit`
        all alias to `credits.json` since each source dir scopes them)

    The full ingest table is just `(binding, path)` per
    `spec.source_bindings`, minus the `_user_corrections` source
    (knot-internal) and minus the imdb→Movie binding (already
    written in 02_ingest).
    """)
    return


@app.cell
def _(spec):
    # tmdb + imdb double-bind to both movies AND tv (sharing Person).
    # We pin "which domain to read from" per (source, class) — most
    # of the time it's just the source's primary domain.
    SOURCE_DEFAULT_DOMAIN = {
        "imdb": "movies",
        "tmdb": "movies",
        "rottentomatoes": "movies",
        "igdb": "games",
        "giant_bomb": "games",
        "steam": "games",
        "apple_podcasts": "podcasts",
        "spotify": "podcasts",
        "listennotes": "podcasts",
        "tvdb": "tv",
        "reddit_film_discussion": "webscraped",
        "letterboxd_user_reviews": "webscraped",
        "fan_wiki": "webscraped",
        "blog_review_aggregator": "webscraped",
    }
    # TV-domain class names that route imdb/tmdb to data/tv/ instead.
    TV_CLASSES = {"Show", "Season", "TVEpisode", "TVCredit"}

    DATA_FILE_FOR_CLASS = {
        "Movie": "movies.json",
        "Person": "persons.json",
        "MovieCredit": "credits.json",
        "Studio": "studios.json",
        "Platform": "platforms.json",
        "Game": "games.json",
        "Release": "releases.json",
        "GameCredit": "credits.json",
        "Podcast": "podcasts.json",
        "PodcastEpisode": "episodes.json",
        "PodcastCredit": "credits.json",
        "Show": "shows.json",
        "Season": "seasons.json",
        "TVEpisode": "episodes.json",
        "TVCredit": "credits.json",
        "Mention": "mentions.json",
    }

    def _domain_for(src_name: str, class_name: str) -> str:
        if class_name in TV_CLASSES:
            return "tv"
        return SOURCE_DEFAULT_DOMAIN[src_name]

    already_done = {("imdb", "Movie")}
    feeds = []
    for b in spec.source_bindings:
        if b.source.name not in SOURCE_DEFAULT_DOMAIN:
            continue  # skip _user_corrections etc.
        if (b.source.name, b.class_.name) in already_done:
            continue
        domain = _domain_for(b.source.name, b.class_.name)
        path = f"../data/{domain}/{b.source.name}/{DATA_FILE_FOR_CLASS[b.class_.name]}"
        feeds.append((b, path))

    print(f"{len(feeds)} ingest steps queued")
    return (feeds,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Run the ingest

    For each `(binding, path)` pair: read the JSON, render
    `binding.write_sql()`, run close-out + insert. Same shape per
    iteration; the binding object knows which table and which
    slot-mappings to use.
    """)
    return


@app.cell
def _(SCHEMA, feeds, pd, pg):
    # Read each feed with json.load (not pd.read_json) because pandas
    # promotes int columns with any NaN to float, and 2120.0 won't
    # cast back to ::integer in postgres. json.load preserves the
    # original literal types from the file.
    import json as _json
    from pathlib import Path as _Path

    summary = []
    for binding, path in feeds:
        rows = _json.loads(_Path(path).read_text())
        # webscraped Mentions reuse the same URL as source_identifier
        # across rows that point at different canonical entities (the
        # same wiki page mentions multiple movies, etc.). knot's
        # bindings PK is (source_name, source_identifier, valid_from)
        # — to keep each Mention row distinct without losing the URL
        # for traceability, suffix the URL with the canonical_id.
        # Real-world fix: negotiate stable per-mention IDs with the
        # scraper.
        if binding.class_.name == "Mention":
            for r in rows:
                r["source_identifier"] = f"{r['source_identifier']}#{r['canonical_id']}"
        payload = _json.dumps(rows)
        close_out, insert = binding.write_sql()
        with pg.cursor() as cur:
            cur.execute(close_out, {"rows": payload})
            cur.execute(insert, {"rows": payload})
        summary.append(
            {
                "source": binding.source.name,
                "class": binding.class_.name,
                "rows": len(rows),
            }
        )
    summary_df = pd.DataFrame(summary)
    print(f"ingested {summary_df['rows'].sum()} total rows across {len(summary)} feeds")
    summary_df
    return (summary_df,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    ## Per-class totals across the live bindings tables

    Pivots `summary` so you can see, per class, how many rows came
    from each source. Same data the resolver will fuse in 05.
    """)
    return


@app.cell
def _(summary_df):
    summary_df.pivot_table(
        index="class",
        columns="source",
        values="rows",
        aggfunc="sum",
        fill_value=0,
    )
    return


@app.cell
def _(SCHEMA, engine, pd, pg, spec):
    # Live counts straight from postgres — one row per concrete
    # class, showing total bindings and how many already have a
    # canonical_id (ER status going into 05).
    concrete = [
        c.name.lower()
        for c in spec.classes.values()
        if c.__class__.__name__ == "OntologyClass"
    ]
    union = " UNION ALL ".join(
        f"SELECT '{n}' AS class, COUNT(*) AS rows, "
        f"COUNT(canonical_id) AS resolved "
        f"FROM {SCHEMA}.{n}_bindings"
        for n in concrete
    )
    pd.read_sql_query(union + " ORDER BY class", engine)
    return


if __name__ == "__main__":
    app.run()
