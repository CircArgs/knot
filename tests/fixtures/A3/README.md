# Tier A3 — 1 class, many (~10) sources

**Status:** template only — fixture data not generated.

## Scope (planned)

- **Classes:** `Movie` only
- **Sources:** ~10 (imdb, tmdb, wikidata, themoviedb, letterboxd, omdb, rt, mc, fanart, ofdb)
- Same single-class stage flow as A2 but with many parallel `normalize` stages

## Volumes (planned)

- ~50 unique Movies
- ~10 sources × 35 rows = ~350 source rows total
- Heavy designed-in disagreement (some movies in 8/10 sources, some in 2/10)

## Edge cases (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source-data normalization | 1.1, 1.10 (encoding variants) |
| 3 — Multi-valued | 3.1, 3.3, 3.9 (WEIGHTED_VOTE with high-trust outlier vs low-trust majority) |
| 9 — Incremental | 9.1, 9.2 — many watermarks moving independently; cache hit propagation across N normalize stages |

## Why this tier might be needed

Stress on source-side coordination — N watermarks advancing independently, source-coverage-drop alerts (if some sources miss publishes), per-source-property trust calibration with many contributors.

## Files (when implemented)

- `spec.py`, `sources/<10 csvs>`, `impls/`, `expected_facts.yaml`, `edge_cases.yaml`
