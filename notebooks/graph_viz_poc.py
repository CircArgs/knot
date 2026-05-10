"""Toy interactive graph viz — proof of concept.

Pure vis.js via inline HTML; no Python dep beyond marimo.
Drag nodes around, click any node or edge to see its properties.

Run: marimo edit notebooks/graph_viz_poc.py
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="full")


@app.cell
def _():
    import json

    import marimo as mo

    return json, mo


@app.cell
def _(mo):
    mo.md("""
    # Interactive Graph — Toy

    Click any node or edge. Drag to rearrange. Scroll to zoom.
    """)
    return


@app.cell
def _():
    nodes = [
        {
            "id": 1,
            "label": "Inception",
            "group": "movie",
            "props": {"year": 2010, "rating": 8.8, "runtime_min": 148, "studio": "Warner Bros."},
        },
        {
            "id": 2,
            "label": "The Matrix",
            "group": "movie",
            "props": {"year": 1999, "rating": 8.7, "runtime_min": 136, "studio": "Warner Bros."},
        },
        {
            "id": 3,
            "label": "The Matrix Reloaded",
            "group": "movie",
            "props": {"year": 2003, "rating": 7.2, "runtime_min": 138, "studio": "Warner Bros."},
        },
        {
            "id": 4,
            "label": "Christopher Nolan",
            "group": "person",
            "props": {"born": 1970, "country": "UK", "primary_role": "director"},
        },
        {
            "id": 5,
            "label": "Leonardo DiCaprio",
            "group": "person",
            "props": {"born": 1974, "country": "USA", "primary_role": "actor"},
        },
        {
            "id": 6,
            "label": "Keanu Reeves",
            "group": "person",
            "props": {"born": 1964, "country": "Canada", "primary_role": "actor"},
        },
        {
            "id": 7,
            "label": "Lana Wachowski",
            "group": "person",
            "props": {"born": 1965, "country": "USA", "primary_role": "director"},
        },
        {
            "id": 8,
            "label": "Lilly Wachowski",
            "group": "person",
            "props": {"born": 1967, "country": "USA", "primary_role": "director"},
        },
    ]

    edges = [
        {"id": "e1", "from": 4, "to": 1, "label": "directed", "props": {"year": 2010}},
        {
            "id": "e2",
            "from": 5,
            "to": 1,
            "label": "acted_in",
            "props": {"role": "Cobb", "billing": 1},
        },
        {"id": "e3", "from": 7, "to": 2, "label": "directed", "props": {"year": 1999}},
        {"id": "e4", "from": 8, "to": 2, "label": "directed", "props": {"year": 1999}},
        {
            "id": "e5",
            "from": 6,
            "to": 2,
            "label": "acted_in",
            "props": {"role": "Neo", "billing": 1},
        },
        {"id": "e6", "from": 7, "to": 3, "label": "directed", "props": {"year": 2003}},
        {"id": "e7", "from": 8, "to": 3, "label": "directed", "props": {"year": 2003}},
        {
            "id": "e8",
            "from": 6,
            "to": 3,
            "label": "acted_in",
            "props": {"role": "Neo", "billing": 1},
        },
        {
            "id": "e9",
            "from": 3,
            "to": 2,
            "label": "sequel_of",
            "props": {"chronology": "direct sequel"},
        },
    ]
    return edges, nodes


@app.cell
def _(edges, json, mo, nodes):
    html = f"""
    <link href="https://unpkg.com/vis-network@9.1.6/styles/vis-network.min.css" rel="stylesheet" />
    <script src="https://unpkg.com/vis-network@9.1.6/standalone/umd/vis-network.min.js"></script>

    <div style="display: flex; gap: 12px; height: 620px; font-family: -apple-system, BlinkMacSystemFont, sans-serif;">
      <div id="knot-graph"
           style="flex: 2; border: 1px solid #ccc; border-radius: 6px; background: #fafafa;">
      </div>
      <div id="knot-props"
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
        id: n.id,
        label: n.label,
        group: n.group,
        title: n.label,
        _props: n.props,
      }}));
      const visEdges = rawEdges.map(e => ({{
        id: e.id,
        from: e.from,
        to: e.to,
        label: e.label,
        title: e.label,
        _props: e.props,
      }}));

      const nodes = new vis.DataSet(visNodes);
      const edges = new vis.DataSet(visEdges);
      const container = document.getElementById('knot-graph');
      const propsEl = document.getElementById('knot-props');

      const options = {{
        nodes: {{
          shape: 'dot',
          size: 22,
          font: {{ size: 14, color: '#222' }},
          borderWidth: 2,
        }},
        edges: {{
          arrows: 'to',
          font: {{ size: 11, align: 'middle', color: '#555', strokeWidth: 0 }},
          color: {{ color: '#888', highlight: '#ff6b35' }},
          smooth: {{ type: 'continuous' }},
        }},
        groups: {{
          movie:  {{ color: {{ background: '#4f9eff', border: '#2563eb' }} }},
          person: {{ color: {{ background: '#22c55e', border: '#15803d' }} }},
        }},
        physics: {{
          stabilization: {{ iterations: 200 }},
          barnesHut: {{ gravitationalConstant: -8000, springLength: 130 }},
        }},
        interaction: {{ hover: true, tooltipDelay: 250, dragNodes: true }},
      }};

      const network = new vis.Network(container, {{ nodes, edges }}, options);

      function renderProps(title, group, propsObj) {{
        const tag = group ? `<span style="display:inline-block; padding: 2px 8px; border-radius: 999px;
                            background: ${{group === 'movie' ? '#dbeafe' : '#dcfce7'}};
                            color: ${{group === 'movie' ? '#1e40af' : '#15803d'}};
                            font-size: 11px; margin-left: 8px;">${{group}}</span>` : '';
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
    mo.iframe(html, height="640px")
    return


@app.cell
def _(mo):
    mo.md("""
    ---
    Notes:
    - **Drag** any node to reposition; physics simulation will settle the rest.
    - **Scroll** inside the graph to zoom.
    - **Click** a node or edge to see its properties on the right.
    - Node colors: blue = movie, green = person.
    """)
    return


if __name__ == "__main__":
    app.run()
