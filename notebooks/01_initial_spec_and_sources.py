import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell
def _(mo):
    mo.md("""
    # 01 — Build initial spec and sources via API

    Drives knot's `/spec/*` router from a clean slate. Every spec mutation
    goes through draft → publish; nothing bypasses the API.
    """)
    return


@app.cell
def _():
    import sqlalchemy
    import requests

    control_db = sqlalchemy.create_engine(
        "postgresql+psycopg://knot:knot@localhost:5432/knot_control"
    )
    return (requests,)


@app.cell
def _():
    API = "http://localhost:8000"
    return (API,)


app._unparsable_cell(
    r"""
    TRUNCATE spec_revisions RESTART IDENTITY CASCADE
    """,
    name="_"
)


@app.cell
def _(API, requests):
    def post(path, body=None):
        r = requests.post(f"{API}{path}", json=body or {})
        r.raise_for_status()
        return r.json()

    def get(path):
        r = requests.get(f"{API}{path}")
        r.raise_for_status()
        return r.json()

    return get, post


@app.cell
def _(post):
    draft = post("/spec/drafts", {"label": "init"})
    draft_id = draft["revision"]
    draft
    return (draft_id,)


@app.cell
def _(draft_id, post):
    post(f"/spec/drafts/{draft_id}/types", {"name": "string", "base": "str"})
    return


@app.cell
def _(draft_id, post):
    post(
        f"/spec/drafts/{draft_id}/slots",
        {
            "name": "imdb_id",
            "range_kind": "type",
            "range_name": "string",
            "identifier": True,
            "required": True,
        },
    )
    post(
        f"/spec/drafts/{draft_id}/slots",
        {
            "name": "title",
            "range_kind": "type",
            "range_name": "string",
            "required": True,
        },
    )
    return


@app.cell
def _(draft_id, post):
    post(
        f"/spec/drafts/{draft_id}/classes",
        {"name": "Movie", "slot_names": ["imdb_id", "title"]},
    )
    return


@app.cell
def _(draft_id, post):
    post(
        f"/spec/drafts/{draft_id}/sources",
        {
            "name": "imdb_movies",
            "entity_class_name": "Movie",
            "identifier_slot_name": "imdb_id",
        },
    )
    return


@app.cell
def _(draft_id, post):
    publish_resp = post(f"/spec/drafts/{draft_id}/publish")
    publish_resp
    return


@app.cell
def _(get):
    {
        "classes": get("/spec/published/classes"),
        "slots": get("/spec/published/slots"),
        "types": get("/spec/published/types"),
        "sources": get("/spec/published/sources"),
    }
    return


if __name__ == "__main__":
    app.run()
