# Tier A1 — 1 class, 1 source (smoke)

**Status:** ✅ implemented in week 1.

## Scope

- **Classes:** `Movie` only
- **Sources:** `imdb_movies` only (single source)
- **Stages exercised:** `normalize:imdb_movies` → `resolve:Movie` (degenerate single-source pass-through) → `merge:Movie` → `validate:Movie` → `materialize:iceberg_movie`
- **Materialization target:** Iceberg analytics table (`resolved_facts/Movie`); Neo4j optional

## Volumes

- ~50 Movie source rows
- ~50 Movie canonical_ids (no merges; single source)

## Edge cases seeded (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source-data normalization | 1.1 (null optionals), 1.2 (null required → fail), 1.3 (empty vs null), 1.4 (whitespace), 1.9 (numeric edges), 1.11 (trailing whitespace), 1.13 (missing identifier) |
| 2 — ER | 2.9 (single-source pass-through is degenerate ER) |
| 3 — Multi-valued | 3.4 (single-source = no resolution; degenerate path) |
| 9 — Incremental | 9.1, 9.2 (basic watermark advance / unchanged) |
| 11 — DataContext | 11.3 (empty primary → loud failure mutation), 11.7 (single-class primary) |
| 12 — Compile / hash | 12.1, 12.3, 12.4, 12.5, 12.8 (basic compile, canonicalization, replay determinism) |
| 14 — Materialization | 14.1 (single-class to Iceberg) |
| 15 — Audit walk-back | 15.1 (basic: fact → run → compile hash → source row) |

## Why this tier exists

The only fixture exercising **no-ER / no-relation-class** code paths. Smoke tests catch wiring breaks fast (compile, dispatch, normalize, materialize, walk-back) without multi-source noise.

## Files

- `spec.py` — Pydantic spec: 1 OntologyClass (Movie), ~5 Slots, 1 Source
- `sources/imdb_movies.csv` — ~50 rows with edge cases seeded
- `impls/iceberg_publisher.py` — minimal Materialization impl (writes Iceberg)
- `expected_facts.yaml` — ground-truth assertions
- `edge_cases.yaml` — canonical_id → category → seeded edge case
