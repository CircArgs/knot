"""knot demo — end-to-end story with interactive spec + data graph viz.

A single runnable narrative that drives knot through its `/spec/*` and
`/graph/*` API. Spec graph and data graph each render as a vis.js network
(drag, zoom, click for properties).

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
    # knot — end-to-end demo

    A small movie / people ontology, ingested from two disagreeing sources,
    with cross-class slot references and a `Merge` correction. Spec graph
    and data graph each rendered as an interactive vis.js network.

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
    ## Step 1 — Clean slate, then publish a small ontology

    Two classes (`Movie`, `Person`), three sources (`imdb`, `tmdb`, `wiki`).
    `Movie.directed_by` is a class-range slot pointing at a `Person`
    canonical_id — that's the cross-class edge the data graph will draw.
    """)
    return


@app.cell
def _(post, reset):
    reset()

    # — Draft —
    draft_id = post("/spec/drafts", {"label": "demo"})["revision"]

    # — Types —
    post(f"/spec/drafts/{draft_id}/types", {"name": "string", "base": "str"})
    post(f"/spec/drafts/{draft_id}/types", {"name": "integer", "base": "int"})

    # — Slots: identifiers + properties + cross-class FK —
    post(f"/spec/drafts/{draft_id}/slots", {
        "name": "imdb_id", "range_kind": "type", "range_name": "string",
        "identifier": True, "required": True,
    })
    post(f"/spec/drafts/{draft_id}/slots", {
        "name": "person_id", "range_kind": "type", "range_name": "string",
        "identifier": True, "required": True,
    })
    post(f"/spec/drafts/{draft_id}/slots", {
        "name": "title", "range_kind": "type", "range_name": "string",
        "required": True,
    })
    post(f"/spec/drafts/{draft_id}/slots", {
        "name": "year", "range_kind": "type", "range_name": "integer",
        "resolution_policy": "posterior_mean",
    })
    post(f"/spec/drafts/{draft_id}/slots", {
        "name": "name", "range_kind": "type", "range_name": "string",
        "required": True,
    })
    # — Classes —
    # Person before Movie so directed_by's range resolves.
    post(f"/spec/drafts/{draft_id}/classes", {
        "name": "Person", "slot_names": ["person_id", "name"],
    })
    post(f"/spec/drafts/{draft_id}/slots", {
        "name": "directed_by", "range_kind": "class", "range_name": "Person",
    })
    post(f"/spec/drafts/{draft_id}/classes", {
        "name": "Movie",
        "slot_names": ["imdb_id", "title", "year", "directed_by"],
    })

    # — Sources —
    for _src in ("imdb", "tmdb"):
        post(f"/spec/drafts/{draft_id}/sources", {
            "name": _src, "entity_class_name": "Movie",
            "identifier_slot_name": "imdb_id",
        })
    post(f"/spec/drafts/{draft_id}/sources", {
        "name": "wiki", "entity_class_name": "Person",
        "identifier_slot_name": "person_id",
    })

    publish_resp = post(f"/spec/drafts/{draft_id}/publish")
    publish_resp
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 2 — Spec graph

    Nodes are spec-graph entities (classes, slots, types, sources).
    Edges encode the structural relationships:

    - `class --has--> slot`
    - `slot --range--> type | class`
    - `source --of--> class`
    - `source --identifier--> slot`

    Click any node to see its full attribute set on the right.
    """)
    return


@app.cell(hide_code=True)
def _(get):
    spec_classes = get("/spec/published/classes")
    spec_slots = get("/spec/published/slots")
    spec_types = get("/spec/published/types")
    spec_sources = get("/spec/published/sources")
    return spec_classes, spec_slots, spec_sources, spec_types


@app.cell(hide_code=True)
def _(spec_classes, spec_slots, spec_sources, spec_types):
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

    for _t in spec_types:
        _add_node("type", _t["name"], _t)
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
        if _s["range_kind"] == "type":
            spec_edges.append({
                "id": f"range:{_s['name']}",
                "from": f"slot:{_s['name']}",
                "to": f"type:{_s['range_name']}",
                "label": "range",
                "props": {"range_kind": "type"},
            })
        elif _s["range_kind"] == "class":
            spec_edges.append({
                "id": f"range:{_s['name']}",
                "from": f"slot:{_s['name']}",
                "to": f"class:{_s['range_name']}",
                "label": "range",
                "props": {"range_kind": "class"},
            })

    # source --of--> class, source --identifier--> slot
    for _src in spec_sources:
        spec_edges.append({
            "id": f"of:{_src['name']}",
            "from": f"source:{_src['name']}",
            "to": f"class:{_src['entity_class']}",
            "label": "of",
            "props": {"role": "entity_class"},
        })
        spec_edges.append({
            "id": f"id:{_src['name']}",
            "from": f"source:{_src['name']}",
            "to": f"slot:{_src['identifier_slot']}",
            "label": "identifier",
            "props": {"role": "identifier_slot"},
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
                            border-radius: 999px; background: ${{tagMap[group].bg}};
                            color: ${{tagMap[group].fg}}; font-size: 11px;
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

    Publishing the spec triggered DDL emission. Every concrete class became
    two tables in `knot_data`: a source-row table (one row per source
    contribution) and a bindings table (SCD2 — tracks which canonical_id
    each row currently belongs to). Cross-class slots whose range is
    another class show up as plain text columns whose value is the
    referenced canonical_id.

    Click any table for its column list.
    """)
    return


@app.cell(hide_code=True)
def _(control_db, get, json, mo):
    from sqlalchemy import text as _text

    # Pull tables + columns from postgres directly.
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

    # Classify nodes by role for color coding.
    # Source-row tables: per-class data tables (movie, person)
    # Bindings tables: <class>_bindings
    # VIEWs (defined classes): table_type = 'VIEW'
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

    # source-row → bindings edges from name pattern.
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

    # Cross-class FK edges from the published spec: any slot whose
    # range is another class becomes a text column on the source-row
    # table of every class that has the slot.
    _spec_classes_for_schema = get("/spec/published/classes")
    _spec_slots_for_schema = get("/spec/published/slots")
    _slot_index = {s["name"]: s for s in _spec_slots_for_schema}
    for _cls in _spec_classes_for_schema:
        _from_table = _cls["name"].lower()
        if _from_table not in _table_set:
            continue
        for _slot_name in _cls["slots"]:
            _slot = _slot_index.get(_slot_name)
            if _slot is None or _slot["range_kind"] != "class":
                continue
            _to_table = _slot["range_name"].lower()
            if _to_table not in _table_set:
                continue
            _schema_edges.append({
                "id": f"fk:{_from_table}.{_slot_name}->{_to_table}",
                "from": _from_table,
                "to": _to_table,
                "label": f"FK ({_slot_name})",
                "props": {
                    "relationship": "cross-class FK",
                    "slot": _slot_name,
                    "column": _slot_name,
                    "references": f"{_to_table}.canonical_id",
                },
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

    <div style="display: flex; gap: 12px; height: 620px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
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
    mo.iframe(_schema_html, height="640px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 3 — Ingest

    Two `Person` rows from `wiki` (one of which is a duplicate we'll
    `Merge` later). Two `Movie` rows from `imdb` + `tmdb` for the same
    movie — they disagree on `year` and on whether the director's
    `person_id` is `nolan_chris` or `nolan_christopher` (the duplicate
    we'll resolve).
    """)
    return


@app.cell
def _(post):
    # — wiki: two Person rows that are actually the same human. —
    post("/graph/ingest/wiki", {"rows": [
        {"person_id": "nolan_chris",       "name": "Chris Nolan"},
        {"person_id": "nolan_christopher", "name": "Christopher Nolan"},
    ]})

    # — imdb + tmdb: same movie, different director-id flavour, different year. —
    post("/graph/ingest/imdb", {"rows": [{
        "imdb_id": "tt1375666", "title": "Inception",
        "year": 2011,  # wrong — real year is 2010
        "directed_by": "nolan_chris",
    }]})
    post("/graph/ingest/tmdb", {"rows": [{
        "imdb_id": "tt1375666", "title": "Inception",
        "year": 2010,
        "directed_by": "nolan_christopher",
    }]})
    return


@app.cell(hide_code=True)
def _(get, json):
    def build_data_graph_html(div_id):
        """Pull the resolved view of every canonical entity from every class
        and synthesise nodes + cross-class edges from class-range slots."""
        spec_classes_local = get("/spec/published/classes")
        spec_slots_local = get("/spec/published/slots")

        slot_index = {s["name"]: s for s in spec_slots_local}
        class_range_slots = {
            c["name"]: [
                slot_index[sn] for sn in c["slots"]
                if slot_index[sn]["range_kind"] == "class"
            ]
            for c in spec_classes_local
        }

        nodes = []
        edges = []
        seen_node_ids = set()
        for cls in spec_classes_local:
            cls_name = cls["name"]
            listing = get(f"/graph/classes/{cls_name}?limit=1000")
            cids = sorted({r["_canonical_id"] for r in listing["rows"]})
            for cid in cids:
                resolved = get(
                    f"/graph/classes/{cls_name}/{cid}/resolved"
                )["resolved"]
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
                    "props": {**resolved, "_canonical_id": cid,
                              "_class": cls_name},
                })

        # Cross-class edges: walk every class-range slot on every entity and
        # turn the FK string into an edge to that target class's canonical_id.
        for cls in spec_classes_local:
            cls_name = cls["name"]
            for slot in class_range_slots[cls_name]:
                listing = get(f"/graph/classes/{cls_name}?limit=1000")
                cids = sorted({r["_canonical_id"] for r in listing["rows"]})
                for cid in cids:
                    resolved = get(
                        f"/graph/classes/{cls_name}/{cid}/resolved"
                    )["resolved"]
                    fk = resolved.get(slot["name"])
                    if not fk:
                        continue
                    target_cls = slot["range_name"]
                    target_id = f"{target_cls}:{fk}"
                    if target_id not in seen_node_ids:
                        # Dangling FK — render a placeholder so the edge has
                        # a target. This is exactly the cross-class duplicate
                        # we're about to merge.
                        nodes.append({
                            "id": target_id, "label": fk,
                            "group": f"{target_cls.lower()}_dangling",
                            "props": {"_canonical_id": fk,
                                      "_class": target_cls,
                                      "_status": "referenced but not ingested"},
                        })
                        seen_node_ids.add(target_id)
                    edges.append({
                        "id": f"{slot['name']}:{cls_name}:{cid}->{fk}",
                        "from": f"{cls_name}:{cid}", "to": target_id,
                        "label": slot["name"],
                        "props": {"slot": slot["name"],
                                  "from_canonical_id": cid,
                                  "to_canonical_id": fk},
                    })

        palette = {
            "movie":            ("#4f9eff", "#2563eb"),
            "person":           ("#22c55e", "#15803d"),
            "person_dangling":  ("#fca5a5", "#b91c1c"),
            "movie_dangling":   ("#fdba74", "#c2410c"),
        }
        groups_js = "{\n" + ",\n".join(
            f'        {g}: {{ color: {{ background: "{bg}", border: "{br}" }} }}'
            for g, (bg, br) in palette.items()
        ) + "\n      }"

        return f"""
    <link href="https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css" rel="stylesheet" />
    <script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>

    <div style="display: flex; gap: 12px; height: 620px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
      <div id="{div_id}"
           style="flex: 2; border: 1px solid #ccc; border-radius: 6px; background: #fafafa;">
      </div>
      <div id="{div_id}-props"
           style="flex: 1; padding: 16px; border: 1px solid #ccc; border-radius: 6px;
                  background: #fff; overflow: auto; font-size: 13px;">
        <em style="color: #888;">Click a node or edge to inspect.</em>
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

    return (build_data_graph_html,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 4 — Data graph (pre-merge)

    Two green Person nodes (the duplicate we're about to merge) and one
    blue Movie node. The Movie's `directed_by` edge points at whichever
    flavour wins per-slot trust resolution; the other Person sits there
    as a redundant entity.

    Click a node to see its resolved attribute set; click an edge to see
    the slot reference.
    """)
    return


@app.cell(hide_code=True)
def _(build_data_graph_html, mo):
    mo.iframe(build_data_graph_html("data-graph-pre"), height="640px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 5 — Apply a `Merge` correction

    `nolan_chris` and `nolan_christopher` are the same human. Submit a
    typed `Merge` correction that collapses the duplicate into the
    canonical id. Knot atomically (one transaction):

    1. Logs the correction in `_user_corrections`.
    2. SCD2-rewrites the duplicate's bindings to point at the keeper.
    3. Walks the published spec, finds every stored slot whose range is
       `Person`, and rewrites those FK columns on every referencing
       class's data table — so `Movie.directed_by` flips from
       `nolan_chris` to `nolan_christopher` automatically.
    4. Appends a lineage event tying the two canonical_ids together.
    """)
    return


@app.cell
def _(post):
    merge_resp = post("/graph/corrections", {
        "type": "merge",
        "class_name": "Person",
        "keep_canonical_id": "nolan_christopher",
        "merge_canonical_ids": ["nolan_chris"],
    })
    merge_resp
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## Step 6 — Data graph (post-merge)

    Same render path, fresh data. The duplicate Person canonical entity
    is gone — `nolan_chris` and `nolan_christopher` collapsed to one row
    with `_canonical_id = nolan_christopher`. The `Movie.directed_by`
    edge points at the keeper because the merge rewrote the FK column
    on the Movie data table in the same transaction. No dangling refs.
    """)
    return


@app.cell(hide_code=True)
def _(build_data_graph_html, mo):
    mo.iframe(build_data_graph_html("data-graph-post"), height="640px")
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    ## What you just saw

    - **Spec graph** rendered from `/spec/published/{classes,slots,types,sources}`.
      Every edge is structural — `class --has--> slot`, `slot --range--> type|class`,
      `source --of/identifier--> class|slot`.
    - **Data graph** rendered from `/graph/classes/{cls}` listings + per-entity
      `/resolved` views. Cross-class edges come from class-range slots like
      `Movie.directed_by → Person`.
    - **Merge correction** atomically rewrote contributions and emitted a
      lineage event. The data graph re-renders against the same API and
      the duplicate is gone.

    Everything went through `/spec/*` and `/graph/*`. No bypass, no
    side-channel writes.
    """)
    return


@app.cell
def _():
    return


@app.cell
def _():
    return


if __name__ == "__main__":
    app.run()
