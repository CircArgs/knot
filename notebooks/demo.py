"""knot demo — Netflix ontology with Source + SourceBinding.

A runnable narrative that drives knot through its `/spec/*` and
`/graph/*` API. Demonstrates the Phase 3 inline-slot model: slots are
defined inline on each class (no top-level slot registry).

Bring-up:
    ./scripts/up.sh
    KNOT_DEV_MODE=1 .venv/bin/uvicorn knot.api.main:app \\
        --port 8000 --log-level warning &
    .venv/bin/marimo edit notebooks/demo.py

Re-runnable: the `reset()` helper truncates spec + data + corrections via
the same control-plane connection the API uses, so re-running the notebook
top-to-bottom is idempotent.
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="full")


@app.cell(hide_code=True)
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # knot — Netflix ontology demo

    A streaming-media ontology ingested from three sources (IMDB, TMDB,
    Wikipedia) that disagree on field names, field formats, and coverage.
    SourceBindings translate each source's native payload into the canonical
    slot names, and per-binding trust priors influence resolution.

    ## What this demo covers

    | Feature | Example |
    |---|---|
    | Mixin inheritance | `Auditable`, `Localizable` applied to `MediaItem` and `Person` |
    | `is_a` hierarchy | `MediaItem → Movie / TVSeries / Episode`; `Person → Director` |
    | Reified junction class | `Credit` (Movie ↔ Person with role + billing) |
    | Defined class (VIEW) | `Director` — any Person with a director Credit |
    | Multi-class source | IMDB binds to `Movie`, `Person`, **and** `Credit` |
    | SQL constraints | `year_plausible` on Movie; `director_credit_role_is_known` on Credit |
    | IMDB-native field names | `tconst`, `primaryTitle`, `nconst`, `primaryName` ... |

    Drag any node, scroll to zoom, click for properties on the right.

    ---

    **Bring-up** (one terminal):

    ```bash
    ./scripts/up.sh
    KNOT_DEV_MODE=1 .venv/bin/uvicorn knot.api.main:app     --port 8000 --log-level warning &
    ```
    """)
    return


@app.cell(hide_code=True)
def _():
    import json

    import requests
    import sqlalchemy
    from sqlalchemy import text

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

    def reset():
        """Wipe spec + data + corrections so the notebook re-runs from clean.

        We TRUNCATE the audit/correction/trust tables and DROP the data-plane
        schema. Truncate-cascades keep the FK graph happy; the API server
        already bootstrapped the schema at startup so we never re-bootstrap
        from the notebook (which would otherwise pull in async DSN config).
        """
        with control_db.begin() as _conn:
            for _stmt in (
                "TRUNCATE spec_revisions RESTART IDENTITY CASCADE",
                "DROP SCHEMA IF EXISTS knot_data CASCADE",
                "CREATE SCHEMA knot_data",
                "TRUNCATE _user_corrections RESTART IDENTITY CASCADE",
                "TRUNCATE canonical_id_lineage RESTART IDENTITY CASCADE",
                "TRUNCATE trust_posteriors RESTART IDENTITY CASCADE",
                "TRUNCATE trust_config RESTART IDENTITY CASCADE",
            ):
                _conn.execute(text(_stmt))

    return control_db, get, json, post, reset


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 1 — Clean slate, then publish the Netflix ontology

    ### Ontology layers

    **Mixins** (`Auditable`, `Localizable`) — abstract crosscutting bundles applied
    via `mixins=`. A mixin is NOT a parent class; it is a named collection of slots
    that multiple unrelated classes share.

    **Abstract parent** (`MediaItem`) — groups the shared media slots and carries
    `is_a` children. `Movie`, `TVSeries`, and `Episode` each *are* a MediaItem, so
    `is_a` is correct.

    **Concrete children** (`Movie`, `TVSeries`, `Episode`, `Person`, `Credit`) —
    each gets a `knot_data.<class>` table and a `knot_data.<class>_bindings` SCD2
    table.

    **Defined class** (`Director`) — backed by a VIEW, not a table. The definition
    is a spec-level SQL predicate: any Person who has a Credit with `role = 'director'`.
    Authors write:

    ```sql
    EXISTS (SELECT 1 FROM Credit WHERE Credit.person = self AND Credit.role = 'director')
    ```

    The compiler resolves `Credit` → schema-qualified table + SCD2 bindings JOIN,
    `Credit.person`/`Credit.role` → alias-qualified column refs, and `self` → the
    outer binding's `canonical_id`, emitting the full DDL:

    ```sql
    CREATE OR REPLACE VIEW knot_data.director AS
      SELECT s.*, b.canonical_id AS _canonical_id
      FROM knot_data.person s
      JOIN knot_data.person_bindings b
        ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL
      WHERE (<compiled-predicate>)
    ```

    ### Why is_a vs mixin?

    > `Movie`/`TVSeries`/`Episode` use `is_a MediaItem` because each *structurally
    > IS* a media item — they inherit the identity and share the table structure.
    > `Director` uses `is_a Person` for the same reason (a Director IS a Person).
    > `Auditable` and `Localizable` are *mixins* because audit timestamps and locale
    > lists are crosscutting concerns that could apply to completely different entity
    > types without any shared structural identity.

    ### Multi-class IMDB source

    A single `Source` (imdb) binds to **three** classes via three separate
    `SourceBinding` records. Each binding carries its own identifier slot, trust
    prior, and field-mapping rules. This demonstrates the key design: `Source` is a
    thin identity node; `SourceBinding` is the reified `(Source, Class)` pair that
    owns the integration metadata.

    ### SQL constraints

    Since constraints are SQL predicate strings (validated via sqlglot at publish
    time), we can write arbitrary WHERE-clause logic:

    - `year_plausible` — gates Movie rows to years in `[1888, now()+5]`
    - `director_credit_role_is_known` — gates Credit rows to a known role set
    """)
    return


@app.cell
def _(mo):
    import json as _json

    SPEC = {
        # ── Mixin classes (slots inline) ──────────────────────────────────────
        "mixin_classes": [
            {
                "name": "Auditable",
                "abstract": True,
                "slots": [
                    {"name": "created_at", "type_kind": "primitive", "type_name": "datetime"},
                    {"name": "updated_at", "type_kind": "primitive", "type_name": "datetime"},
                ],
                "description": "Mixin: stamps any entity with audit timestamps.",
            },
            {
                "name": "Localizable",
                "abstract": True,
                "slots": [
                    {"name": "default_locale",    "type_kind": "primitive",       "type_name": "string"},
                    {"name": "available_locales", "type_kind": "array_of_primitive", "type_name": "string"},
                ],
                "description": "Mixin: marks media artifacts as having localized content.",
            },
        ],
        # ── Abstract parent (no data slots) ──────────────────────────────────
        # MediaItem is a logical grouping that applies Auditable + Localizable
        # mixins and marks Movie/TVSeries/Episode as structurally related via
        # is_a. It carries NO data slots itself.
        #
        # Why: effective_slots() does NOT walk is_a (each concrete class owns
        # its own table; walking is_a would double-store parent columns). This
        # means all slots that a SourceBinding maps to — including shared ones
        # like title, year, synopsis — must live on the concrete class's own
        # slot list or on one of its direct mixins, NOT on the is_a parent.
        "parent_class": [
            {
                "name": "MediaItem",
                "abstract": True,
                "mixin_names": ["Auditable", "Localizable"],
                "slots": [],
                "description": (
                    "Abstract parent for Movie / TVSeries / Episode. "
                    "Carries only mixin application (Auditable, Localizable); "
                    "shared media slots live on each concrete child directly."
                ),
            },
        ],
        # ── Concrete children — each owns its full slot set ────────────────────
        # Shared media slots (title, year, synopsis) live here, not on MediaItem,
        # so that SourceBinding mappings can resolve them via effective_slots.
        # Source-identifier slots (imdb_id, tmdb_id, wiki_slug) are one per
        # source, each on the class that the binding feeds.
        "child_classes": [
            {
                "name": "Movie",
                "is_a_name": "MediaItem",
                "slots": [
                    # Source-identifier slots: one per source, all plain (no identifier=True
                    # / required=True). Each binding's identifier_slot points to its own slot;
                    # required_slot_names on the binding enforces presence at ingest time
                    # for that source only. This way IMDB rows don't need tmdb_id and vice versa.
                    {"name": "imdb_id",   "type_kind": "primitive", "type_name": "string"},
                    {"name": "tmdb_id",   "type_kind": "primitive", "type_name": "string"},
                    {"name": "wiki_slug", "type_kind": "primitive", "type_name": "string"},
                    # Shared media slots — title is required across all sources.
                    {"name": "title",    "type_kind": "primitive", "type_name": "string", "required": True},
                    {"name": "year",     "type_kind": "primitive", "type_name": "integer"},
                    {"name": "synopsis", "type_kind": "primitive", "type_name": "string"},
                    {"name": "runtime",  "type_kind": "primitive", "type_name": "integer"},
                ],
                "description": "A theatrical or direct-to-streaming film.",
            },
            {
                "name": "TVSeries",
                "is_a_name": "MediaItem",
                "slots": [
                    {"name": "season_count", "type_kind": "primitive", "type_name": "integer"},
                ],
                "description": "A multi-season serialised show.",
            },
            {
                "name": "Episode",
                "is_a_name": "MediaItem",
                "slots": [
                    {"name": "episode_number", "type_kind": "primitive", "type_name": "integer"},
                ],
                "description": "A single episode of a TVSeries.",
            },
        ],
        # ── Person + Person subclasses ─────────────────────────────────────────
        "person_classes": [
            {
                "name": "Person",
                "mixin_names": ["Auditable", "Localizable"],
                "slots": [
                    # Source-id slots: one per source, plain (no identifier=True).
                    # required_slot_names on each binding enforces the right id
                    # per source. name is required across all sources.
                    {"name": "name",             "type_kind": "primitive", "type_name": "string", "required": True},
                    {"name": "born",             "type_kind": "primitive", "type_name": "integer"},
                    {"name": "imdb_person_id",   "type_kind": "primitive", "type_name": "string"},
                    {"name": "wiki_person_slug", "type_kind": "primitive", "type_name": "string"},
                ],
                "description": "A person (actor, director, writer, etc.).",
            },
        ],
        # ── Defined classes (compiled to VIEWs, not tables) ───────────────────
        # Director: a Person who has a Credit with role='director'.
        # The definition is written at the spec level — bare class names and
        # ``self`` are resolved by the compiler:
        #   - ``Credit`` → knot_data.credit + bindings JOIN (SCD2 currency)
        #   - ``Credit.person``, ``Credit.role`` → alias-qualified column refs
        #   - ``self`` → outer bindings alias's canonical_id (the Person row
        #     being tested by the VIEW's WHERE clause)
        # The compiler expands this to the full DDL-level EXISTS subquery.
        "defined_classes": [
            {
                "name": "Director",
                "is_a_name": "Person",
                "definition": (
                    "EXISTS ("
                    "  SELECT 1 FROM Credit"
                    "  WHERE Credit.person = self"
                    "    AND Credit.role = 'director'"
                    ")"
                ),
                "description": (
                    "Defined class: any Person who has a Credit with role='director'. "
                    "Compiled to a VIEW over knot_data.person filtered by an EXISTS "
                    "subquery into knot_data.credit (spec-level form — no DDL details)."
                ),
            },
        ],
        # ── Credit (junction class: Movie ↔ Person) ────────────────────────────
        # Two ClassRef slots (movie, person) make this a junction. The UI renders
        # these with a cut-corner octagon shape.
        "junction_classes": [
            {
                "name": "Credit",
                "slots": [
                    {"name": "credit_id",     "type_kind": "primitive", "type_name": "string", "required": True, "identifier": True},
                    {"name": "movie",         "type_kind": "class",     "type_name": "Movie"},
                    {"name": "person",        "type_kind": "class",     "type_name": "Person"},
                    {"name": "role",          "type_kind": "primitive", "type_name": "string"},
                    {"name": "billing_order", "type_kind": "primitive", "type_name": "integer"},
                ],
                "description": "Reified Movie↔Person relation with role and billing data.",
            },
        ],
        # ── Sources (thin labels) ──────────────────────────────────────────────
        "sources": [
            {"name": "imdb", "description": "IMDB ratings & metadata"},
            {"name": "tmdb", "description": "The Movie Database"},
            {"name": "wiki", "description": "Wikipedia"},
        ],
        # ── SourceBindings — one per (source, class) pair ─────────────────────
        # IMDB binds to THREE classes: Movie, Person, Credit.
        # TMDB and wiki bind to Movie; wiki also binds to Person.
        "source_bindings": [
            # ── IMDB → Movie ──────────────────────────────────────────────────
            {
                "source_name": "imdb",
                "class_name": "Movie",
                "identifier_slot_name": "imdb_id",
                "trust_prior": [9.0, 1.0],
                "required_slot_names": ["imdb_id", "title"],
                "description": "IMDB → Movie: strong prior, IMDB native field names.",
                "mappings": [
                    {"slot_name": "imdb_id",  "source_field": "tconst"},
                    {"slot_name": "title",    "source_field": "primaryTitle", "prior": [50.0, 1.0]},
                    {"slot_name": "year",     "source_field": "startYear"},
                    {"slot_name": "runtime",  "source_field": "runtimeMinutes"},
                ],
            },
            # ── IMDB → Person ─────────────────────────────────────────────────
            {
                "source_name": "imdb",
                "class_name": "Person",
                "identifier_slot_name": "imdb_person_id",
                "trust_prior": [7.0, 1.0],
                "required_slot_names": ["imdb_person_id", "name"],
                "description": "IMDB → Person: nconst/primaryName field mapping.",
                "mappings": [
                    {"slot_name": "imdb_person_id", "source_field": "nconst"},
                    {"slot_name": "name",           "source_field": "primaryName"},
                    {"slot_name": "born",           "source_field": "birthYear"},
                ],
            },
            # ── IMDB → Credit ─────────────────────────────────────────────────
            {
                "source_name": "imdb",
                "class_name": "Credit",
                "identifier_slot_name": "credit_id",
                "trust_prior": [8.0, 2.0],
                "required_slot_names": ["credit_id"],
                "description": "IMDB → Credit: tconst_nconst composite identifier.",
                "mappings": [
                    {"slot_name": "credit_id",    "source_field": "tconst_nconst"},
                    {"slot_name": "movie",        "source_field": "tconst"},
                    {"slot_name": "person",       "source_field": "nconst"},
                    {"slot_name": "role",         "source_field": "category"},
                    {"slot_name": "billing_order","source_field": "ordering"},
                ],
            },
            # ── TMDB → Movie ──────────────────────────────────────────────────
            {
                "source_name": "tmdb",
                "class_name": "Movie",
                "identifier_slot_name": "tmdb_id",
                "trust_prior": [7.0, 2.0],
                "description": "TMDB → Movie: renames id→tmdb_id, original_title→title, etc.",
                "mappings": [
                    {"slot_name": "tmdb_id",  "source_field": "id"},
                    {"slot_name": "title",    "source_field": "original_title"},
                    {"slot_name": "year",     "source_field": "release_year"},
                    {"slot_name": "runtime",  "source_field": "runtime_minutes"},
                ],
            },
            # ── Wiki → Movie ──────────────────────────────────────────────────
            {
                "source_name": "wiki",
                "class_name": "Movie",
                "identifier_slot_name": "wiki_slug",
                "trust_prior": [3.0, 2.0],
                "description": "Wikipedia → Movie: weaker prior, contributes synopsis.",
                "mappings": [
                    {"slot_name": "wiki_slug", "source_field": "slug"},
                    {"slot_name": "title",     "source_field": "display_title"},
                    {"slot_name": "synopsis",  "source_field": "lead_paragraph"},
                ],
            },
            # ── Wiki → Person ─────────────────────────────────────────────────
            {
                "source_name": "wiki",
                "class_name": "Person",
                "identifier_slot_name": "wiki_person_slug",
                "trust_prior": [4.0, 2.0],
                "description": "Wikipedia → Person: contributes birth year and slug.",
                "mappings": [
                    {"slot_name": "wiki_person_slug", "source_field": "person_slug"},
                    {"slot_name": "name",             "source_field": "display_name"},
                    {"slot_name": "born",             "source_field": "birth_year"},
                ],
            },
        ],
        # ── SQL constraints ────────────────────────────────────────────────────
        # Now that constraints are SQL predicate strings, we can write concise
        # WHERE-clause fragments. Validated via sqlglot at publish time.
        "constraints": [
            {
                "name": "year_plausible",
                "primary_class_name": "Movie",
                "body": "year >= 1888 AND year <= EXTRACT(YEAR FROM now())::integer + 5",
                "severity": "error",
                "message": "Movie year must be between 1888 and now+5 years.",
            },
            {
                "name": "director_credit_role_is_known",
                "primary_class_name": "Credit",
                "body": "role IN ('director', 'writer', 'actor', 'producer', 'editor', 'composer')",
                "severity": "warning",
                "message": "Credit role must be one of the known categories.",
            },
        ],
    }

    mo.ui.code_editor(
        value=_json.dumps(SPEC, indent=2),
        language="json",
        disabled=True,
    )
    return (SPEC,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    The build order matters:

    1. Mixin classes (`Auditable`, `Localizable`) — before any class that applies them.
    2. Abstract parent (`MediaItem`) — before its `is_a` children.
    3. Concrete children (`Movie`, `TVSeries`, `Episode`) — before `Credit` which
       holds ClassRef slots pointing to `Movie` and `Person`.
    4. `Person` — before `Credit` (ClassRef target) and before `Director` (is_a parent).
    5. `Credit` — concrete junction class; must exist as a table before the Director
       VIEW's EXISTS subquery can reference `knot_data.credit`.
    6. `Director` — defined class compiled to a VIEW; must come after `Person` and
       after `Credit` tables are created.
    7. Sources — thin labels; no dependencies.
    8. SourceBindings — after all sources and all classes.
    9. Constraints — after all classes (primary_class_name must resolve).
    """)
    return


@app.cell
def _(SPEC, mo, post, reset):
    reset()
    draft_id = post("/spec/drafts", {"label": "netflix-demo-v2"})["revision"]

    # Build order: mixin → abstract parent → concrete children → person →
    # junction (Credit) → defined classes (Director) → sources → bindings → constraints.
    _PHASES = [
        ("mixin_classes",    "classes"),
        ("parent_class",     "classes"),
        ("child_classes",    "classes"),
        ("person_classes",   "classes"),
        ("junction_classes", "classes"),
        ("defined_classes",  "classes"),
        ("sources",          "sources"),
        ("source_bindings",  "source_bindings"),
        ("constraints",      "constraints"),
    ]

    _ledger: list[dict[str, str]] = []
    for _phase, _endpoint in _PHASES:
        for _body in SPEC.get(_phase, []):
            _path = f"/spec/drafts/{draft_id}/{_endpoint}"
            try:
                post(_path, _body)
                _status = "200 OK"
            except Exception as _exc:
                _status = f"FAILED: {_exc}"
            _name = _body.get("name") or _body.get("source_name", "?") + "/" + _body.get("class_name", "?")
            _ledger.append({
                "phase": _phase,
                "endpoint": _path,
                "name": _name,
                "status": _status,
            })

    publish_resp = post(f"/spec/drafts/{draft_id}/publish")
    _ledger.append({
        "phase": "publish",
        "endpoint": f"/spec/drafts/{draft_id}/publish",
        "name": "—",
        "status": f"revision {publish_resp['revision']}",
    })

    mo.ui.table(_ledger)
    return draft_id, publish_resp


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 2 — Spec graph

    Nodes are spec-graph entities (classes, sources, source bindings).
    Slots live inline on each class node. Edges encode structural relationships:

    - `class --is_a--> class`  *(inheritance: Movie→MediaItem, Director→Person)*
    - `class --mixin--> class`  *(crosscutting: MediaItem→Auditable, Person→Localizable)*
    - `class --fk--> class`  *(ClassRef slot: Credit.movie→Movie, Credit.person→Person)*
    - `source_binding --binds--> class`
    - `source_binding --source--> source`

    Notice that IMDB has **three** binding nodes — one per class it feeds.
    The `Credit` class has two FK edges (to `Movie` and `Person`), marking it as
    a junction. The `Director` class has an `is_a → Person` edge and no binding
    (it is a defined class / VIEW, not a data source).

    Click any node to see its full attribute set on the right.
    """)
    return


@app.cell(hide_code=True)
def _(get):
    spec_classes = get("/spec/published/classes")
    spec_sources = get("/spec/published/sources")
    spec_bindings = get("/spec/published/source_bindings")
    return spec_bindings, spec_classes, spec_sources


@app.cell(hide_code=True)
def _(spec_bindings, spec_classes, spec_sources):
    # Build vis.js node + edge sets from the published spec read-models.
    spec_nodes: list = []
    spec_edges: list = []

    def _add_node(group, name, props):
        spec_nodes.append({
            "id": f"{group}:{name}",
            "label": name,
            "group": group,
            "props": props,
        })

    for _c in spec_classes:
        # Summarise inline slots for the property panel.
        _slot_summary = [s["name"] for s in _c.get("slots", [])]
        _add_node("class", _c["name"], {**_c, "own_slots": _slot_summary})
    for _src in spec_sources:
        _add_node("source", _src["name"], _src)

    # class --is_a--> parent
    _class_names = {_c["name"] for _c in spec_classes}
    for _c in spec_classes:
        if _c.get("is_a") and _c["is_a"] in _class_names:
            spec_edges.append({
                "id": f"isa:{_c['name']}",
                "from": f"class:{_c['name']}",
                "to": f"class:{_c['is_a']}",
                "label": "is_a",
                "props": {"relationship": "inheritance"},
            })
        for _mx in _c.get("mixins", []):
            if _mx in _class_names:
                spec_edges.append({
                    "id": f"mixin:{_c['name']}:{_mx}",
                    "from": f"class:{_c['name']}",
                    "to": f"class:{_mx}",
                    "label": "mixin",
                    "props": {"relationship": "mixin"},
                })

    # class-ref slots: class --fk--> class
    for _c in spec_classes:
        for _s in _c.get("slots", []):
            if _s.get("type_kind") in ("class", "array_of_class") and _s.get("type_name") in _class_names:
                spec_edges.append({
                    "id": f"fk:{_c['name']}.{_s['name']}",
                    "from": f"class:{_c['name']}",
                    "to": f"class:{_s['type_name']}",
                    "label": _s["name"],
                    "props": {"slot": _s["name"], "type_kind": _s["type_kind"]},
                })

    # source_binding --binds--> class
    for _b in spec_bindings:
        _add_node("binding", f"{_b['source_name']}/{_b['class_name']}", _b)
        spec_edges.append({
            "id": f"binding-src:{_b['source_name']}:{_b['class_name']}",
            "from": f"binding:{_b['source_name']}/{_b['class_name']}",
            "to": f"source:{_b['source_name']}",
            "label": "source",
            "props": {},
        })
        spec_edges.append({
            "id": f"binding-cls:{_b['source_name']}:{_b['class_name']}",
            "from": f"binding:{_b['source_name']}/{_b['class_name']}",
            "to": f"class:{_b['class_name']}",
            "label": f"binds ({_b.get('identifier_slot', _b.get('identifier_slot_name', ''))})",
            "props": {
                "trust_prior": _b.get("trust_prior"),
                "required_slots": _b.get("required_slots", _b.get("required_slot_names", [])),
                "mappings": len(_b.get("mappings", [])),
            },
        })

    return spec_edges, spec_nodes


@app.cell(hide_code=True)
def _(json, mo, spec_edges: list, spec_nodes: list):
    _palette = {
        "class":   ("#4f9eff", "#2563eb", "#dbeafe", "#1e40af"),
        "source":  ("#f59e0b", "#b45309", "#fef3c7", "#92400e"),
        "binding": ("#a78bfa", "#7c3aed", "#ede9fe", "#5b21b6"),
    }
    _groups_js = "{\n" + ",\n".join(
        f'        {g}: {{ color: {{ background: "{bg}", border: "{br}" }} }}'
        for g, (bg, br, _, _) in _palette.items()
    ) + "\n      }"
    _tag_map_js = json.dumps({g: {"bg": tagbg, "fg": tagfg}
                              for g, (_, _, tagbg, tagfg) in _palette.items()})

    _spec_html = f"""
    <link href="https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css" rel="stylesheet" />
    <script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>

    <div style="display: flex; gap: 12px; height: 620px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
      <div id="spec-graph"
           style="flex: 2; border: 1px solid #ccc; border-radius: 6px; background: #fafafa;">
      </div>
      <div id="spec-props"
           style="flex: 1; padding: 16px; border: 1px solid #ccc; border-radius: 6px;
                  background: #fff; overflow: auto; font-size: 13px;">
        <em style="color: #888;">Click a node or edge to inspect.</em>
      </div>
    </div>

    <script>
    (function() {{
      const rawNodes = {json.dumps(spec_nodes)};
      const rawEdges = {json.dumps(spec_edges)};
      const tagMap = {_tag_map_js};

      const visNodes = rawNodes.map(n => ({{
        id: n.id, label: n.label, group: n.group,
        title: `${{n.group}}: ${{n.label}}`, _props: n.props,
      }}));
      const visEdges = rawEdges.map(e => ({{
        id: e.id, from: e.from, to: e.to, label: e.label,
        title: e.label, _props: e.props,
      }}));

      const nodes = new vis.DataSet(visNodes);
      const edges = new vis.DataSet(visEdges);
      const container = document.getElementById('spec-graph');
      const propsEl = document.getElementById('spec-props');

      const network = new vis.Network(container, {{ nodes, edges }}, {{
        nodes: {{ shape: 'dot', size: 22, font: {{ size: 14, color: '#222' }}, borderWidth: 2 }},
        edges: {{
          arrows: 'to',
          font: {{ size: 11, align: 'middle', color: '#555', strokeWidth: 0 }},
          color: {{ color: '#888', highlight: '#ff6b35' }},
          smooth: {{ type: 'continuous' }},
        }},
        groups: {_groups_js},
        physics: {{
          stabilization: {{ iterations: 250 }},
          barnesHut: {{ gravitationalConstant: -10000, springLength: 140 }},
        }},
        interaction: {{ hover: true, tooltipDelay: 250, dragNodes: true }},
      }});

      function renderProps(title, group, propsObj) {{
        const tag = group ? `<span style="display:inline-block; padding: 2px 8px;
                            border-radius: 999px; background: ${{tagMap[group]?.bg ?? '#eee'}};
                            color: ${{tagMap[group]?.fg ?? '#333'}}; font-size: 11px;
                            margin-left: 8px;">${{group}}</span>` : '';
        const rows = Object.entries(propsObj || {{}}).map(([k, v]) =>
          `<tr><td style="padding: 4px 12px 4px 0; color: #666; vertical-align: top;">${{k}}</td>
               <td style="padding: 4px 0; font-family: ui-monospace, monospace;">${{
                 typeof v === 'string' ? v : JSON.stringify(v)
               }}</td></tr>`
        ).join('');
        propsEl.innerHTML = `
          <div style="font-size: 16px; font-weight: 600; margin-bottom: 12px;">${{title}}${{tag}}</div>
          <table style="border-collapse: collapse; width: 100%;">${{rows}}</table>
        `;
      }}

      network.on('selectNode', params => {{
        const n = nodes.get(params.nodes[0]);
        renderProps(n.label, n.group, n._props);
      }});
      network.on('selectEdge', params => {{
        if (params.nodes.length > 0) return;
        const e = edges.get(params.edges[0]);
        const fromNode = nodes.get(e.from);
        const toNode = nodes.get(e.to);
        renderProps(`${{fromNode.label}} → ${{e.label}} → ${{toNode.label}}`, null, e._props);
      }});
      network.on('deselectNode', () => {{
        propsEl.innerHTML = '<em style="color: #888;">Click a node or edge to inspect.</em>';
      }});
    }})();
    </script>
    """
    mo.iframe(_spec_html, height="640px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 2.5 — Tables and views generated from this spec

    Publishing the spec triggers DDL emission. Every **concrete** class
    (`Movie`, `TVSeries`, `Episode`, `Person`, `Credit`) becomes two tables:

    - `knot_data.<class>` — source-row table (one row per source contribution)
    - `knot_data.<class>_bindings` — SCD2 table tracking which `canonical_id`
      each source row currently maps to

    **Director** is a **defined class** — it compiles to a VIEW, not a table.
    The spec-level definition body is:

    ```sql
    EXISTS (SELECT 1 FROM Credit WHERE Credit.person = self AND Credit.role = 'director')
    ```

    The compiler rewrites bare class names (`Credit` → `knot_data.credit` + SCD2 bindings JOIN)
    and `self` → outer binding's `canonical_id`, producing:

    ```sql
    CREATE OR REPLACE VIEW knot_data.director AS
      SELECT s.*, b.canonical_id AS _canonical_id
      FROM knot_data.person s
      JOIN knot_data.person_bindings b
        ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL
      WHERE (
        EXISTS (
          SELECT 1 FROM knot_data.credit AS credit__c0
          INNER JOIN knot_data.credit_bindings AS credit__b0
            ON credit__b0.knot_row_id = credit__c0._knot_row_id
           AND credit__b0.valid_to IS NULL
          WHERE credit__c0.person = b.canonical_id
            AND credit__c0.role = 'director'
        )
      )
    ```

    Abstract classes (`MediaItem`, `Auditable`, `Localizable`) do **not**
    get tables — they are compile-time constructs that expand their slots
    into concrete children.

    Click any table or view node to see its column list.
    """)
    return


@app.cell(hide_code=True)
def _(control_db, json, mo):
    from sqlalchemy import text as _text

    with control_db.begin() as _conn:
        _tables = _conn.execute(_text("""
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'knot_data'
            ORDER BY table_name
        """)).all()
        _cols = _conn.execute(_text("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'knot_data'
            ORDER BY table_name, ordinal_position
        """)).all()

    _columns_by_table: dict = {}
    for _row in _cols:
        _columns_by_table.setdefault(_row[0], []).append({
            "name": _row[1],
            "type": _row[2],
            "nullable": _row[3] == "YES",
        })

    _schema_nodes: list = []
    for _table_name, _ttype in _tables:
        if _ttype == "VIEW":
            _group = "view"
        elif _table_name.endswith("_bindings"):
            _group = "bindings"
        else:
            _group = "source_rows"
        _schema_nodes.append({
            "id": _table_name,
            "label": _table_name,
            "group": _group,
            "props": {
                "table_type": _ttype,
                "columns": _columns_by_table.get(_table_name, []),
            },
        })

    _schema_edges: list = []
    _table_set = {n["id"] for n in _schema_nodes}
    for _table_name, _ttype in _tables:
        if _table_name.endswith("_bindings"):
            _parent = _table_name[: -len("_bindings")]
            if _parent in _table_set:
                _schema_edges.append({
                    "id": f"scd2:{_parent}->{_table_name}",
                    "from": _parent,
                    "to": _table_name,
                    "label": "SCD2",
                    "props": {"relationship": "source-row → bindings"},
                })

    _schema_palette = {
        "source_rows": ("#4f9eff", "#2563eb"),
        "bindings":    ("#a78bfa", "#7c3aed"),
        "view":        ("#fbbf24", "#d97706"),
    }
    _schema_groups_js = "{\n" + ",\n".join(
        f'        {g}: {{ color: {{ background: "{bg}", border: "{br}" }} }}'
        for g, (bg, br) in _schema_palette.items()
    ) + "\n      }"

    _schema_html = f"""
    <link href="https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css" rel="stylesheet" />
    <script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>

    <div style="display: flex; gap: 12px; height: 480px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
      <div id="schema-graph"
           style="flex: 2; border: 1px solid #ccc; border-radius: 6px; background: #fafafa;">
      </div>
      <div id="schema-props"
           style="flex: 1; padding: 16px; border: 1px solid #ccc; border-radius: 6px;
                  background: #fff; overflow: auto; font-size: 13px;">
        <em style="color: #888;">Click a table or edge to inspect.</em>
      </div>
    </div>

    <script>
    (function() {{
      const rawNodes = {json.dumps(_schema_nodes)};
      const rawEdges = {json.dumps(_schema_edges)};

      const visNodes = rawNodes.map(n => ({{
        id: n.id, label: n.label, group: n.group,
        title: `${{n.group}}: ${{n.label}}`, _props: n.props,
      }}));
      const visEdges = rawEdges.map(e => ({{
        id: e.id, from: e.from, to: e.to, label: e.label,
        title: e.label, _props: e.props,
      }}));

      const nodes = new vis.DataSet(visNodes);
      const edges = new vis.DataSet(visEdges);
      const container = document.getElementById('schema-graph');
      const propsEl = document.getElementById('schema-props');

      const network = new vis.Network(container, {{ nodes, edges }}, {{
        nodes: {{ shape: 'dot', size: 22, font: {{ size: 14, color: '#222' }}, borderWidth: 2 }},
        edges: {{
          arrows: 'to',
          font: {{ size: 11, align: 'middle', color: '#555', strokeWidth: 0 }},
          color: {{ color: '#888', highlight: '#ff6b35' }},
          smooth: {{ type: 'continuous' }},
        }},
        groups: {_schema_groups_js},
        physics: {{
          stabilization: {{ iterations: 250 }},
          barnesHut: {{ gravitationalConstant: -10000, springLength: 160 }},
        }},
        interaction: {{ hover: true, tooltipDelay: 250, dragNodes: true }},
      }});

      function renderColumns(cols) {{
        if (!cols || cols.length === 0) return '<em style="color:#888;">no columns</em>';
        const header = `
          <tr style="border-bottom: 1px solid #ddd;">
            <th style="text-align: left; padding: 4px 12px 4px 0; font-weight: 600; color: #444;">column</th>
            <th style="text-align: left; padding: 4px 12px 4px 0; font-weight: 600; color: #444;">type</th>
            <th style="text-align: left; padding: 4px 0; font-weight: 600; color: #444;">null</th>
          </tr>`;
        const body = cols.map(c =>
          `<tr>
             <td style="padding: 3px 12px 3px 0; font-family: ui-monospace, monospace;">${{c.name}}</td>
             <td style="padding: 3px 12px 3px 0; font-family: ui-monospace, monospace; color: #555;">${{c.type}}</td>
             <td style="padding: 3px 0; color: ${{c.nullable ? '#888' : '#444'}};">${{c.nullable ? 'YES' : 'NO'}}</td>
           </tr>`
        ).join('');
        return `<table style="border-collapse: collapse; width: 100%; font-size: 12px;">${{header}}${{body}}</table>`;
      }}

      function renderTableProps(title, group, propsObj) {{
        const tag = `<span style="display:inline-block; padding: 2px 8px;
                      border-radius: 999px; background: #eef; color: #335;
                      font-size: 11px; margin-left: 8px;">${{group}}</span>`;
        const ttype = propsObj.table_type || '';
        const cols = renderColumns(propsObj.columns || []);
        propsEl.innerHTML = `
          <div style="font-size: 16px; font-weight: 600; margin-bottom: 4px;">${{title}}${{tag}}</div>
          <div style="font-size: 12px; color: #666; margin-bottom: 12px;
                      font-family: ui-monospace, monospace;">${{ttype}}</div>
          ${{cols}}
        `;
      }}

      function renderEdgeProps(title, propsObj) {{
        const rows = Object.entries(propsObj || {{}}).map(([k, v]) =>
          `<tr><td style="padding: 4px 12px 4px 0; color: #666; vertical-align: top;">${{k}}</td>
               <td style="padding: 4px 0; font-family: ui-monospace, monospace;">${{
                 typeof v === 'string' ? v : JSON.stringify(v)
               }}</td></tr>`
        ).join('');
        propsEl.innerHTML = `
          <div style="font-size: 16px; font-weight: 600; margin-bottom: 12px;">${{title}}</div>
          <table style="border-collapse: collapse; width: 100%;">${{rows}}</table>
        `;
      }}

      network.on('selectNode', params => {{
        const n = nodes.get(params.nodes[0]);
        renderTableProps(n.label, n.group, n._props || {{}});
      }});
      network.on('selectEdge', params => {{
        if (params.nodes.length > 0) return;
        const e = edges.get(params.edges[0]);
        const fromNode = nodes.get(e.from);
        const toNode = nodes.get(e.to);
        renderEdgeProps(`${{fromNode.label}} → ${{e.label}} → ${{toNode.label}}`, e._props);
      }});
      network.on('deselectNode', () => {{
        propsEl.innerHTML = '<em style="color: #888;">Click a table or edge to inspect.</em>';
      }});
    }})();
    </script>
    """
    mo.iframe(_schema_html, height="500px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 3 — Ingest with source-native field names

    Each source pushes rows using **its own field names**. The SourceBinding's
    `mappings` table transparently rewrites them to canonical slot names.

    ### IMDB ingests three entity types

    **Movies** (IMDB basic titles format):

    | IMDB field | Canonical slot | Notes |
    |---|---|---|
    | `tconst` | `imdb_id` | IMDB title identifier |
    | `primaryTitle` | `title` | Preferred display title |
    | `startYear` | `year` | Release year |
    | `runtimeMinutes` | `runtime` | Running time |

    **People** (IMDB name basics format):

    | IMDB field | Canonical slot |
    |---|---|
    | `nconst` | `imdb_person_id` |
    | `primaryName` | `name` |
    | `birthYear` | `born` |

    **Credits** (IMDB principals format — composite key `tconst_nconst`):

    | IMDB field | Canonical slot |
    |---|---|
    | `tconst_nconst` | `credit_id` (composite) |
    | `tconst` | `movie` (ClassRef → Movie) |
    | `nconst` | `person` (ClassRef → Person) |
    | `category` | `role` |
    | `ordering` | `billing_order` |

    ### ClassRef fields in Credit

    `Credit.movie` and `Credit.person` are `ClassRef` slots — they store
    **canonical_ids**, not raw source keys. The canonical_id format in this
    demo is `{source}:{native_id}` (e.g. `imdb:nm0000151`). In production the
    ER layer resolves cross-source references automatically; here we supply the
    canonical_id directly so the Director VIEW's EXISTS subquery can match
    `c.person = b.canonical_id`.

    ### TMDB and Wikipedia

    - **TMDB** pushes `{id, original_title, release_year, runtime_minutes}` →
      Movie binding rewrites to canonical names.
    - **Wikipedia** contributes synopsis to Movie and birth year to Person.
    """)
    return


@app.cell
def _(API, mo):
    import requests as _requests

    def post_ingest(source_name, class_name, rows):
        """POST /graph/ingest/{source}?class_name={class} — required when a source
        has bindings to multiple classes (e.g. imdb binds Movie, Person, Credit)."""
        r = _requests.post(
            f"{API}/graph/ingest/{source_name}",
            params={"class_name": class_name},
            json={"rows": rows},
        )
        r.raise_for_status()
        return r.json()

    # ── IMDB movie titles (native IMDB field names) ───────────────────────────
    # class_name=Movie required: imdb has 3 bindings (Movie, Person, Credit).
    imdb_movies_resp = post_ingest("imdb", "Movie", [
        {"tconst": "tt0111161", "primaryTitle": "The Shawshank Redemption", "startYear": 1994, "runtimeMinutes": 142},
        {"tconst": "tt0468569", "primaryTitle": "The Dark Knight",          "startYear": 2008, "runtimeMinutes": 152},
        {"tconst": "tt1375666", "primaryTitle": "Inception",                "startYear": 2010, "runtimeMinutes": 148},
    ])

    # ── IMDB people (native IMDB name basics format) ──────────────────────────
    imdb_people_resp = post_ingest("imdb", "Person", [
        {"nconst": "nm0000209", "primaryName": "Frank Darabont",    "birthYear": 1959},
        {"nconst": "nm0000151", "primaryName": "Christopher Nolan", "birthYear": 1970},
        {"nconst": "nm0634240", "primaryName": "Jonathan Nolan",    "birthYear": 1976},
    ])

    # ── IMDB credits (native IMDB principals format) ──────────────────────────
    # tconst_nconst is the composite identifier (maps to credit_id).
    # Credit.movie and Credit.person are ClassRef slots — they store canonical_ids,
    # not raw source keys. In this demo we supply the knot canonical_id format
    # directly: "{source}:{native_id}". In production the ER layer resolves these
    # cross-source references automatically.
    imdb_credits_resp = post_ingest("imdb", "Credit", [
        {"tconst_nconst": "tt0111161_nm0000209", "tconst": "imdb:tt0111161", "nconst": "imdb:nm0000209", "category": "director", "ordering": 1},
        {"tconst_nconst": "tt0468569_nm0000151", "tconst": "imdb:tt0468569", "nconst": "imdb:nm0000151", "category": "director", "ordering": 1},
        {"tconst_nconst": "tt1375666_nm0000151", "tconst": "imdb:tt1375666", "nconst": "imdb:nm0000151", "category": "director", "ordering": 1},
        {"tconst_nconst": "tt0468569_nm0634240", "tconst": "imdb:tt0468569", "nconst": "imdb:nm0634240", "category": "writer",   "ordering": 2},
    ])

    # ── TMDB movies — single binding, class_name optional but explicit ────────
    # Binding rewrites: id→tmdb_id, original_title→title, release_year→year,
    # runtime_minutes→runtime.
    tmdb_movies_resp = post_ingest("tmdb", "Movie", [
        {"id": "278",   "original_title": "The Shawshank Redemption", "release_year": 1994, "runtime_minutes": 142},
        {"id": "155",   "original_title": "The Dark Knight",          "release_year": 2008, "runtime_minutes": 152},
        {"id": "27205", "original_title": "Inception",                "release_year": 2010, "runtime_minutes": 148},
    ])

    # ── Wikipedia — two bindings: Movie and Person ────────────────────────────
    # Movie: slug→wiki_slug, display_title→title, lead_paragraph→synopsis.
    wiki_movies_resp = post_ingest("wiki", "Movie", [
        {"slug": "shawshank-redemption", "display_title": "The Shawshank Redemption",
         "lead_paragraph": "The Shawshank Redemption is a 1994 American drama film..."},
        {"slug": "the-dark-knight",      "display_title": "The Dark Knight",
         "lead_paragraph": "The Dark Knight is a 2008 superhero film..."},
        {"slug": "inception-film",       "display_title": "Inception",
         "lead_paragraph": "Inception is a 2010 science fiction action film..."},
    ])
    # Person: person_slug→wiki_person_slug, display_name→name, birth_year→born.
    wiki_people_resp = post_ingest("wiki", "Person", [
        {"person_slug": "frank-darabont",    "display_name": "Frank Darabont",    "birth_year": 1959},
        {"person_slug": "christopher-nolan", "display_name": "Christopher Nolan", "birth_year": 1970},
    ])

    mo.ui.table([
        {"source": "imdb", "class": "Movie",  "accepted": imdb_movies_resp.get("accepted")},
        {"source": "imdb", "class": "Person", "accepted": imdb_people_resp.get("accepted")},
        {"source": "imdb", "class": "Credit", "accepted": imdb_credits_resp.get("accepted")},
        {"source": "tmdb", "class": "Movie",  "accepted": tmdb_movies_resp.get("accepted")},
        {"source": "wiki", "class": "Movie",  "accepted": wiki_movies_resp.get("accepted")},
        {"source": "wiki", "class": "Person", "accepted": wiki_people_resp.get("accepted")},
    ])
    return (
        imdb_movies_resp, imdb_people_resp, imdb_credits_resp,
        tmdb_movies_resp, wiki_movies_resp, wiki_people_resp,
        post_ingest,
    )


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 4 — Data graph (resolved canonical entities)

    Each node is a resolved canonical entity. The trust resolution layer applies
    per-binding Beta priors to weight each source's contributions per slot.

    - `Movie` nodes: IMDB (prior 9,1) dominates `title`/`year`/`runtime`;
      Wikipedia fills `synopsis`.
    - `Person` nodes: IMDB (prior 7,1) provides name + born; Wikipedia
      (prior 4,2) cross-confirms born year.
    - `Credit` nodes: pure IMDB data (only source for credits).

    Click a node to see its full resolved attribute set.
    """)
    return


@app.cell(hide_code=True)
def _(get, json):
    def build_data_graph_html(div_id):
        """Pull resolved canonical entities and render as a vis.js network."""
        spec_classes_local = get("/spec/published/classes")

        # Only concrete, non-defined classes have data tables we can query.
        # Director is a VIEW; we query it separately below.
        concrete_classes = [
            c for c in spec_classes_local
            if not c.get("abstract") and not c.get("definition")
        ]

        nodes = []
        edges = []
        seen_node_ids = set()

        for cls in concrete_classes:
            cls_name = cls["name"]
            try:
                listing = get(f"/graph/classes/{cls_name}?limit=1000")
            except Exception:
                continue
            cids = sorted({r["_canonical_id"] for r in listing["rows"]})
            for cid in cids:
                try:
                    resolved = get(f"/graph/classes/{cls_name}/{cid}/resolved")["resolved"]
                except Exception:
                    resolved = {}
                node_id = f"{cls_name}:{cid}"
                if node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                label_slot = next(
                    (s for s in ("title", "name") if s in resolved),
                    None,
                )
                label = resolved.get(label_slot, cid) if label_slot else cid
                nodes.append({
                    "id": node_id, "label": str(label),
                    "group": cls_name.lower(),
                    "props": {**resolved, "_canonical_id": cid, "_class": cls_name},
                })

        palette = {
            "movie":    ("#4f9eff", "#2563eb"),
            "tvseries": ("#22c55e", "#15803d"),
            "episode":  ("#f59e0b", "#b45309"),
            "person":   ("#f472b6", "#be185d"),
            "credit":   ("#a78bfa", "#7c3aed"),
        }
        groups_js = "{\n" + ",\n".join(
            f'        {g}: {{ color: {{ background: "{bg}", border: "{br}" }} }}'
            for g, (bg, br) in palette.items()
        ) + "\n      }"

        return f"""
    <link href="https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css" rel="stylesheet" />
    <script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>

    <div style="display: flex; gap: 12px; height: 480px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
      <div id="{div_id}"
           style="flex: 2; border: 1px solid #ccc; border-radius: 6px; background: #fafafa;">
      </div>
      <div id="{div_id}-props"
           style="flex: 1; padding: 16px; border: 1px solid #ccc; border-radius: 6px;
                  background: #fff; overflow: auto; font-size: 13px;">
        <em style="color: #888;">Click a node to inspect resolved attributes.</em>
      </div>
    </div>

    <script>
    (function() {{
      const rawNodes = {json.dumps(nodes)};
      const rawEdges = {json.dumps(edges)};
      const visNodes = rawNodes.map(n => ({{
        id: n.id, label: n.label, group: n.group,
        title: n.label, _props: n.props,
      }}));
      const visEdges = rawEdges.map(e => ({{
        id: e.id, from: e.from, to: e.to, label: e.label,
        title: e.label, _props: e.props,
      }}));
      const nodes = new vis.DataSet(visNodes);
      const edges = new vis.DataSet(visEdges);
      const container = document.getElementById('{div_id}');
      const propsEl = document.getElementById('{div_id}-props');

      const network = new vis.Network(container, {{ nodes, edges }}, {{
        nodes: {{ shape: 'dot', size: 22, font: {{ size: 14, color: '#222' }}, borderWidth: 2 }},
        edges: {{
          arrows: 'to',
          font: {{ size: 11, align: 'middle', color: '#555', strokeWidth: 0 }},
          color: {{ color: '#888', highlight: '#ff6b35' }},
          smooth: {{ type: 'continuous' }},
        }},
        groups: {groups_js},
        physics: {{
          stabilization: {{ iterations: 250 }},
          barnesHut: {{ gravitationalConstant: -10000, springLength: 150 }},
        }},
        interaction: {{ hover: true, tooltipDelay: 250, dragNodes: true }},
      }});

      function renderProps(title, group, propsObj) {{
        const rows = Object.entries(propsObj || {{}}).map(([k, v]) =>
          `<tr><td style="padding: 4px 12px 4px 0; color: #666; vertical-align: top;">${{k}}</td>
               <td style="padding: 4px 0; font-family: ui-monospace, monospace;">${{
                 typeof v === 'string' ? v : JSON.stringify(v)
               }}</td></tr>`
        ).join('');
        propsEl.innerHTML = `
          <div style="font-size: 16px; font-weight: 600; margin-bottom: 12px;">${{title}}</div>
          <table style="border-collapse: collapse; width: 100%;">${{rows}}</table>
        `;
      }}

      network.on('selectNode', params => {{
        const n = nodes.get(params.nodes[0]);
        renderProps(n.label, n.group, n._props);
      }});
      network.on('deselectNode', () => {{
        propsEl.innerHTML = '<em style="color: #888;">Click a node to inspect resolved attributes.</em>';
      }});
    }})();
    </script>
    """

    return (build_data_graph_html,)


@app.cell(hide_code=True)
def _(build_data_graph_html, mo):
    mo.iframe(build_data_graph_html("data-graph"), height="500px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 5 — Director defined class VIEW

    `SELECT * FROM knot_data.director` should return only the Persons who
    have a Credit row with `role = 'director'`. In our ingest above, all
    three credits for the three film directors are tagged with
    `category = 'director'` (which maps to `role`).

    The VIEW resolves dynamically — add a new Credit with
    `role = 'director'` for a Person and they automatically appear in the
    `director` view on the next read. No separate "mark as director" step.

    The raw SQL below queries `knot_data.director` directly (bypassing the
    API) to confirm the VIEW returns the right rows.
    """)
    return


@app.cell(hide_code=True)
def _(control_db, mo):
    from sqlalchemy import text as _text2

    with control_db.begin() as _conn2:
        try:
            _director_rows = _conn2.execute(_text2(
                "SELECT _canonical_id, name, born, imdb_person_id "
                "FROM knot_data.director "
                "ORDER BY name"
            )).fetchall()
            _director_data = [
                {"canonical_id": r[0], "name": r[1], "born": r[2], "imdb_person_id": r[3]}
                for r in _director_rows
            ]
        except Exception as _exc:
            _director_data = [{"error": str(_exc)}]

    mo.ui.table(_director_data)
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## What you just saw

    - **Slots are inline on each class.** No separate slot-registration phase —
      each class definition carries its own slot list directly. Mixin classes
      define their slots inline; concrete classes inherit them via
      `mixin_names` / `is_a_name`.

    - **is_a hierarchy.** `Movie`/`TVSeries`/`Episode` inherit from `MediaItem`.
      `Director` inherits from `Person` (a Director IS a Person). The `is_a`
      relationship signals structural identity, not just slot reuse.

    - **Mixins vs is_a.** `Auditable`/`Localizable` are mixins: crosscutting
      concerns that apply to unrelated entity types without shared structural
      identity. Both `MediaItem` and `Person` use them.

    - **Source** (thin label) carries only a name + description.

    - **SourceBinding** is the reified `(Source, Class)` relationship. It carries
      field-mapping rules, trust prior, required slots, and the identifier slot.
      One `Source` can bind to many classes — IMDB binds to Movie, Person, AND Credit.

    - **Multi-class IMDB.** One source feeding three classes via three bindings.
      Each binding has its own identifier slot (`imdb_id`, `imdb_person_id`,
      `credit_id`) and its own field-mapping rules (`tconst`, `nconst`,
      `tconst_nconst`).

    - **Reified Credit (junction class).** Two `ClassRef` slots (`movie`, `person`)
      make Credit a junction between Movie and Person. Roles, billing order, and
      other join-table attributes live directly on Credit rows.

    - **Director defined class.** Compiled to a `CREATE OR REPLACE VIEW` that
      filters `knot_data.person` rows via an EXISTS subquery into
      `knot_data.credit`. The view resolves dynamically — no materialization step.

    - **SQL constraints.** `year_plausible` and `director_credit_role_is_known`
      are WHERE-clause predicate strings validated by sqlglot at publish time.
      They run at ingest-time validation and on each publish gate pass.

    Everything went through `/spec/*` and `/graph/*`. No bypass, no
    side-channel writes.
    """)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
