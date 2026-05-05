# Tier B2 — 3 classes, 3 sources per class (default integration tier)

**Status:** ✅ implemented in week 1. **The default fixture for integration tests.** Every commitment exercised at once.

## Scope

- **Classes:** `Movie`, `Person`, `Credit`
- **Sources:** `{imdb,tmdb,wikidata}_movies`, `{imdb,tmdb}_persons`, `imdb_credits` (mixed source counts per class)
- **Stages:** full pipeline — normalize × N → resolve → merge → validate → materialize
- **Derivations:** `Movie.director`, `Movie.actors`, `Movie.writers`, `Movie.producers` (all via Credit role)
- **Materialization:** Neo4j (nodes + derived edges) + Iceberg analytics tables

## Volumes

- ~100 Movies, ~200 Persons, ~1000 Credits
- ~3 sources × 70-80 rows per movie/person → ~500 source rows
- ~20 designed-in known dupes (ground truth labeled)
- ~10 designed-in disagreement cases per slot
- ~5 designed-in conflict cases (multi-source disagreement on canonical-id-determining fields)
- ~5 polymorphic-Identifier test cases (deferred to C-tier ideally; may include in B2)

## Edge cases — ALL categories from `../EDGE-CASES.md`

This tier seeds every category at least once. See `../EDGE-CASES.md` coverage matrix; B2 row says "ALL CATEGORIES — kitchen sink default."

Specific must-exercise per category:

| Cat | Specific cases |
|---|---|
| 1 | 1.1, 1.3, 1.4, 1.5, 1.6, 1.8, 1.10, 1.11, 1.12, 1.13 |
| 2 | 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7 (mutations: 2.8) |
| 3 | 3.1, 3.2, 3.3, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10 |
| 4 | 4.1, 4.2, 4.3, 4.6 |
| 5 | 5.1, 5.2, 5.3, 5.6, 5.7 (mutations: 5.4, 5.5, 5.8) |
| 6 | 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7 (mutations) |
| 7 | 7.1, 7.2, 7.3, 7.4, 7.5 (mutations) |
| 9 | 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7 (mutations) |
| 10 | 10.1, 10.2, 10.3, 10.4, 10.5, 10.6 (mutations) |
| 11 | 11.1, 11.2, 11.4, 11.5, 11.6, 11.7, 11.8, 11.9 |
| 12 | 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8 |
| 13 | 13.1, 13.2, 13.3, 13.4, 13.5 |
| 14 | 14.1, 14.2, 14.3, 14.4, 14.6 |
| 15 | 15.1, 15.2, 15.3, 15.4, 15.5, 15.6 |

## Why this tier is the default

Where most regressions surface first. Multi-source + relations + derivations + materialization in one fixture means a single failing test usually pinpoints the broken commitment.

## Files

- `spec.py` — Pydantic spec for Movie + Person + Credit with derivations
- `sources/{imdb,tmdb,wikidata}_movies.csv`, `sources/{imdb,tmdb}_persons.csv`, `sources/imdb_credits.csv`
- `impls/er_movie.py`, `impls/er_person.py`, `impls/er_credit.py`
- `impls/neo4j_publisher.py`, `impls/iceberg_publisher.py`
- `configs/` — initial impl configs
- `expected_facts.yaml` — ground-truth assertions for ~50 specific canonical_ids and derived edges
- `edge_cases.yaml` — full case-id manifest mapping seeded test cases to canonical_ids
