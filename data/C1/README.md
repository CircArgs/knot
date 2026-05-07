# Tier C1 — 10+ classes, 1 source per leaf

**Status:** template only — fixture data not generated.

## Scope (planned)

- **Classes:** `Title` (abstract) → `Movie`, `Series`, `Episode`, `Game` subclasses; `Person`; `Credit`; `Identifier` (polymorphic); `Studio`; `Award`; `Country`
- **Sources:** one per leaf class (e.g., `imdb_movies`, `imdb_series`, `imdb_episodes`, `igdb_games`, `imdb_persons`, ...)
- Subclassing, polymorphic refs, deep DataContext expressions

## Volumes (planned)

- ~30 Movies, ~10 Series (each with ~5 Episodes), ~10 Games
- ~80 Persons, ~300 Credits, ~50 Identifiers (polymorphic across systems)
- ~5 Studios, ~20 Awards, ~10 Countries

## Edge cases additional to B-tier (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source normalization | 1.14 (discriminator-routed multi-class sources), 1.15 (wildcard-drop) |
| 4 — Cross-class relations | 4.4 (Identifier polymorphic refs), 4.5 (cyclical → compile error) |
| 5 — Derivations | 5.4 (chains: `Episode.parent_series.creator.country.name`) |
| 8 — Polymorphic / subclass | 8.1, 8.2, 8.3, 8.4, 8.5 (full subclass + polymorphic suite) |
| 11 — DataContext | 11.9 (deep `slot.range` chains across subclasses) |
| 14 — Materialization | 14.5 (subclass-query materialization: Title → all leaf nodes typed by subclass) |

## Why this tier might be needed

Subclass + polymorphic + derivation-chain regressions in isolation. C2 covers everything; C1 is for debugging structural regressions when multi-source noise is in the way.

## Files (when implemented)

- `spec.py` — Pydantic spec with full Title hierarchy + polymorphic Identifier
- `sources/<one csv per leaf class>`
- `impls/` — degenerate ER per class + materialization
- `expected_facts.yaml`, `edge_cases.yaml`
