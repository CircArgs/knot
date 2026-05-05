# Tier A2 — 1 class, 3 sources (multi-source resolution focus)

**Status:** template only — fixture data not generated. Build when multi-source / trust-CTE tests need isolation from relation-class noise.

## Scope (planned)

- **Classes:** `Movie` only
- **Sources:** `imdb_movies`, `tmdb_movies`, `wikidata_movies`
- **Stages exercised:** `normalize:*` × 3 → `resolve:Movie` (real ER) → `merge:Movie` → `validate:Movie` → `materialize:*`

## Volumes (planned)

- ~50 unique Movies
- ~150 source rows (3 sources × 50 movies, with ~70% overlap → ~30 dupes per source)
- ~10 designed-in known dupes
- ~10 designed-in disagreement cases (year, title, runtime)

## Edge cases (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source-data normalization | 1.1, 1.6 (unicode), 1.8 (date format drift), 1.10 (encoding) |
| 2 — ER | 2.1 (known dupes), 2.3 (three-way dupes), 2.4 (asymmetric coverage) |
| 3 — Multi-valued | 3.1, 3.2, 3.3, 3.5, 3.7, 3.10 (every `ResolutionPolicy` enum value) |
| 6 — Spec evolution | 6.5 (change `resolution_policy` mid-run, mutation test) |
| 9 — Incremental | 9.1, 9.2 |
| 12 — Compile / hash | 12.8 |
| 14 — Materialization | 14.1 |
| 15 — Audit walk-back | 15.1 |

## Why this tier might be needed

Debugging trust-resolution tests in isolation when B2 fails — A2 strips relation/derivation noise so failures are unambiguously about the multi-valued machinery. Optional; B2 covers everything.

## Files (when implemented)

- `spec.py`, `sources/{imdb,tmdb,wikidata}_movies.csv`, `impls/er_movie.py`, `impls/iceberg_publisher.py`, `expected_facts.yaml`, `edge_cases.yaml`
