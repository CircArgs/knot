import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # knot — end-to-end

    From an empty database to multi-source disagreement, user
    corrections, and **bandit-learned trust** — driven entirely
    through the API.
    """)
    return


@app.cell(hide_code=True)
def _():
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

    return control_db, get, post


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 1 — Clean slate

    Truncate spec_revisions, drop the data-plane schema(), drop trust
    + correction state. Idempotent schema bootstrap recreates the
    control plane.
    """)
    return


@app.cell(hide_code=True)
def _(control_db):
    # Use sqlalchemy directly for the multi-DDL setup; mo.sql parses
    # bare table names as variable refs which trips on TRUNCATE / DROP
    # scripts.
    from sqlalchemy import text
    with control_db.begin() as _conn:
        for _stmt in (
            "TRUNCATE spec_revisions RESTART IDENTITY CASCADE",
            "DROP SCHEMA IF EXISTS knot_data CASCADE",
            "DROP TABLE IF EXISTS trust_config",
            "DROP TABLE IF EXISTS trust_posteriors",
            "DROP TABLE IF EXISTS _user_corrections",
        ):
            _conn.execute(text(_stmt))
    "wiped"
    return


@app.cell(hide_code=True)
def _():
    from knot import db
    db.apply_schema()
    "control plane bootstrapped"
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 2 — Author the ontology

    Spec authoring is API-driven. Every change goes through a draft
    → publish lifecycle so the spec graph is validated before it
    ships. We're modelling **Movie** with three slots: an
    identifier, a title, and a year.

    The interesting bit: `year` is wired to **POSTERIOR_MEAN** as
    its resolution policy. Disagreement on `year` will be resolved by
    picking the source with the highest posterior mean — deterministic,
    learns from corrections, no dice rolls.
    """)
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
         "resolution_policy": "posterior_mean"},
    )
    return


@app.cell
def _(draft_id, post):
    post(
        f"/spec/drafts/{draft_id}/classes",
        {"name": "Movie", "property_names": ["imdb_id", "title", "year"]},
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
    mo.md("""
    ## Step 3 — Publish

    The publish gate validates the spec graph. On pass, the data-
    plane migration runs **in the same transaction** as the
    published-flag flip — so `knot_data.movie` materializes
    atomically with the spec going live.
    """)
    return


@app.cell
def _(draft_id, post):
    _publish_resp = post(f"/spec/drafts/{draft_id}/publish")
    _publish_resp
    return


@app.cell(hide_code=True)
def _(get, mo):
    _slots_summary = [
        f"- `{s['name']}` — {s['range_name']}, policy={s['resolution_policy']}"
        for s in get("/spec/published/slots")
    ]
    mo.md(
        "**Published slot summary**\n\n" + "\n".join(_slots_summary)
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 4 — Two sources disagree

    IMDB and TMDB both contribute `Movie` rows. They agree on the
    identifier (`tt0111161`) but **disagree on the year** — a real
    provenance scenario, no toy data.
    """)
    return


@app.cell
def _(post):
    # imdb gets `year` wrong on purpose (1995); tmdb has it right (1994).
    # imdb is alphabetically first, so under uniform priors the wrong
    # value wins the tiebreak — until the user correction lands.
    post(
        "/graph/ingest/imdb",
        {"rows": [{"imdb_id": "tt0111161",
                   "title": "Shawshank",
                   "year": 1995}]},
    )
    post(
        "/graph/ingest/tmdb",
        {"rows": [{"imdb_id": "tt0111161",
                   "title": "The Shawshank Redemption",
                   "year": 1994}]},
    )
    return


@app.cell(hide_code=True)
def _(get, mo):
    _rows = get("/graph/classes/Movie/tt0111161")["contributions"]
    _body = "\n".join(
        f"| `{r['_source']}` | {r['title']} | {r['year']} |" for r in _rows
    )
    mo.md(
        "### Raw contributions (multi-source bag)\n\n"
        "| source | title | year |\n"
        "|---|---|---|\n"
        f"{_body}\n\n"
        "Same `_canonical_id`, two rows. Knot doesn't pick a winner at "
        "write time — multi-valued canonical, query-time resolution."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 5 — Resolve with default (uniform) priors

    Every slot has a Beta(1, 1) prior per (source, slot). With equal
    means, POSTERIOR_MEAN ties on `year` → alphabetical tie-break (imdb).
    ARGMAX_TRUST on `title` ties on default trust → alphabetical too.
    Resolution is deterministic.
    """)
    return


@app.cell(hide_code=True)
def _(get, mo):
    _draws = []
    for _ in range(5):
        _r = get("/graph/classes/Movie/tt0111161/resolved")["resolved"]
        _draws.append((_r["title"], _r["year"]))
    _body = "\n".join(
        f"| {i+1} | {t} | {y} |" for i, (t, y) in enumerate(_draws)
    )
    mo.md(
        "### Five resolutions (uniform priors)\n\n"
        "| # | title | year |\n"
        "|---|---|---|\n"
        f"{_body}\n\n"
        "Both stable — every query under the same posterior state returns "
        "the same answer."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 6 — A user submits a correction

    A user knows the year is **1994**. They POST a typed
    `PropertyCorrection`. Knot does three things atomically:

    1. Logs the correction in the immortal `_user_corrections`
       audit table.
    2. Upserts a `_source = '_user_corrections'` row in the data
       table.
    3. Emits **feedback**: every disagreeing source's
       `(source, slot)` Beta posterior is updated. TMDB matched →
       α += 1; IMDB didn't → β += 1.

    No manual `/trust/feedback` calls. The correction closes the loop.
    """)
    return


@app.cell
def _(post):
    _correction_response = post(
        "/graph/corrections",
        {"type": "property",
         "class_name": "Movie",
         "canonical_id": "tt0111161",
         "slot": "year",
         "value": 1994,
         "applied_by": "demo"},
    )
    _correction_response
    return


@app.cell(hide_code=True)
def _(get, mo):
    _posteriors = get("/graph/trust/posteriors")
    _body = "\n".join(
        f"| `{p['source']}` | `{p['slot']}` | {p['alpha']:.0f} | "
        f"{p['beta']:.0f} | {p['mean']:.3f} |"
        for p in _posteriors
    )
    mo.md(
        "### Posteriors after the correction\n\n"
        "| source | slot | α | β | mean |\n"
        "|---|---|---|---|---|\n"
        f"{_body}\n\n"
        "TMDB's `year` posterior shifted toward 1; IMDB's toward 0."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 7 — Resolution reflects learned trust

    Same query as before. The posteriors moved → POSTERIOR_MEAN's
    argmax flips from imdb to tmdb → resolved year goes from 1995 → 1994.
    """)
    return


@app.cell(hide_code=True)
def _(get, mo):
    _r = get("/graph/classes/Movie/tt0111161/resolved")["resolved"]
    mo.md(
        "### Resolved (post-feedback)\n\n"
        f"- title : **{_r['title']}**\n"
        f"- year  : **{_r['year']}**\n\n"
        "Year resolution flipped to the corrected value because TMDB's "
        "posterior mean for `year` now exceeds IMDB's. Deterministic — "
        "every subsequent query returns the same answer until new "
        "feedback arrives."
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 8 — Audit walk-back

    Every fact carries `_spec_revision`; every correction carries
    `applied_revision`. The lineage is mechanical: contribution →
    spec_revision → published spec.
    """)
    return


@app.cell(hide_code=True)
def _(get, mo):
    _audit = get("/graph/corrections")
    _contribs = get("/graph/classes/Movie/tt0111161")["contributions"]
    _audit_md = "\n".join(
        f"- correction #{a['id']} ({a['correction_type']}) — "
        f"applied_by `{a['applied_by']}`, "
        f"applied_revision `{a['applied_revision']}`, "
        f"`{a['payload']['slot']} = {a['payload']['value']}`"
        for a in _audit
    )
    _contribs_md = "\n".join(
        f"- `{c['_source']}` ingested at rev `{c['_spec_revision']}` "
        f"({c['_ingest_at']})"
        for c in _contribs
    )
    mo.md(
        "### Audit log\n\n"
        f"{_audit_md}\n\n"
        "### Per-row provenance\n\n"
        f"{_contribs_md}"
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
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
    """)
    return


if __name__ == "__main__":
    app.run()
