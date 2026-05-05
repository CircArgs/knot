---
title: "Walkthrough paths — UI ↔ API ↔ postgres ↔ lake ↔ orchestrator"
status: note
created_at: 2026-04-29T03:35:00+00:00
project: knot
tags: [walkthrough, prototype, exploration]
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.


# Walkthrough paths

Five curated UX paths to demonstrate knot end-to-end. For each, what
the curator clicks in the UI, what API call happens, which postgres
tables change, what the lake sees, and what the orchestrator does.

Use with one of the seed variants under `scripts/seed/` (start with
`busy.py` for richest signal). Run each path in a different browser
tab while keeping a `psql` shell + the orchestrator log open.

---

## Path 1 — Submit a correction, watch it propagate end-to-end

**Setup:** seeded state. Open `/entities` triage dashboard.

**Step-by-step:**

| # | Curator action | API hit | Postgres mutation | Lake / orch | What you see in UI |
|---|---|---|---|---|---|
| 1 | Click an entity in "Most corrected" | `GET /entity/Movie/{key}` | none (read) | — | Entity panel slides in: properties + alternatives + lineage tabs |
| 2 | Click pencil on `title` row → enter new value → submit | `POST /corrections` body `{ontology_class, key, property_, new_value}` | `INSERT corrections (...)` ; `INSERT history (entity_type='correction', activity='created', user_id, request_id, source_ip, trace_id)` ; `INSERT per_source_facts` under the synthetic `_user_corrections` source at trust 0.95 | — | Correction appears as `_user_corrections, pending` in alternatives list (optimistic) |
| 3 | Wait for next pipeline tick (or click "Run now" on the Movie pipeline) | `POST /pipelines/BuildMediaGraph/runs` returns `{run_id}` | `INSERT runs (status='queued')` | Orchestrator picks up: ingest → resolve → merge → publish stages | Pipeline run appears in `/pipelines` panel; status flips queued → running → succeeded |
| 4 | Refresh entity panel | `GET /entity/Movie/{key}` | none | resolved_facts now has the new winning row from merge (correction won by trust score) | Resolved value shows new corrected text; correction no longer flagged "pending" |

**Verification queries** (`./scripts/db.sh psql`):

```sql
-- The correction record
SELECT id, ontology_class, source_natural_key, target_property, payload, user_id, created_at
FROM corrections
WHERE source_natural_key = '<key>'
ORDER BY created_at DESC LIMIT 1;

-- Audit row
SELECT entity_type, activity_type, user_id, request_id, created_at
FROM history WHERE activity_type = 'correction_submitted'
ORDER BY created_at DESC LIMIT 1;

-- The synthetic source row in per_source_facts
SELECT psf.property, psf.value, psf.trust_at_write, n.name AS source
FROM per_source_facts psf JOIN ontology_nodes n ON n.id = psf.source_node_id
WHERE psf.source_natural_key = '<key>' AND n.name = '_user_corrections';

-- The resolved value AFTER the next merge
SELECT property, value, resolved_from_source_id, trust_at_resolve, resolved_at
FROM resolved_facts WHERE source_natural_key = '<key>'
ORDER BY resolved_at DESC LIMIT 5;
```

---

## Path 2 — Add a new source, ingest, ER, materialize

**Setup:** any seed.

| # | Action | API | Postgres | Lake / orch |
|---|---|---|---|---|
| 1 | `/sources` → "Add source" → fill form (name=letterboxd, ontology_class=Movie, initial_trust=0.7) | `POST /nodes` (kind=source draft) → `POST /nodes/letterboxd/publish` | `ontology_nodes (kind='source')` ; `ontology_node_revisions (payload=…)` | — |
| 2 | Confirm in `/sources` rows, source detail shows trust history empty | `GET /sources` ; `GET /sources/letterboxd/trust-history` | none | — |
| 3 | `POST /sources/letterboxd/data` (or paste JSON via test page) | `POST /sources/{name}/data` body `{rows: [...]}` | `INSERT per_source_facts (source_node_id, ontology_class, source_natural_key, property, value, trust_at_write, ingested_at)` per cell | — |
| 4 | Trigger `POST /merge/Movie?strategy=trust_weighted` | same | `INSERT resolved_facts (resolved_from_source_id, ontology_class, key, property, value, trust_at_resolve, resolved_at)` | merge runner emits **lake job spec** to orchestrator |
| 5 | Open `/triage` → "Just arrived" card → see new key | `GET /entities/just-arrived?since=…` | none | — |
| 6 | Drill into a new entity → lineage tab | `GET /entity/Movie/{key}/lineage` | reads `node_dependencies` + per-fact `resolved_from_source_id` | — |

**psql checkpoints:**

```sql
-- Source registered + trust score
SELECT n.name, n.kind, ss.current_trust, ss.last_seen_at
FROM ontology_nodes n LEFT JOIN sources_state ss ON ss.node_id = n.id
WHERE n.name = 'letterboxd';

-- Per-source ingested facts
SELECT property, value, trust_at_write, ingested_at FROM per_source_facts psf
JOIN ontology_nodes n ON n.id = psf.source_node_id
WHERE n.name = 'letterboxd' ORDER BY ingested_at DESC LIMIT 10;

-- After merge: resolved + per-source diff
SELECT rf.property, rf.value AS resolved, n.name AS chose
FROM resolved_facts rf JOIN ontology_nodes n ON n.id = rf.resolved_from_source_id
WHERE rf.source_natural_key = '<key>';
```

---

## Path 3 — Adjust a source's trust score, watch ER reweight

**Setup:** `busy` seed. Open `/sources/fan_wiki`.

| # | Action | API | Postgres |
|---|---|---|---|
| 1 | Click pencil next to trust score (0.3) → modal → slide to 0.95 → reason="emergency upgrade for testing" → save | `PATCH /sources/fan_wiki/trust` `{trust_score, reason}` | `UPDATE sources_state SET current_trust=0.95, trust_set_by='manual', trust_set_by_user, trust_set_at, trust_reason WHERE source_name='fan_wiki'` ; `INSERT history (activity='trust_score_set', pre, post, details, user_id, …)` |
| 2 | Trust history chart annotates the manual point distinctly | `GET /sources/fan_wiki/trust-history` | reads from history rows where activity_type='trust_score_set' |
| 3 | Run merge again | `POST /merge/Movie` | resolved_facts now picks fan_wiki's values for affected keys (it just outranked imdb) |
| 4 | Triage dashboard "Trust conflict" card empties for fan_wiki entries (they're now winners) | `GET /entities/trust-conflicts` | server-side join of resolved vs alternatives by trust |

**Curator gotcha to highlight:** algorithm respects manual overrides
until `trust_expires_at` — without an expiry, the source stays
upgraded forever. Show the user this in the modal's "Temporary?"
checkbox.

---

## Path 4 — Run a query through the workbench, dispatched via target plugin

**Setup:** any seed; `/query` page (workbench).

| # | Action | API | Postgres / lake / orch |
|---|---|---|---|
| 1 | Pick language=SQL, paste `SELECT * FROM movies_resolved LIMIT 50` | (none until run) | — |
| 2 | Click Run | `POST /query` `{language: 'sql', query, target?}` returns `202 {id}` | `INSERT query_runs (status='queued', user_id, request_id, source_ip, trace_id, query_text, query_shape, target)` ; orchestrator `submit({job_type:'query', payload:{sql, target}})` |
| 3 | UI polls every 1s | `GET /query/{id}` | first poll: status='running' ; subsequent polls: orchestrator `status(orch_run_id)` reports terminal → `UPDATE query_runs SET status='succeeded', result_rows=…::jsonb, result_columns=…::jsonb, row_count, truncated, finished_at` |
| 4 | Result panel switches Table → Subgraph if shape allows | (subgraph render is client-side) | — |
| 5 | Click an entity-uuid pill in a result row → entity detail panel | `GET /entity/Movie/{uuid}` | — |

**Translator path (if Cypher / SPARQL / GraphQL chosen):**
- POST `/translate/{lang}` first turns it into SQL using the registered
  `QueryTranslator[Lang]` plugin (per `translator-impl-decision.md`).
- Then same submit flow as SQL.

**Truncation visibility:** if the query returns more than ROW_CAP
(10k) rows, `truncated: true` rides through to the response. UI
shows a banner "Result truncated at 10,000 rows."

---

## Path 5 — Publish a new ontology class, see blast radius

**Setup:** any seed. Open `/ontology` (browser-editor).

| # | Action | API | Postgres |
|---|---|---|---|
| 1 | "+ New ontology class" → name=Director, kind=data → empty skeleton | `POST /nodes` (draft) | `INSERT ontology_nodes (kind='data', name='Director')` ; `INSERT ontology_node_revisions (node_id, version='v1.0-draft', is_breaking=false, payload={…skeleton…})` |
| 2 | Toggle Rich/Raw — add slot `name: string required`, `dob: date` | (in-browser only until save) | — |
| 3 | Save draft | `POST /nodes/Director/draft` updating revision | `UPDATE ontology_node_revisions SET payload=…` |
| 4 | Click Publish | `POST /nodes/Director/publish?version=v1.0` | static validator runs; on PASS: `UPDATE ontology_nodes SET current_version='v1.0'` ; `INSERT history (entity_type='ontology_node', activity='published', pre, post, …)` |
| 5 | Diff tab shows additive (no `is_breaking`) | `GET /nodes/Director/revisions` reads | — |
| 6 | Pre-publish, banner shows "0 downstream nodes affected" | `GET /metagraph?around=Director` | walks `node_dependencies` |

**psql checkpoints:**

```sql
-- New node visible
SELECT name, kind, current_version FROM ontology_nodes WHERE name = 'Director';

-- Revision payload
SELECT version, is_breaking, jsonb_pretty(payload), created_at, created_by
FROM ontology_node_revisions WHERE node_id = (SELECT id FROM ontology_nodes WHERE name='Director')
ORDER BY created_at DESC;

-- Audit
SELECT activity_type, jsonb_pretty(post), created_at FROM history
WHERE entity_type='ontology_node' AND post->>'name'='Director' ORDER BY created_at DESC;
```

---

## Cross-cutting probes (works on any path)

```sql
-- All recent history events
SELECT entity_type, activity_type, user_id, created_at FROM history
ORDER BY created_at DESC LIMIT 30;

-- Active pipeline runs
SELECT id, status, started_at, finished_at FROM runs ORDER BY started_at DESC LIMIT 10;

-- Per-stage events for a run
SELECT stage, status, started_at, finished_at FROM run_events
WHERE run_id = '<id>' ORDER BY started_at;

-- Lake snapshot per Movie key
SELECT property, value, resolved_from_source_id FROM resolved_facts
WHERE ontology_class='Movie' AND source_natural_key='<key>';

-- Sources state
SELECT n.name, ss.current_trust, ss.trust_set_by, ss.last_seen_at
FROM ontology_nodes n JOIN sources_state ss ON ss.node_id = n.id
WHERE n.kind='source' ORDER BY ss.current_trust DESC;
```

---

## Demo ordering recommendation

For a 30-minute live demo:

1. Reset stack (`./scripts/db.sh reset && ./scripts/db.sh up`).
2. Run `python -m scripts.seed.busy` (~30s).
3. Walk Path 1 (correction propagation) — 8 min.
4. Walk Path 3 (trust adjustment) — 5 min.
5. Walk Path 4 (workbench query) — 6 min.
6. Walk Path 5 (publish ontology class with blast radius) — 6 min.
7. Show `/triage` dashboard's response to all the changes — 5 min.

Save Path 2 (new source + ER) for a deeper-dive session — it's the
longest end-to-end path and benefits from a separate framing.
