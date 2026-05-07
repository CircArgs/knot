# Tier C2 — 10+ classes, ~3 sources per class (rich-ontology stress)

**Status:** ✅ implemented in week 1. **The structural-stress fixture.**

## Scope

- **Classes:** `Title` (abstract) → `Movie`, `Series`, `Episode`, `Game` subclasses; `Person`; `Credit`; `Identifier` (polymorphic); `Studio`; `Award`; `Country`
- **Sources:** ~3 sources per leaf class
- Full subclassing, polymorphic refs, deep DataContext expressions, derivation chains
- **Derivations:** `Movie.director`, `Movie.actors`, `Movie.writers`, `Movie.producers`, `Series.creator`, `Episode.lead_actors`, `Person.directing_credits`, `Studio.films`, `Person.country.name` chain

## Volumes

- ~30 Movies, ~10 Series × ~5 Episodes each = ~50 Episodes, ~10 Games
- ~80 Persons, ~300 Credits, ~50 Identifiers
- ~5 Studios, ~20 Awards, ~10 Countries
- ~3 sources × full overlap = ~700 source rows total

## Edge cases — ALL categories from `../EDGE-CASES.md` at richer ontology than B2

C2 inherits B2's coverage (every category) and ADDS:

| Cat | Additional cases |
|---|---|
| 1 | 1.7 (non-Latin scripts), 1.10 (en-dash / em-dash), 1.14 (discriminator-routed sources), 1.15 (wildcard-drop) |
| 4 | 4.4 (polymorphic Identifier refs), 4.5 (cyclical → compile error mutation) |
| 5 | 5.4 (deep chains: `Episode.parent_series.creator.country.name`) |
| 6 | 6.8 (subclass hierarchy edit) |
| 8 | 8.1, 8.2, 8.3, 8.4, 8.5, 8.6 (full polymorphic + subclass suite) |
| 11 | 11.9 (deep slot.range across subclasses) |
| 14 | 14.5 (subclass-query materialization) |
| 15 | walk-back through subclass + polymorphic indirection |

## Why this tier is the default for structural stress

Rich ontology + multi-source surfaces every interaction between commitments simultaneously. If a structural primitive is broken, C2 catches it where B2 might pass on accident.

## Files

- `spec.py` — full Title hierarchy + Person + Credit + Identifier + Studio + Award + Country, with derivations
- `sources/` — many CSVs (one per source × leaf class)
- `impls/` — ER per class, Neo4j publisher (whole graph), Iceberg publisher
- `configs/`
- `expected_facts.yaml` — ground truth for subclass query results, polymorphic walks, derivation chains
- `edge_cases.yaml`
