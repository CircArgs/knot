---
title: DataJunction schema versioning survey
status: note
tags: [research, datajunction, reference, versioning]
project: knot
created_at: 2026-04-27T22:45:00+00:00
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# DataJunction schema versioning survey

Reference clone at `~/code/knot/refs/datajunction/`. Survey informs
Knot's schema-history architecture.

## Overview

DJ models a logical "node" (source / transform / dimension / metric /
cube) as a `Node` row that points at a sequence of immutable
`NodeRevision` rows. Each user-visible mutation of a node's definition
mints a *new* `NodeRevision`, bumps the version using a tiny
`vMAJOR.MINOR` scheme, and flips the parent `Node.current_version`
string. Old revisions stay in-table forever (no soft-delete on
revisions; only the parent `Node` carries a `deactivated_at`). Audit
events are appended to a separate `history` table that is
free-form-JSON-shaped and entity-agnostic.

There is **no rollback API and no formal "breaking change" classifier**.
"Breaking" is a coarse heuristic ("did the SQL/columns/PK change? then
major-bump") that exists purely to drive the version-bump policy — DJ
does not refuse a breaking change, it just bumps the major and
revalidates downstreams.

Key files (all paths relative to the clone):

- `datajunction-server/datajunction_server/database/node.py` — the
  `Node` and `NodeRevision` SQLAlchemy models.
- `datajunction-server/datajunction_server/database/history.py` — the
  `History` audit table model.
- `datajunction-server/datajunction_server/internal/history.py` —
  `ActivityType` and `EntityType` enums.
- `datajunction-server/datajunction_server/utils.py` — `Version`
  value-object and `VersionUpgrade` enum.
- `datajunction-server/datajunction_server/internal/nodes.py` — all
  the version-bump / diff / propagate / deactivate / restore logic.
- `datajunction-server/datajunction_server/api/nodes.py` — the
  user-facing routes (`/nodes/{name}/`, `/nodes/{name}/restore/`,
  `/nodes/{name}/revisions/`, etc.).
- `datajunction-server/datajunction_server/api/history.py` — the two
  history-listing endpoints.
- `datajunction-server/datajunction_server/internal/validation.py` —
  `NodeValidator` (the column-diff helper).

## The version model

**Unit of versioning is the `NodeRevision`** — a full snapshot of one
node's definition at a point in time, not a per-column or per-attribute
delta.

`NodeRevision` table (`database/node.py:1127`):

- Table name `noderevision`, primary key `id` (BigInt) plus a
  `UniqueConstraint("version", "node_id")` at line 1136.
- Owns the *content* of the node: `name`, `display_name`, `type`,
  `description`, `query`, `mode`, `version`, `node_id`, `catalog_id`,
  `schema_`, `table`, `cube_filters`, `status`, `updated_at`,
  `lineage` (JSON), `query_ast` (zlib-compressed pickle of the parsed
  AST, lines 1307-1310), `custom_metadata` (JSON), `derived_expression`.
- Owns the relationships: `columns` (cascade delete-orphan, line 1271),
  `dimension_links`, `availability`, `materializations`,
  `cube_elements`, `required_dimensions`, `metric_metadata`,
  `frozen_measures`, `parents`, `missing_parents`.

`Node` table (`database/node.py:281`) is the umbrella row — name,
namespace, type, `created_by`, `created_at`, `deactivated_at`, plus a
single string column `current_version` (line 318) that points at a
revision via the `version` text.

Versions are **two-component pseudo-semver strings** ("vMAJOR.MINOR",
no patch) parsed by `utils.py:480 class Version`:

```python
DEFAULT_DRAFT_VERSION = Version(major=0, minor=1)        # node.py:41
DEFAULT_PUBLISHED_VERSION = Version(major=1, minor=0)    # node.py:42
```

`Version.parse()` validates against `^v(?P<major>[0-9]+)\.(?P<minor>[0-9]+)`
(`utils.py:498`), so versions are not opaque hashes, content digests,
timestamps, or monotonic integers — they're a manually-bumped
human-readable label. `next_minor_version()` and
`next_major_version()` are the only mutators (`utils.py:505-515`); the
latter zeroes the minor.

`VersionUpgrade` enum (`utils.py:471`) has only `MAJOR` and `MINOR` —
there is no `PATCH`. It exists so callers can *force* a major bump
even when the diff would only justify a minor one; it's used by the
deployment orchestrator (`internal/deployment/orchestrator.py:2244,
3202`).

## Linkage to "current"

`Node.current_version` is a **denormalised string pointer** stored on
the parent row, not a foreign key. The "current revision" object is
exposed as a SQLAlchemy view-only relationship that joins on
`(Node.id == NodeRevision.node_id) AND (Node.current_version == NodeRevision.version)`
(`database/node.py:352-360`):

```python
current: Mapped["NodeRevision"] = relationship(
    "NodeRevision",
    primaryjoin=(
        "and_(Node.id==NodeRevision.node_id, "
        "Node.current_version == NodeRevision.version)"
    ),
    viewonly=True,
    uselist=False,
)
```

So "current" is *stored* (the string) and *resolved* (the join). A
matching `revisions` collection (`database/node.py:345`) lists all
revisions ordered by `updated_at`. There is no `is_current` flag on
revisions, no `valid_from / valid_to` interval, no `superseded_by`
backref.

Old revisions are **never deleted in normal flow** — they're retained
in `noderevision` indefinitely. Cascade deletion only fires when the
parent `Node` itself is hard-deleted (`Node.revisions` has
`cascade="all,delete"`, line 349, and `NodeRevision.node_id` has
`ondelete="CASCADE"`, line 1192). Soft-delete is the parent-level
`Node.deactivated_at` timestamp (line 326); deactivating a node leaves
all of its revisions untouched on disk. `Node.get_by_name(...,
include_inactive=False)` (line 581) is what filters them out by
default.

Cross-revision references in the DAG point at *specific revisions*,
not the umbrella node:

- `NodeRelationship.parent_id` -> `node.id` but `child_id` ->
  `noderevision.id` (`database/node.py:151-182`). So a child revision
  pins to a parent *node*, not a parent revision — there is a
  `parent_version: Optional[str]` field with default `"latest"`
  (line 173) that lets a child optionally pin to a specific parent
  version, but in normal use it follows whatever the parent's
  `current_version` is at query time.
- `CubeRelationship.cube_id` and `BoundDimensionsRelationship.metric_id`
  both go to `noderevision.id` (lines 193, 220).

This means: if you bump a parent and downstream nodes don't get a new
revision, the downstream's `parents` relationship still resolves
through the join, and the *meaning* of that edge changes silently
unless something revalidates the child. That's exactly what the
`propagate_update_downstream` machinery (see "Migration semantics")
does.

## Diffing

DJ does **not** maintain a stored revision-vs-revision diff. The
"what changed?" question is answered ad-hoc at the moment of an
`UPDATE` request, by comparing the incoming `UpdateNode` payload
against the in-memory `old_revision`.

The two diff helpers:

1. `has_minor_changes(old_revision, data)` — `internal/nodes.py:1386`
   — returns true if any of `description`, `mode`, `display_name`,
   `cube_filters`, `custom_metadata` differ. This is **string equality
   on top-level scalar fields**.

2. `create_new_revision_from_existing(...)` —
   `internal/nodes.py:1873` — inlines the same minor-change check at
   1885-1903 and the major-change check at 1906-1932. The major check
   is more interesting:
   - `query_changes`: `old_revision.query != data.query` (raw SQL string
     equality, line 1906-1911).
   - `column_changes`: only for SOURCE nodes — set-equality over
     `Column.identifier()` tuples (line 1912-1917).
   - `pk_changes`: set-equality on column names (line 1918-1923).
   - `required_dim_changes`: set-equality on column names (line 1924-1928).

3. For SOURCE refresh and revalidation, column-level diffing is set
   equality on `(name, type-string)`:
   - `refresh_source` (`internal/nodes.py:3607`): `{col.identifier()
     for col in current_revision.columns} != {(col.name, str(...))
     for col in new_columns}`.
   - `revalidate_node` (`internal/nodes.py:3352-3362`): walks
     `existing_columns` by name and compares `existing_col.type !=
     col.type`.

4. `NodeValidator.modified_columns(node_revision)` —
   `internal/validation.py:88` — the closest thing to a structured
   column diff. Given a freshly-validated set of columns, returns the
   set of names that differ from `node_revision.columns` by
   `(name, type)`:

```python
def modified_columns(self, node_revision: NodeRevision) -> Set[str]:
    initial_node_columns = {col.name: col for col in node_revision.columns}
    updated_columns = set(initial_node_columns.keys()).difference(
        {n.name for n in self.columns},
    )
    for column in self.columns:
        if column.name in initial_node_columns:
            if initial_node_columns[column.name].type != column.type:
                updated_columns.add(column.name)
        else:
            updated_columns.add(column.name)
    return updated_columns
```

That is: it conflates "column added", "column dropped", and "column
type changed" into a single set of names. There is no AST-level
structural diff, no "added vs removed vs renamed" distinction, no
column-attribute diff (PK / dimension link / partition).

DJ stores the parsed AST (`NodeRevision.query_ast`, compressed pickle,
line 1307) but uses it for query construction, not diffing.

## Breaking-change detection

There is no `change_type` enum, no `Compatibility` / `Breaking` enum,
no semantic-versioning gate, and **no policy that refuses a breaking
change**. The only classifier is the major-vs-minor heuristic in
`create_new_revision_from_existing` (`internal/nodes.py:1885-1932`):

| Triggered bump | What counts                                                                |
| -------------- | -------------------------------------------------------------------------- |
| MINOR          | description / mode / display_name / metric_metadata / custom_metadata changed |
| MAJOR          | query string changed, source columns changed, primary key changed, required_dimensions changed |

Cube-update has a parallel classifier (`update_cube_node`,
`internal/nodes.py:1453-1458`):

- MINOR: same five scalar fields plus `cube_filters`.
- MAJOR: `data.metrics != old_metrics` or `data.dimensions !=
  old_dimensions`.

Two more major-bump call sites:

- `revalidate_node` at `internal/nodes.py:3373-3377`: when a node is
  revalidated and **column types** of the validated query differ from
  the stored columns (`updated_columns` flag), DJ deep-copies the
  current revision, bumps **major**, and stores it. So a parent
  changing its column types implicitly bumps the *child's* major
  version when the child is next revalidated.
- `refresh_source` at `internal/nodes.py:3656`: any column change in
  the underlying physical table forces a major bump.
- `create_new_revision_for_dimension_link_update`
  (`internal/nodes.py:2849`): adding/removing a dimension link forces
  a **minor** bump (debatable — adding a join could absolutely break
  consumers, but DJ classifies it as minor).

`VersionUpgrade.MAJOR` lets the deployment orchestrator force a
major bump even when the diff doesn't trigger it (used at
`orchestrator.py:2244, 3202` for owner / catalog updates that
mechanically invalidate downstream artifacts).

What DJ does *not* do:

- No "additive vs subtractive" classification. Adding a nullable
  column is treated identically to dropping a non-null column —
  both bump major if they're SOURCE column changes.
- No type-compatibility analysis. `INT -> BIGINT` is a major bump.
  `STRING -> INT` is also a major bump. Same code path.
- No downstream-impact scoring at version-bump time. Impact is
  computed *post hoc* by `propagate_update_downstream` revalidating
  every descendant.
- No producer/consumer contract. There is no
  `NodeRevision.deprecated_at`, no migration window, no warning emitted
  to clients still pinned to vN-1.

## Migration semantics

When a user `PATCH /nodes/{name}/`s a node definition, DJ executes
`update_any_node` (`internal/nodes.py:1131` for cube,
`update_node_with_query` at `internal/nodes.py:1185` otherwise). The
flow:

1. Load the node `for_update=True` so concurrent writers serialize
   (line 1143).
2. `create_new_revision_from_existing(...)` builds a *new*
   `NodeRevision` row. Its `parents` are reset to `[]` and rebuilt
   from re-parsing the new query (lines 2036-2049, also see
   line 1975).
3. Bump version on the new revision (`old.next_major_version()` or
   `old.next_minor_version()`).
4. **Atomically** flip `node.current_version = new_revision.version`
   under `session.no_autoflush` (line 1230) and commit.
5. Schedule `propagate_update_downstream` as a FastAPI background
   task (line 1337).
6. Background task: load all transitive descendants, `topological_sort`
   them, and call `revalidate_node(downstream.name, ...)` for each
   (`internal/nodes.py:1683`).
7. `revalidate_node` re-parses the downstream's query in the new
   parent context. If column types changed (`updated_columns`),
   it copies the downstream's current revision via
   `copy_existing_node_revision` (line 1742), bumps **major** on the
   downstream, and saves it (line 3373-3413). If validation fails,
   `status` is flipped to `INVALID` and a `STATUS_CHANGE` history event
   is written.

So:

- **(a) Downstream nodes that depend on the changed node**: revalidated
  asynchronously; if their *column shape* changes as a result, they
  get a fresh major-bumped revision; if validation fails, they go
  `INVALID` and emit a status-change history event but are *not*
  rolled back, and a `History(entity_type=NODE, activity_type=UPDATE,
  details={changes: {updated_columns: [...]}, upstream: {...}, reason:
  ...}, pre/post={status, version}, user=...)` is appended (line
  1708-1738).

- **(b) Materialized data with the old shape**: `NodeRevision.availability`
  and `NodeRevision.materializations` are owned per-revision (lines
  1287, 1297). The *new* revision starts with empty `materializations`
  (line 1977), so Druid/Spark jobs from the old revision do not
  automatically re-target. The update flow in `update_node_with_query`
  *does* recreate active materializations for the new revision when
  the query changed (line 1266-1305), but old materialised tables
  produced under v1.0 are not deleted, not deprecated, not labelled
  — they just stop being reachable through `Node.current.materializations`
  and remain visible only via the per-revision history. The cube
  refresh path explicitly re-activates materializations whose
  `mat_config.cube.version == current_version` (line 1483-1494) but
  silently *ignores* materializations belonging to older versions.

- **(c) Live queries**: DJ has no "API version" pin in its query
  endpoints; every `/sql/{name}` build resolves through
  `Node.current` (which now points at the new revision). If a caller
  was pinning to a specific version via `parent_version` on a
  `NodeRelationship` (the only place pinning is supported), that pin
  still works. There is no "live query session" abstraction that
  could be invalidated.

## Audit trail

Single table, `history` (`database/history.py:23`):

```
id            BigInt PK
entity_type   Enum(EntityType)         -- NODE, COLUMN_ATTRIBUTE, LINK,
                                          MATERIALIZATION, NAMESPACE,
                                          PARTITION, BACKFILL, ATTRIBUTE,
                                          AVAILABILITY, CATALOG, DEPENDENCY,
                                          ENGINE, HIERARCHY, QUERY, ROLE,
                                          ROLE_ASSIGNMENT, ROLE_SCOPE, TAG
entity_name   String
node          String                    -- denormalised "the node this is
                                          about" for cheap node-scoped queries
version       String                    -- nullable; usually unused (the
                                          version goes in `details` instead)
activity_type Enum(ActivityType)       -- CREATE, DELETE, RESTORE, UPDATE,
                                          REFRESH, TAG, SET_ATTRIBUTE,
                                          STATUS_CHANGE
user          String                    -- username, FREE STRING (not FK)
pre           JSON
post          JSON
details       JSON
created_at    UTCDatetime
```

(Indexes: `ix_history_entity_name`, `ix_history_user` —
`alembic/versions/2024_10_26_0340-..._add_indexes_on_history_and_node_tables.py`.)

The `Node` model exposes `history` via a back-relationship that joins
on `History.entity_name == Node.name` (`database/node.py:388-392`),
and `Node.edited_by` is a hybrid_property that distincts on
`history.user` (line 397).

What is captured:

- **Who**: `user` is `current_user.username` — written by the call
  site (`api/helpers.py:932 save_history`). Not a FK to `users.id`,
  so renaming a user breaks the trail.
- **What**: `entity_type` + `activity_type` discriminators, plus
  free-form `pre`, `post`, `details` JSON. Schema is per-emit-site —
  e.g. `_propagate_update_downstream` (`internal/nodes.py:1708`)
  emits `details={changes: {updated_columns: [...]}, upstream: {...},
  reason: ...}, pre={status, version}, post={status, version}`,
  while `node_update_history_event` (line 1419) emits only
  `details={version}`.
- **When**: `created_at`, server-generated (line 53-56 of
  `database/history.py`).

What is **not** captured: source IP, request ID, user-agent, session
ID, trace ID, OAuth client. The save site is
`api/helpers.py:932 save_history(event, session, _notify)` — it
takes only the `History` object and the session, no `Request`. The
`get_save_history` factory at line 948 wraps it with a notifier hook,
not with request-context enrichment.

There is no append-only enforcement at the DB layer — `history` rows
are normal SQLAlchemy ORM rows; nothing prevents an admin from
`UPDATE`ing or `DELETE`ing them. There is no hash-chain, no signature.

API surface (`api/history.py`):

- `GET /history/{entity_type}/{entity_name}/` — paginated entity
  history.
- `GET /history/?node=...&only_subscribed=...` — paginated, optionally
  intersected with the caller's `notification_preference` rows.

Both routes return `HistoryOutput` which includes `pre`, `post`,
`details` verbatim (`models/history.py:20-36`).

## Rollback / restore

There is **no revision-rollback API**. The only "restore" verb is at
the *node* (umbrella) level, not the revision level:

- `POST /nodes/{name}/restore/` (`api/nodes.py:435`) un-deactivates a
  whole node by clearing `Node.deactivated_at`, re-attaching it to
  downstream parents that had marked it as a `MissingParent`, and
  revalidating downstreams (`activate_node`,
  `internal/nodes.py:3083`). This is the inverse of `DELETE
  /nodes/{name}/` (which calls `deactivate_node`,
  `internal/nodes.py:3016`). It does *not* select a specific revision
  to restore to — `current_version` is whatever it was at deactivation
  time.

To "go back to v1.2 of node X", a user has to:

1. Read the old revision via `GET /nodes/{name}/revisions/`
   (`api/nodes.py:464`, returns `List[NodeRevisionOutput]`).
2. Construct an `UpdateNode` payload with the old query / columns /
   etc.
3. `PATCH /nodes/{name}/` with that payload, which mints a *new*
   revision (e.g. v3.0) whose contents happen to mirror v1.2.

Said another way: every "rollback" is a forward-only *re-paste*
producing a new major version. Old revisions are never re-promoted to
`current`. There is no API that lets a user say "set
`Node.current_version = 'v1.2'`" — the only writer of that field is
`update_node_with_query` (and friends), and they always set it to
the version of the freshly-minted new revision.

`Node.deactivated_at` is the closest thing to a tombstone. Once a
node is hard-deleted (`api/nodes.py:403`, `hard_delete_node` at
`internal/nodes.py:3426`), the entire `Node` row plus all its
revisions are SQL-deleted via cascade — at that point history
survives only via the `history` table (which references nodes by
*name*, not FK, so it is not cascaded).

## What's directly applicable to Knot

- The **immutable-revision-with-current-pointer** pattern is the right
  default. `(Node, NodeRevision, current_version)` is simple,
  composable, and fast for the 99% read path.
- Modeling all per-version state (columns, links, availability,
  materializations) as relations *on the revision*, not on the umbrella
  node, cleanly localises "what changed" to a single FK swap.
- A separate **`history` audit table with `(entity_type, activity_type,
  pre, post, details)` JSON columns** is a low-friction way to capture
  unstructured-but-greppable mutation events without coupling to the
  domain model — useful for both operator forensics and notification
  fan-out (the `notification_preference` table piggybacks off this).
- Putting the parsed AST in compressed pickle on the revision
  (`query_ast` column) accelerates downstream revalidation and
  preserves a structured snapshot — Knot will want the same for
  ontology shapes (LinkML schema → parsed graph form).
- The "downstream invalidation as topological-sorted background task"
  pattern is a good fit for ontology change propagation.

## What we'd do differently

- **Use a real semantic-version classifier**, not a five-field
  heuristic. At minimum: `additive` (new optional column / class) vs
  `breaking` (rename / drop / type change), exposed as an enum on the
  revision and computed once at write time.
- **Stronger audit fields**: FK to `users.id` rather than a free
  string; capture `request_id`, source IP, user-agent, OIDC `sub`,
  trace context. The DJ trail is fine for "who probably did this"
  but useless for forensic correlation.
- **Append-only history at the DB layer** (Postgres trigger or
  separate insert-only role) so it isn't editable by an app-level
  bug. Optionally add a hash chain.
- **First-class rollback**: a `POST /nodes/{name}/revert/{version}/`
  endpoint that re-promotes an old revision to current, plus a
  `superseded_by` / `restored_from` link on the revision so the
  history reads as a graph rather than a flat append.
- **Structured column-level diff** stored on the revision: added,
  removed, type_changed, attribute_changed, with type-compatibility
  classification. Knot's ontology-shape diffs will be richer than
  DJ's column lists; we want to compute them once and store them.
- **Per-revision deprecation lifecycle**: `deprecated_at`,
  `sunset_at`, `replaced_by_version`, plus a "live consumers" view
  computed from access logs / pinned references so we can warn
  before breaking. DJ has none of this.
- **Versioning contract for the wire**: clients should be able to
  pin to `vMAJOR.x` or `vMAJOR.MINOR` and get a deprecation header.
  DJ leaks `current_version` everywhere but has no client-side
  pin protocol.
- **Don't denormalise `current_version` as a string FK**. A real FK
  to a `noderevision.id` (with a partial unique index for "one current
  per node") avoids the join-on-string in `Node.current` and removes
  a class of "version drifted" bugs.

## Open questions / gaps in the survey

- I did not trace whether the GraphQL surface
  (`api/graphql/`) exposes any version operations beyond the REST
  ones; spot-check needed if Knot wants a graph-shaped admin API.
- Branches: there is a `git_branch` / `default_branch` concept
  surfaced in the search-scoring SQL (`database/node.py:138-143`) and
  an `api/branches.py` route file — DJ has *some* notion of feature
  branches for nodes that I did not survey here. May overlap with
  versioning semantics if Knot wants branching.
- I did not chase how `frozen_measures` interact with revisions
  (referenced at `database/node.py:1318-1323`); if Knot has a
  measures-equivalent it could matter.
- The deployment orchestrator (`internal/deployment/orchestrator.py`)
  uses `VersionUpgrade.MAJOR` to force bumps in two places — I did
  not chase the full flow there. May reveal additional implicit
  versioning rules.
- DJ stores `lineage` as a JSON list on each revision (line 1302).
  How that snapshot interacts with versioning (is it recomputed on
  every revalidate? cached? diffed?) is in scope for the lineage
  sibling survey, not this one.
- I did not check whether deactivation prevents new revisions from
  being created on the node, or whether there's a way to update an
  inactive node — `update_any_node` does pass `include_inactive=True`
  on its `get_by_name` (line 1144), suggesting yes-with-caveats, but
  I didn't trace the full path.
