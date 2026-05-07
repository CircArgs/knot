import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo
    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        # knot — end-to-end

        From an empty database to multi-source disagreement, user
        corrections, and **bandit-learned trust** — driven entirely
        through the API.
        """
    )
    return


@app.cell(hide_code=True)
def _():
    import json
    import requests
    import sqlalchemy

    API = "http://localhost:8000"
    control_db = sqlalchemy.create_engine(
        "postgresql+psycopg://knot:knot@localhost:5432/knot_control"
    )

    def post(path, body=None):
        r = requests.post(f"{API}{path}", json=body or {})
        r.raise_for_status()
        return r.json()

    def get(path):
        r = requests.get(f"{API}{path}")
        r.raise_for_status()
        return r.json()

    def put(path, body):
        r = requests.put(f"{API}{path}", json=body)
        r.raise_for_status()
        return r.json()

    return API, control_db, get, json, post, put


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 1 — Clean slate

        Truncate spec_revisions, drop the data-plane schema, drop trust
        + correction state. Idempotent schema bootstrap recreates the
        control plane.
        """
    )
    return


@app.cell
def _(control_db, mo):
    _ = mo.sql(
        """
        TRUNCATE spec_revisions RESTART IDENTITY CASCADE;
        DROP SCHEMA IF EXISTS knot_data CASCADE;
        DROP TABLE IF EXISTS trust_config;
        DROP TABLE IF EXISTS trust_posteriors;
        DROP TABLE IF EXISTS _user_corrections;
        """,
        engine=control_db,
    )
    return


@app.cell(hide_code=True)
def _():
    from knot import db
    db.apply_schema()
    "control plane bootstrapped"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 2 — Author the ontology

        Spec authoring is API-driven. Every change goes through a draft
        → publish lifecycle so the spec graph is validated before it
        ships. We're modelling **Movie** with three slots: an
        identifier, a title, and a year.

        The interesting bit: `year` is wired to **THOMPSON_SAMPLING** as
        its resolution policy. Disagreement on `year` will be resolved
        by sampling each source's per-slot Beta posterior.
        """
    )
    return


@app.cell
def _(post):
    draft_id = post("/spec/drafts", {"label": "demo"})["revision"]
    post(f"/spec/drafts/{draft_id}/types", {"name": "string", "base": "str"})
    post(f"/spec/drafts/{draft_id}/types", {"name": "integer", "base": "int"})
    return (draft_id,)


@app.cell
def _(draft_id, post):
    post(
        f"/spec/drafts/{draft_id}/slots",
        {"name": "imdb_id", "range_kind": "type", "range_name": "string",
         "identifier": True, "required": True},
    )
    post(
        f"/spec/drafts/{draft_id}/slots",
        {"name": "title", "range_kind": "type", "range_name": "string",
         "required": True},
    )
    post(
        f"/spec/drafts/{draft_id}/slots",
        {"name": "year", "range_kind": "type", "range_name": "integer",
         "resolution_policy": "thompson_sampling"},
    )
    return


@app.cell
def _(draft_id, post):
    post(
        f"/spec/drafts/{draft_id}/classes",
        {"name": "Movie", "slot_names": ["imdb_id", "title", "year"]},
    )
    post(
        f"/spec/drafts/{draft_id}/sources",
        {"name": "imdb", "entity_class_name": "Movie",
         "identifier_slot_name": "imdb_id"},
    )
    post(
        f"/spec/drafts/{draft_id}/sources",
        {"name": "tmdb", "entity_class_name": "Movie",
         "identifier_slot_name": "imdb_id"},
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 3 — Publish

        The publish gate validates the spec graph. On pass, the data-
        plane migration runs **in the same transaction** as the
        published-flag flip — so `knot_data.movie` materializes
        atomically with the spec going live.
        """
    )
    return


@app.cell
def _(draft_id, post):
    publish_resp = post(f"/spec/drafts/{draft_id}/publish")
    publish_resp
    return


@app.cell(hide_code=True)
def _(get, mo):
    slots_summary = [
        f"- `{s['name']}` — {s['range_name']}, policy={s['resolution_policy']}"
        for s in get("/spec/published/slots")
    ]
    mo.md(
        "**Published slot summary**\n\n" + "\n".join(slots_summary)
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 4 — Two sources disagree

        IMDB and TMDB both contribute `Movie` rows. They agree on the
        identifier (`tt0111161`) but **disagree on the year** — a real
        provenance scenario, no toy data.
        """
    )
    return


@app.cell
def _(post):
    post(
        "/graph/ingest/imdb",
        {"rows": [{"imdb_id": "tt0111161",
                   "title": "Shawshank",
                   "year": 1994}]},
    )
    post(
        "/graph/ingest/tmdb",
        {"rows": [{"imdb_id": "tt0111161",
                   "title": "The Shawshank Redemption",
                   "year": 1995}]},
    )
    return


@app.cell(hide_code=True)
def _(get, mo):
    rows = get("/graph/classes/Movie/tt0111161")["contributions"]
    body = "\n".join(
        f"| `{r['_source']}` | {r['title']} | {r['year']} |" for r in rows
    )
    mo.md(
        "### Raw contributions (multi-source bag)\n\n"
        "| source | title | year |\n"
        "|---|---|---|\n"
        f"{body}\n\n"
        "Same `_canonical_id`, two rows. Knot doesn't pick a winner at "
        "write time — multi-valued canonical, query-time resolution."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 5 — Resolve with default (uniform) priors

        Every slot has a Beta(1, 1) prior per (source, slot). Thompson
        sampling on `year` with no observations yet is a coin flip;
        ARGMAX_TRUST on `title` falls back to the alphabetically-first
        source.
        """
    )
    return


@app.cell(hide_code=True)
def _(get, mo):
    draws = []
    for _ in range(5):
        r = get("/graph/classes/Movie/tt0111161/resolved")["resolved"]
        draws.append((r["title"], r["year"]))
    body = "\n".join(f"| {i+1} | {t} | {y} |" for i, (t, y) in enumerate(draws))
    mo.md(
        "### Five resolution draws (uniform priors)\n\n"
        "| # | title | year |\n"
        "|---|---|---|\n"
        f"{body}\n\n"
        "Year flips between 1994 and 1995 (Thompson stochasticity); "
        "title is stable (ARGMAX_TRUST tie → alphabetical)."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 6 — A user submits a correction

        A user knows the year is **1994**. They POST a typed
        `PropertyCorrection`. Knot does three things atomically:

        1. Logs the correction in the immortal `_user_corrections`
           audit table.
        2. Upserts a `_source = '_user_corrections'` row in the data
           table.
        3. Emits **bandit feedback**: every disagreeing source's
           `(source, slot)` Beta posterior is updated. IMDB matched →
           α += 1; TMDB didn't → β += 1.

        No manual `/trust/feedback` calls. The correction closes the loop.
        """
    )
    return


@app.cell
def _(post):
    correction_response = post(
        "/graph/corrections",
        {"type": "property",
         "class_name": "Movie",
         "canonical_id": "tt0111161",
         "slot": "year",
         "value": 1994,
         "applied_by": "demo"},
    )
    correction_response
    return


@app.cell(hide_code=True)
def _(get, mo):
    posteriors = get("/graph/trust/posteriors")
    body = "\n".join(
        f"| `{p['source']}` | `{p['slot']}` | {p['alpha']:.0f} | {p['beta']:.0f} | {p['mean']:.3f} |"
        for p in posteriors
    )
    mo.md(
        "### Posteriors after the correction\n\n"
        "| source | slot | α | β | mean |\n"
        "|---|---|---|---|---|\n"
        f"{body}\n\n"
        "IMDB's `year` posterior shifted toward 1; TMDB's toward 0."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 7 — Resolution reflects learned trust

        Same query as before — but now the bandit knows IMDB has been
        getting `year` right.
        """
    )
    return


@app.cell(hide_code=True)
def _(get, mo):
    draws = []
    for _ in range(10):
        r = get("/graph/classes/Movie/tt0111161/resolved")["resolved"]
        draws.append(r["year"])
    counts = {1994: draws.count(1994), 1995: draws.count(1995)}
    mo.md(
        "### Ten Thompson draws (post-feedback)\n\n"
        f"- year = **1994** : {counts[1994]} draws\n"
        f"- year = **1995** : {counts[1995]} draws\n\n"
        "Year resolution converged on the corrected value because "
        "Thompson now samples from posteriors that prefer IMDB over TMDB "
        "for this slot."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## Step 8 — Audit walk-back

        Every fact carries `_spec_revision`; every correction carries
        `applied_revision`. The lineage is mechanical: contribution →
        spec_revision → published spec.
        """
    )
    return


@app.cell(hide_code=True)
def _(get, mo):
    audit = get("/graph/corrections")
    contribs = get("/graph/classes/Movie/tt0111161")["contributions"]
    audit_md = "\n".join(
        f"- correction #{a['id']} ({a['correction_type']}) — "
        f"applied_by `{a['applied_by']}`, "
        f"applied_revision `{a['applied_revision']}`, "
        f"`{a['payload']['slot']} = {a['payload']['value']}`"
        for a in audit
    )
    contribs_md = "\n".join(
        f"- `{c['_source']}` ingested at rev `{c['_spec_revision']}` "
        f"({c['_ingest_at']})"
        for c in contribs
    )
    mo.md(
        "### Audit log\n\n"
        f"{audit_md}\n\n"
        "### Per-row provenance\n\n"
        f"{contribs_md}"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        """
        ## What just happened

        - Built a 3-slot ontology + 2 sources via the API, draft → publish.
        - Migration ran in the publish transaction → `knot_data.movie`
          materialised.
        - Ingested **disagreeing** rows from imdb + tmdb.
        - Resolved with uniform priors (stochastic on `year`).
        - Submitted one user correction; **bandit posteriors updated
          themselves**.
        - Re-resolved; `year` converged on the corrected value.
        - Every fact + every correction is pinned to a spec revision.

        All through `/spec/*` and `/graph/*`. No external orchestrator,
        no DI impls, no lake — postgres is the source of truth and the
        API is the only seam.
        """
    )
    return


if __name__ == "__main__":
    app.run()
