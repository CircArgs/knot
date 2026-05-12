"""knot demo — Netflix ontology with Source + SourceBinding.

A runnable narrative that drives knot through its `/spec/*` and
`/graph/*` API. Demonstrates the Phase 2 Source/SourceBinding split:
Source is a thin label; the per-class metadata (identifier slot, field
mappings, trust priors) lives on SourceBinding.

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

    The spec has three layers:

    - **Mixins** (`Auditable`, `Localizable`) — abstract crosscutting concerns
      applied via `mixins=` rather than `is_a=`. A mixin is NOT a parent class;
      it is a named bundle of slots that multiple unrelated classes share.
    - **Abstract parent** (`MediaItem`) — groups the shared media slots and
      carries `is_a` children. `Movie`, `TVSeries`, and `Episode` each *are*
      a MediaItem (structural identity), so `is_a` is correct.
    - **Concrete children** (`Movie`, `TVSeries`, `Episode`) — each adds its
      own specific slots and will be backed by a `knot_data.<class>` table.

    **Why is_a vs mixin?**

    > `Movie`/`TVSeries`/`Episode` use `is_a MediaItem` because each
    > *structurally IS* a media item — they inherit the identity and share the
    > table structure.  `Auditable` and `Localizable` are *mixins* because
    > audit timestamps and locale lists are crosscutting concerns: they could
    > apply to completely different entity types (e.g. a `User`) without any
    > shared structural identity.

    **Sources and SourceBindings:**

    Three thin `Source` labels (imdb, tmdb, wiki) carry only a name and
    description. A `SourceBinding` reifies each (Source, Class) pair and
    carries the field-mapping rules and trust prior. IMDB binds to Movie
    with a strong prior (9,1); TMDB with a moderate prior (7,2); Wikipedia
    with a weaker prior (3,2) — it contributes synopsis but isn't authoritative
    for runtime or year.
    """)
    return


@app.cell
def _(mo):
    import json as _json

    SPEC = {
        # ── Mixin slots ────────────────────────────────────────────────────
        "mixin_slots": [
            {"name": "created_at",       "type_kind": "primitive", "type_name": "datetime"},
            {"name": "updated_at",       "type_kind": "primitive", "type_name": "datetime"},
            {"name": "default_locale",   "type_kind": "primitive", "type_name": "string"},
            {"name": "available_locales","type_kind": "array_of_primitive", "type_name": "string"},
        ],
        # ── Identifier slots (one per source) ──────────────────────────────
        "id_slots": [
            {"name": "imdb_id",   "type_kind": "primitive", "type_name": "string", "identifier": True},
            {"name": "tmdb_id",   "type_kind": "primitive", "type_name": "string", "identifier": True},
            {"name": "wiki_slug", "type_kind": "primitive", "type_name": "string", "identifier": True},
        ],
        # ── Shared media slots ─────────────────────────────────────────────
        "media_slots": [
            {"name": "title",    "type_kind": "primitive", "type_name": "string", "required": True},
            {"name": "year",     "type_kind": "primitive", "type_name": "integer"},
            {"name": "synopsis", "type_kind": "primitive", "type_name": "string"},
        ],
        # ── Class-specific slots ───────────────────────────────────────────
        "class_slots": [
            {"name": "runtime",        "type_kind": "primitive", "type_name": "integer"},
            {"name": "season_count",   "type_kind": "primitive", "type_name": "integer"},
            {"name": "episode_number", "type_kind": "primitive", "type_name": "integer"},
        ],
        # ── Mixin classes ──────────────────────────────────────────────────
        "mixin_classes": [
            {
                "name": "Auditable",
                "abstract": True,
                "slot_names": ["created_at", "updated_at"],
                "description": "Mixin: stamps any entity with audit timestamps.",
            },
            {
                "name": "Localizable",
                "abstract": True,
                "slot_names": ["default_locale", "available_locales"],
                "description": "Mixin: marks media artifacts as having localized content.",
            },
        ],
        # ── Abstract parent ────────────────────────────────────────────────
        "parent_class": [
            {
                "name": "MediaItem",
                "abstract": True,
                "slot_names": ["title", "year", "synopsis", "imdb_id", "tmdb_id", "wiki_slug"],
                "mixin_names": ["Auditable", "Localizable"],
                "description": "Abstract parent for Movie / TVSeries / Episode.",
            },
        ],
        # ── Concrete children ──────────────────────────────────────────────
        "child_classes": [
            {"name": "Movie",    "is_a_name": "MediaItem", "slot_names": ["runtime"],
             "description": "A theatrical or direct-to-streaming film."},
            {"name": "TVSeries", "is_a_name": "MediaItem", "slot_names": ["season_count"],
             "description": "A multi-season serialised show."},
            {"name": "Episode",  "is_a_name": "MediaItem", "slot_names": ["episode_number"],
             "description": "A single episode of a TVSeries."},
        ],
        # ── Sources (thin labels) ──────────────────────────────────────────
        "sources": [
            {"name": "imdb", "description": "IMDB ratings & metadata"},
            {"name": "tmdb", "description": "The Movie Database"},
            {"name": "wiki", "description": "Wikipedia"},
        ],
        # ── SourceBindings — one per (source, class) pair ─────────────────
        "source_bindings": [
            {
                "source_name": "imdb",
                "class_name": "Movie",
                "identifier_slot_name": "imdb_id",
                "trust_prior": [9.0, 1.0],
                "required_slot_names": ["imdb_id", "title"],
                "description": "IMDB → Movie: strong prior, native field names.",
                "mappings": [
                    {"slot_name": "imdb_id", "source_field": "imdb_id"},
                    {"slot_name": "title",   "source_field": "title",   "prior": [50.0, 1.0]},
                    {"slot_name": "year",    "source_field": "year"},
                    {"slot_name": "runtime", "source_field": "runtime"},
                ],
            },
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
    Each entry above is one POST. The build order matters: mixin slots
    before mixin classes (classes reference their slots by name), parent
    class after its mixin classes, child classes after the parent, sources
    before source bindings. The table below shows each call in order.
    """)
    return


@app.cell
def _(SPEC, mo, post, reset):
    reset()
    draft_id = post("/spec/drafts", {"label": "netflix-demo"})["revision"]

    # Ordered list of (phase_key, endpoint_segment) pairs.
    _PHASES = [
        ("mixin_slots",   "slots"),
        ("id_slots",      "slots"),
        ("media_slots",   "slots"),
        ("class_slots",   "slots"),
        ("mixin_classes", "classes"),
        ("parent_class",  "classes"),
        ("child_classes", "classes"),
        ("sources",       "sources"),
        ("source_bindings", "source_bindings"),
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

    Nodes are spec-graph entities (classes, slots, types, sources).
    Edges encode the structural relationships:

    - `class --has--> slot`
    - `slot --range--> type | class`
    - `source --of--> class`  *(binding target)*
    - `class --is_a--> class`  *(inheritance)*
    - `class --mixin--> class`  *(crosscutting)*

    Click any node to see its full attribute set on the right.
    """)
    return


@app.cell(hide_code=True)
def _(get):
    spec_classes = get("/spec/published/classes")
    spec_slots = get("/spec/published/slots")
    spec_sources = get("/spec/published/sources")
    spec_bindings = get("/spec/published/source_bindings")
    return spec_bindings, spec_classes, spec_slots, spec_sources


@app.cell(hide_code=True)
def _(spec_bindings, spec_classes, spec_slots, spec_sources):
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

    # Primitive types referenced by slots (infer from slot data)
    _seen_types: set = set()
    for _s in spec_slots:
        if _s["type_kind"] in ("primitive", "array_of_primitive") and _s["type_name"]:
            _seen_types.add(_s["type_name"])
    for _tn in sorted(_seen_types):
        _add_node("type", _tn, {"name": _tn, "kind": "primitive"})

    for _s in spec_slots:
        _add_node("slot", _s["name"], _s)
    for _c in spec_classes:
        _add_node("class", _c["name"], _c)
    for _src in spec_sources:
        _add_node("source", _src["name"], _src)

    # class --has--> slot
    for _c in spec_classes:
        for _slot_name in _c["slots"]:
            spec_edges.append({
                "id": f"has:{_c['name']}:{_slot_name}",
                "from": f"class:{_c['name']}",
                "to": f"slot:{_slot_name}",
                "label": "has",
                "props": {"class": _c["name"], "slot": _slot_name},
            })

    # slot --range--> type | class
    for _s in spec_slots:
        if _s["type_kind"] in ("primitive", "array_of_primitive") and _s["type_name"]:
            spec_edges.append({
                "id": f"range:{_s['name']}",
                "from": f"slot:{_s['name']}",
                "to": f"type:{_s['type_name']}",
                "label": "range",
                "props": {"range_kind": _s["type_kind"]},
            })
        elif _s["type_kind"] in ("class", "array_of_class") and _s["type_name"]:
            spec_edges.append({
                "id": f"range:{_s['name']}",
                "from": f"slot:{_s['name']}",
                "to": f"class:{_s['type_name']}",
                "label": "range",
                "props": {"range_kind": _s["type_kind"]},
            })

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

    # source --bound_to--> class (via source_bindings)
    for _b in spec_bindings:
        spec_edges.append({
            "id": f"binding:{_b['source_name']}:{_b['class_name']}",
            "from": f"source:{_b['source_name']}",
            "to": f"class:{_b['class_name']}",
            "label": f"binds ({_b['identifier_slot']})",
            "props": {
                "trust_prior": _b["trust_prior"],
                "required_slots": _b["required_slots"],
                "mappings": len(_b["mappings"]),
            },
        })

    return spec_edges, spec_nodes


@app.cell(hide_code=True)
def _(json, mo, spec_edges: list, spec_nodes: list):
    _palette = {
        "class":  ("#4f9eff", "#2563eb", "#dbeafe", "#1e40af"),
        "slot":   ("#22c55e", "#15803d", "#dcfce7", "#15803d"),
        "type":   ("#a78bfa", "#7c3aed", "#ede9fe", "#5b21b6"),
        "source": ("#f59e0b", "#b45309", "#fef3c7", "#92400e"),
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
    ## Step 2.5 — Tables generated from this spec

    Publishing the spec triggers DDL emission. Every **concrete** class
    (`Movie`, `TVSeries`, `Episode`) becomes two tables in `knot_data`:

    - `knot_data.<class>` — source-row table (one row per source contribution)
    - `knot_data.<class>_bindings` — SCD2 table tracking which `canonical_id`
      each source row currently maps to

    Abstract classes (`MediaItem`, `Auditable`, `Localizable`) do **not**
    get tables — they're compile-time constructs that expand their slots into
    concrete children.

    The resulting `knot_data.movie` source-row table looks like:

    | canonical_id | _source | imdb_id | tmdb_id | wiki_slug | title | year | synopsis | runtime |
    |---|---|---|---|---|---|---|---|---|
    | *(assigned by ER)* | imdb | tt0111161 | — | — | Shawshank Redemption | 1994 | — | 142 |
    | *(same canonical_id)* | tmdb | — | 278 | — | The Shawshank Redemption | 1994 | — | 142 |
    | *(same canonical_id)* | wiki | — | — | shawshank-redemption | The Shawshank Redemption | — | "One of the best..." | — |

    Each source contributes a row under its own `_source` key. The ER layer
    merges them into a single canonical entity; per-slot trust resolution
    picks the winning value per slot using the Beta posteriors from each
    binding's `trust_prior`.

    Click any table for its column list.
    """)
    return


@app.cell(hide_code=True)
def _(control_db, get, json, mo):
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
    `mappings` table transparently rewrites them to canonical slot names:

    - **IMDB** pushes `{imdb_id, title, year, runtime}` — native names match
      the canonical slots, so no renaming needed.
    - **TMDB** pushes `{id, original_title, release_year, runtime_minutes}` —
      the binding rewrites `id→tmdb_id`, `original_title→title`,
      `release_year→year`, `runtime_minutes→runtime`.
    - **Wikipedia** pushes `{slug, display_title, lead_paragraph}` — contributes
      only a subset of slots; `slug→wiki_slug`, `display_title→title`,
      `lead_paragraph→synopsis`. No `year` or `runtime` — that's fine, wiki
      just doesn't claim those values (NO_CLAIM null semantics).

    All three source rows for "The Shawshank Redemption" land in
    `knot_data.movie` bound to the same `canonical_id` once ER merges them.
    """)
    return


@app.cell
def _(post):
    # IMDB — native field names pass straight through the mapping.
    post("/graph/ingest/imdb", {"rows": [
        {"imdb_id": "tt0111161", "title": "Shawshank Redemption",      "year": 1994, "runtime": 142},
        {"imdb_id": "tt0468569", "title": "The Dark Knight",           "year": 2008, "runtime": 152},
        {"imdb_id": "tt1375666", "title": "Inception",                 "year": 2010, "runtime": 148},
    ]})

    # TMDB — binding rewrites: id→tmdb_id, original_title→title,
    #         release_year→year, runtime_minutes→runtime.
    post("/graph/ingest/tmdb", {"rows": [
        {"id": "278",  "original_title": "The Shawshank Redemption", "release_year": 1994, "runtime_minutes": 142},
        {"id": "155",  "original_title": "The Dark Knight",          "release_year": 2008, "runtime_minutes": 152},
        {"id": "27205","original_title": "Inception",                "release_year": 2010, "runtime_minutes": 148},
    ]})

    # Wikipedia — subset only: slug→wiki_slug, display_title→title,
    #             lead_paragraph→synopsis. No year/runtime claim.
    post("/graph/ingest/wiki", {"rows": [
        {"slug": "shawshank-redemption", "display_title": "The Shawshank Redemption",
         "lead_paragraph": "The Shawshank Redemption is a 1994 American drama film..."},
        {"slug": "the-dark-knight",      "display_title": "The Dark Knight",
         "lead_paragraph": "The Dark Knight is a 2008 superhero film..."},
        {"slug": "inception-film",       "display_title": "Inception",
         "lead_paragraph": "Inception is a 2010 science fiction action film..."},
    ]})
    return


@app.cell(hide_code=True)
def _(get, json):
    def build_data_graph_html(div_id):
        """Pull resolved canonical entities and render as a vis.js network."""
        spec_classes_local = get("/spec/published/classes")
        spec_slots_local = get("/spec/published/slots")

        slot_index = {s["name"]: s for s in spec_slots_local}

        # Only concrete classes (non-abstract) have data tables.
        concrete_classes = [c for c in spec_classes_local if not c.get("abstract")]

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
def _(mo):
    mo.md("""
    ## Step 4 — Data graph

    Each blue node is a resolved `Movie` canonical entity. The resolved
    attributes reflect per-slot trust resolution: where IMDB and TMDB agree
    on `year` and `runtime`, the posterior mean of their contributions wins.
    Wikipedia's `synopsis` fills in a slot that IMDB and TMDB don't claim.

    Click a node to see its full resolved attribute set.
    """)
    return


@app.cell(hide_code=True)
def _(build_data_graph_html, mo):
    mo.iframe(build_data_graph_html("data-graph"), height="500px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## What you just saw

    - **Source** (thin label) carries only a name + description.
      It is NOT the owner of the binding relationship.
    - **SourceBinding** is the reified `(Source, Class)` relationship.
      It carries the field-mapping rules (`id → tmdb_id`), the trust prior
      (`Beta(7, 2)` for TMDB), the required slots, and the identifier slot.
    - **Field-mapping** lets each source push using its native schema without
      requiring the source system to adopt knot's slot names.
    - **Trust resolution** uses per-binding Beta priors to weight each source's
      contributions per slot. The slot-level `prior` in a `SlotMapping` can
      further tune a specific slot (e.g. IMDB's title gets `Beta(50,1)` because
      IMDB titles are highly reliable).
    - **Abstract classes** (`Auditable`, `Localizable`, `MediaItem`) are
      compile-time only — they expand their slots into concrete children
      (`Movie`, `TVSeries`, `Episode`) but produce no data tables.

    Everything went through `/spec/*` and `/graph/*`. No bypass, no
    side-channel writes.
    """)
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
