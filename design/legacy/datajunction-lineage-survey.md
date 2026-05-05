---
title: DataJunction lineage survey
status: note
tags: [research, datajunction, reference, lineage]
project: knot
created_at: 2026-04-27T22:45:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# DataJunction lineage survey

Reference clone at `~/code/knot/refs/datajunction/`. Survey informs
Knot's lineage architecture.

## Overview

DJ has **two distinct lineage layers** that coexist:

1. **Node-level dependency graph** (the "DAG"). The set of source /
   transform / dimension / metric / cube nodes plus three edge tables:
   - `noderelationship` — parent/child between a `NodeRevision` and a
     `Node` (a transform's parents are the nodes referenced in its
     SQL). Defined in
     `datajunction-server/datajunction_server/database/node.py:151`
     (`class NodeRelationship`).
   - `dimensionlink` — a node's column joins to a dimension node (the
     "dimension graph"). Defined in
     `datajunction-server/datajunction_server/database/dimensionlink.py:23`
     (`class DimensionLink`).
   - `column.dimension_id` — direct column→dimension reference (a
     soft FK). See
     `datajunction-server/datajunction_server/database/column.py:44`.
2. **Column-level lineage**. A nested tree per output column,
   computed from the node's compiled SQL AST and persisted as JSON on
   `NodeRevision.lineage`. See
   `datajunction-server/datajunction_server/database/node.py:1302`.

Almost every "DAG" function the codebase exposes (upstream /
downstream / dimensions graph / impact propagation) operates on the
node-level layer. The column-level layer is a separate, narrower
artifact used mostly by the UI's `NodeColumnLineage` tab.

## Granularity

**Both, with very different mechanisms:**

- **Node-level.** This *is* the node graph itself — there is no
  separate "lineage graph" object. `NodeRelationship` rows are
  written when a node is created/revised by the parser populating
  `NodeRevision.parents`
  (`datajunction-server/datajunction_server/database/node.py:1256`,
  `parents: Mapped[List["Node"]] = relationship(... secondary=
  "noderelationship", primaryjoin="NodeRevision.id==NodeRelationship.
  child_id", secondaryjoin="Node.id==NodeRelationship.parent_id")`).
- **Column-level.** Tree of `LineageColumn` records, one per output
  column, each carrying `column_name`, `node_name`, `node_type`,
  `display_name`, and a recursive `lineage: List[LineageColumn]`
  (`datajunction-server/datajunction_server/models/node.py:1139-1154`).
  Built by walking the SQL AST: each `ast.Column` is followed back
  through aliases / expressions to the source `dj_node`. Logic in
  `datajunction-server/datajunction_server/internal/nodes.py:2197-2290`
  (`async def column_lineage`).

There's a third "graph" — the **dimensions DAG** — used to enumerate
which dimensions are reachable from a fact/transform. It rides on
`DimensionLink` plus the `column.dimension_id` shortcut and is
treated as lineage-adjacent metadata for query construction rather
than provenance lineage. See
`datajunction-server/datajunction_server/sql/dag.py:434` (recursive
CTE `get_dimension_nodes`) and `…/sql/dag.py:529`
(`_get_dimensions_dag_bfs`, the iterative replacement).

## Computation timing

- **Node-level edges.** Written **at node-creation / node-revision
  time**, synchronously as part of the `create`/`upsert`/`update`
  transactions. The parser produces `NodeRevision.parents`, and
  SQLAlchemy persists the join table. No separate ingestion path.
- **Column-level lineage.** Computed **after creation**, in a
  FastAPI **`BackgroundTask`** kicked off from the upsert/update
  flow — see
  `datajunction-server/datajunction_server/internal/nodes.py:1308-1311`
  (`background_tasks.add_task(save_column_level_lineage,
  node_revision_id=new_revision.id)`). It can also be (re)computed
  **lazily on request**: the GET endpoint falls through to
  `get_column_level_lineage(...)` if `node.current.lineage` is empty
  (`datajunction-server/datajunction_server/api/nodes.py:1523-1525`).
- **Dimensions DAG.** Computed **on every request** (no
  materialization), via either a Postgres recursive CTE or — newer —
  layered BFS in Python issuing one SQL per depth.

## Storage

- **Node graph edges:** rows in `noderelationship` (parent_id,
  child_id) plus `dimensionlink` and `column.dimension_id`. Two
  indexes are declared on `noderelationship`
  (`idx_noderelationship_parent_id`, `idx_noderelationship_child_id`,
  `…/database/node.py:158-160`). The whole graph is reconstructable
  from these tables; nothing is denormalized.
- **Column-level lineage:** materialised as a JSON column on
  `NodeRevision.lineage` (`Mapped[Optional[List[Dict]]]`,
  `database/node.py:1302-1305`). This is intentional caching — the
  recursive `column_lineage` walk is expensive — but it's a stale
  copy that's only refreshed on revision update.
- **No dedicated lineage table.** There is no separate
  `lineage_edges` / `lineage_nodes` schema. The node graph **is** the
  lineage at table granularity.

## API exposure

REST and GraphQL are both wired up. No GraphViz/dot output anywhere —
the UI assembles its own React-Flow nodes/edges client-side.

- **REST (column-level)**: `GET /nodes/{name}/lineage/`,
  `datajunction-server/datajunction_server/api/nodes.py:1491-1525`.
  Response: `List[LineageColumn]`, the recursive nested-tree shape
  defined in `models/node.py:1139`. (Note: each top-level entry is
  one of the node's output columns; deeper levels are upstream
  columns it derives from.)
- **REST (dimension graph for a node)**:
  `…/api/nodes.py:1469-1488` returns a `DimensionDAGOutput` with
  inbound/outbound nodes plus `inbound_edges`/`outbound_edges`
  (flat edge list `{source, target}`). Built by
  `get_dimension_inbound_bfs` / `get_dimension_outbound_bfs` at
  `…/sql/dag.py:1613` and `…/sql/dag.py:1693`.
- **GraphQL queries** (`api/graphql/queries/dag.py`):
  `upstreamNodes(node_names, node_type, include_deactivated)`,
  `downstreamNodes(...)`, `commonDimensions(nodes)`. Schema in
  `…/api/graphql/schema.graphql:586-614`. They return full `Node`
  objects (deduped) — the caller selects whichever sub-fields it
  wants. No edge list; the client infers edges from each node's
  `parents` field.
- **Internal helpers** (`sql/dag.py`): `get_upstream_nodes`,
  `get_downstream_nodes`, `get_dimensions_dag`,
  `get_filter_only_dimensions`, `get_common_dimensions`,
  `get_metric_parents_map`, `topological_sort`,
  `get_dimension_dag_indegree`, `get_cubes_using_dimensions`,
  `get_nodes_with_common_dimensions`. These are the building blocks
  the API endpoints and the deployment / impact analysis call.
- **Impact propagation**: not lineage *per se*, but a downstream BFS
  with revalidation. Used at deploy time to predict which downstream
  nodes will break.
  `…/internal/impact.py:61` (`propagate_impact`) walks
  `noderelationship` (Phase 1) and re-validates each downstream node
  level-by-level (Phase 3). Returns `List[DownstreamImpact]` with
  `caused_by`, `depth`, `impact_type` (WILL_INVALIDATE /
  WILL_RECOVER / MAY_AFFECT / UNCHANGED). Defined in
  `…/models/impact.py:12-32`.
- **UI**: React + reactflow. `NodeLineageTab.jsx` calls
  `node_lineage(name)` (REST), then walks the recursive
  `LineageColumn` tree client-side, emitting reactflow
  nodes/edges with column-level handles
  (`datajunction-ui/src/app/pages/NodePage/NodeLineageTab.jsx:30-71`).
  A separate `NodeDataFlowTab` renders a recharts Sankey of
  upstreams/downstreams.

## DAG construction

Multiple approaches coexist — DJ has clearly migrated **away** from
recursive CTEs **toward** layered BFS in Python, but both styles
remain.

- **Layered BFS (raw SQL per depth)** is the dominant pattern:
  - `get_downstream_nodes` (`sql/dag.py:113-203`): one raw SQL per
    depth level over `noderelationship`, accumulates a `visited` set,
    then a single batched ORM load with caller-supplied `options`.
    Comment: "avoids the Postgres recursive CTE materialization
    overhead on wide/deep graphs and the per-node ORM queries of the
    old BFS fallback" (`sql/dag.py:127-130`).
  - `get_upstream_nodes` (`sql/dag.py:206-279`): same pattern,
    inverted edge direction.
  - `_get_dimensions_dag_bfs` (`sql/dag.py:529-813`): more elaborate
    BFS that also tracks role paths and edge labels to reproduce the
    old recursive CTE's `path` and role-suffix output.
  - `get_dimension_inbound_bfs` / `get_dimension_outbound_bfs`
    (`sql/dag.py:1613`, `1693`).
  - Impact propagation BFS in `internal/impact.py:210-279`.
- **Recursive CTEs (SQLAlchemy + raw SQL)**:
  - `get_dimension_nodes` (`sql/dag.py:434-502`): full recursive CTE
    on `dimensionlink`, with `path`/`role` arrays accumulated using
    `func.array_cat`. Still in use for the
    `get_dimension_attributes` reference-link path.
  - `get_nodes_with_common_dimensions` (`sql/dag.py:1299-1470`):
    recursive CTE that expands a target dimension set across both
    `column.dimension_id` and `DimensionLink` "branches", then
    intersects with `having count(...) >= num_dimensions`.
  - `find_upstream_node_names` in build_v3
    (`construction/build_v3/loaders.py:33`): "lightweight" recursive
    CTE returning just node names + parent-child mapping, used
    during query construction to bulk-load ancestors in two queries.
- **Pure-Python Kahn's algorithm**:
  - `topological_sort(nodes)` (`sql/dag.py:1473-1521`): in-memory
    Kahn's algorithm over `node.current.parents`. Used wherever a
    deterministic dependency ordering is needed.
  - `topological_levels(graph)`
    (`internal/deployment/utils.py:139`): variant that returns nodes
    bucketed by level (used during deployment ordering).

**No use of `networkx`, `rustworkx`, or `igraph`** — DJ is
deliberately graph-library-free. All graph state lives in the
relational schema; traversals are SQL or hand-rolled.

## Cycle handling

Three layers of defense, all reactive (DJ does **not** statically
prove acyclicity at write time):

1. **In-memory traversals carry a `visited` set.** Every BFS in
   `sql/dag.py` and `internal/impact.py` short-circuits when it sees
   a node it's already visited
   (e.g. `sql/dag.py:643-644`, `frontier`/`visited` in
   `get_downstream_nodes`, `id_to_name` in
   `get_dimension_outbound_bfs`).
2. **Postgres `WITH RECURSIVE … CYCLE` clauses.** Both
   `get_dimension_nodes` and `get_nodes_with_common_dimensions`
   suffix their CTE with `CYCLE node_id SET is_cycle USING path`
   (`sql/dag.py:462`, `…:1356`) — this is the SQL:2023 cycle
   detection that flags rows where the path repeats so they aren't
   re-expanded.
3. **Topological sort raises.** `topological_sort` in `sql/dag.py:1518`
   raises `DJGraphCycleException("Graph has at least one cycle")`
   when Kahn's algorithm doesn't consume every node.
   `topological_levels` in `internal/deployment/utils.py:183` does
   the same. Defined in `errors.py:380`.

There does **not** appear to be a pre-commit cycle check that
prevents a user from creating a node whose query references a
descendant. The detection happens later, when something tries to
sort or traverse. (Caveat: I didn't trace the create-node path
exhaustively — flagging this as uncertain. The fact that the
in-memory cycle-prevention sets exist at all suggests DJ doesn't
fully trust that the graph is acyclic at runtime.)

The one explicit *up-front* cycle check is on **namespace
parentage** (`internal/namespaces.py:1615`, `detect_parent_cycle`),
which is a different graph (namespace hierarchy, not data lineage).

## Performance / caching

- **Indexes**: only on `noderelationship.parent_id` and
  `noderelationship.child_id` (`database/node.py:158-160`), and on
  `dimensionlink.node_revision_id` and `.dimension_id`
  (`database/dimensionlink.py:31-32`). No materialized closure table.
- **Layered-BFS-in-Python > recursive-CTE.** The migration pattern
  (BFS replacing recursive CTEs across the codebase) is explicitly
  motivated by recursive-CTE materialization overhead on wide/deep
  graphs. Each layer is one SQL round-trip; node loading is a single
  batched query at the end with caller-provided eager-load options.
- **Hard caps.** `get_downstream_nodes` truncates at
  `settings.node_list_max` (default `10000`,
  `config.py:221`) and emits a warning log when it trips
  (`sql/dag.py:171-178`). Depth is also caller-controlled
  (`depth: int = -1` for unbounded, default 30 for the dimensions
  DAG).
- **Memoisation:**
  - `get_shared_dimensions` builds a per-parent dimension cache
    (`parent_dims_cache`) so multi-metric queries don't re-traverse
    the same parent (`sql/dag.py:1010-1013`).
  - `_get_dimensions_dag_bfs` keys `discovered` by
    `(node_id, role_path)` so identical states aren't re-explored.
  - `get_metric_parents_map` batches the parent-resolution recursion
    into one SQL per "level" of derived-metric nesting.
- **Materialised column-level lineage.** Persisted to
  `NodeRevision.lineage` (JSON) so the GET endpoint usually returns
  immediately. The GraphQL resolver explicitly `defer()`s this column
  (`api/graphql/resolvers/nodes.py:386`) — it's heavy and rarely
  needed.
- **Instrumentation.** `_dag_timed` decorator in `sql/dag.py:44-72`
  wraps the major traversals and emits `dj.dag.traversal_ms` timer
  metrics tagged with the operation. There's also explicit timing in
  `propagate_impact` emitting `dj.deployment.propagate_impact_ms`.
- **No global query cache** for traversals. Each request re-walks.
  The materialized `NodeRevision.lineage` JSON is the only persistent
  cache.

## What's directly applicable to Knot

- **Don't build a separate lineage graph.** The node graph *is* the
  table-level lineage. A simple `(parent_node_id, child_node_id)`
  edge table indexed both ways is enough; everything else is a
  view/traversal over it. DJ's `noderelationship` schema is a clean
  template.
- **Three edge types, not one.** DJ separates (a) query parentage
  (`noderelationship`), (b) join links (`dimensionlink`), and (c)
  column-level dimension references (`column.dimension_id`). For
  Knot, we should similarly distinguish "definitional" edges (this
  view depends on this table) from "join" edges (these tables can
  be joined on this key) — they have different semantics for
  impact analysis.
- **Layered BFS in Python beats recursive CTEs at our scale.** DJ's
  team explicitly migrated away from CTEs because Postgres
  materialization is poor on wide graphs. One round-trip per layer
  + one final batched ORM load is a portable, cheap pattern.
- **`WITH RECURSIVE … CYCLE`**. If we do keep recursive CTEs (e.g.
  for the join graph), the SQL:2023 `CYCLE … SET is_cycle USING
  path` clause is the cleanest defense.
- **Background-task + JSON-blob lineage caching.** For column-level
  lineage (which is expensive to compute and rarely changes per
  revision), persisting a JSON blob on the revision row and
  recomputing as a `BackgroundTask` is a sensible pattern. We could
  do similarly for any expensive Knot derived data.
- **Instrumentation hook (`_dag_timed`).** Wrapping every traversal
  in a timer with an operation tag would catch perf regressions
  early; cheap to copy.

## What we'd do differently

- **Cycle prevention should be up-front, not reactive.** DJ's
  defense in depth is fine but it lets a user persist a cyclic
  graph. Knot should reject the offending revision at write time —
  a `BEFORE INSERT` check via a recursive CTE on the proposed parent
  set, or an explicit "compute would-be ancestors of this node and
  refuse if it appears" check, would be cleaner.
- **Column-level lineage from the AST is tightly coupled to the SQL
  parser.** DJ's `column_lineage` walks the compiled `ast.Column`
  tree (`internal/nodes.py:2256-2289`) and depends on a `dj_node`
  attribute being attached to each `ast.Table`. That's brittle —
  any parser change risks breaking lineage. If Knot wants
  column-lineage, consider emitting it from a more stable
  IR/projection layer rather than a hand-rolled AST walk.
- **No GraphQL / REST consistency for column-level lineage.** REST
  exposes `/nodes/{name}/lineage/`; GraphQL only has node-level
  upstream/downstream and `defer`s the lineage column. Knot should
  decide one canonical surface.
- **`node_list_max=10000` as an unconditional truncation feels
  fragile.** Logging a warning and silently dropping nodes risks
  incorrect impact analysis on large graphs. Better to page/stream
  or fail loudly.
- **No closure table.** For very wide downstream queries this could
  be worth materializing (insert/delete triggers maintain it).
  Worth measuring before deciding — DJ has measured at their scale
  and concluded BFS-per-layer is enough.
- **Sankey + reactflow column-graph in the UI is one client-side
  walk per render.** Caching the rendered topology server-side would
  cut repeat-render cost.

## Open questions / gaps in the survey

- **Is there an up-front cycle check on node-create?** I didn't
  exhaustively trace the create/upsert path; the parser populates
  `parents` but I didn't find a dedicated "would this create a
  cycle?" check there. Worth verifying before deciding Knot's write
  path.
- **What happens to lineage on node `deactivate` / hard-delete?**
  `noderelationship` has `ondelete="CASCADE"` on `parent_id`
  (`database/node.py:166`), so deleting a node drops edges. But
  most queries default to `include_deactivated=True` — the API may
  return zombie edges. Worth re-reading
  `…/api/nodes.py` deactivate path.
- **Does column lineage handle CTEs / subqueries / window
  functions correctly?** The walk in
  `internal/nodes.py:2256-2289` is a generic `find_all(ast.Column)`
  pop-loop; I didn't trace edge cases. Tests under
  `tests/api/nodes_test.py` and `tests/sql/dag_test.py` /
  `dag_bfs_test.py` would clarify.
- **`get_nodes_with_common_dimensions` recursive CTE intersection**
  (`sql/dag.py:1397-1404`, `having count(distinct …) >= N`) — does
  this scale on real Netflix-sized cube definitions? No
  benchmarks visible.
- **Deployment-time impact propagation re-validates queries
  level-by-level using ANTLR in a thread pool**
  (`internal/impact.py:391-411`). Did not survey the SQL parser
  cost or thread-pool sizing for very wide impact sets — that's a
  separate sibling-agent concern (materialization SQL).
- **GraphQL `defer(DBNodeRevision.lineage)`** — is it ever
  re-loaded? I didn't find a GraphQL field that explicitly returns
  the column-level lineage; possible the field exists but isn't
  surfaced in the schema. Flag for follow-up.
