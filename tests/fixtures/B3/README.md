# Tier B3 — 3 classes, many sources per class

**Status:** template only — fixture data not generated.

## Scope (planned)

- **Classes:** `Movie`, `Person`, `Credit`
- **Sources:** ~10 sources for Movie, ~6 for Person, ~3 for Credit
- All of B2's machinery + scale-stress on the source side

## Volumes (planned)

- Same canonical entity count as B2 (~100 Movies, ~200 Persons, ~1000 Credits)
- Many more source rows (~10× the B2 source-row count)
- Designed-in source-coverage variance (some movies in 8/10 sources, others in 2/10)

## Edge cases additional to B2 (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source normalization | 1.6, 1.7 (heavier unicode / non-Latin volume) |
| 3 — Multi-valued | 3.9 (WEIGHTED_VOTE with multiple high-trust outliers) |
| 9 — Incremental | 9.1 with many parallel watermark advances |

## Why this tier might be needed

Detect coordination bugs that only surface with N parallel watermarks (e.g., race conditions in cache-key computation across many normalize stages, watermark-aggregation drift).

## Files (when implemented)

- Same shape as B2 with additional `sources/` CSVs
