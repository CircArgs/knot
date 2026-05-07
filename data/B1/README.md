# Tier B1 — 3 classes, 1 source per class

**Status:** template only — fixture data not generated.

## Scope (planned)

- **Classes:** `Movie`, `Person`, `Credit`
- **Sources:** `imdb_movies`, `imdb_persons`, `imdb_credits` (one source per class)
- **Stages:** full pipeline; ER stages are degenerate single-source pass-through; cross-class pinning still applies (Credit pins Movie + Person hashes)
- **Derivations:** `Movie.director` (via Credit role)

## Volumes (planned)

- ~50 Movies, ~100 Persons, ~250 Credits
- No ER work (single-source); cross-class structure is the focus

## Edge cases (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source-data normalization | 1.1, 1.4, 1.5 |
| 2 — ER | 2.9 (single-source pass-through across all 3 classes) |
| 4 — Cross-class relations | 4.1 (existing parent ref), 4.3 (orphan ref → fail) |
| 5 — Derivation rules | 5.1 (empty), 5.2 (single-result), 5.6 (forward-chain), 5.7 (backward-chain) |
| 8 — Polymorphic / subclass | (none — keep B1 simple; subclass tests live in C-tier) |
| 9 — Incremental | 9.3 (cross-class pinning + cache key composition) |
| 11 — DataContext | 11.7, 11.9 (slot.range JOINs) |
| 12 — Compile / hash | 12.8 |
| 14 — Materialization | 14.2 (Movie + Person + Credit → Neo4j; derived edges) |
| 15 — Audit walk-back | 15.4 (cross-class walk-back) |

## Why this tier might be needed

Debugging cross-class-pinning + derivation tests in isolation when B2 fails. Strips multi-source noise so failures are unambiguously about relation/derivation machinery. Optional; B2 covers everything.

## Files (when implemented)

- `spec.py`, `sources/imdb_{movies,persons,credits}.csv`, `impls/{er_movie,er_person,er_credit,neo4j_publisher}.py`, `expected_facts.yaml`, `edge_cases.yaml`
