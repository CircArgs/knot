# Sample data — video games

Multi-source sample fixture for the `knot` library, exercising
overlapping entities across three game-catalog sources.

## Sources

- **igdb** — IGDB (Internet Game Database). Most complete; deep
  metadata, themes, game modes, ratings.
- **giant_bomb** — Giant Bomb. Editorial deck/aliases, concepts list,
  curated platform/release coverage.
- **steam** — Steam storefront. PC-skewed; rich commercial metadata
  (price, recommendations, current players, tags) but shallow
  credit/role attribution.

## Entities

Six classes per source where applicable:

| Class      | Slots                                                                            |
|------------|----------------------------------------------------------------------------------|
| `Studio`   | canonical_id, name, country, founded_year                                        |
| `Game`     | canonical_id, title, release_year, studio (FK), genre, platform_summary          |
| `Platform` | canonical_id, name, manufacturer                                                 |
| `Release`  | canonical_id, game (FK), platform (FK), release_date, region                     |
| `Person`   | canonical_id, name, role_summary                                                 |
| `Credit`   | canonical_id, game (FK), person (FK), role                                       |

## Conventions

- `canonical_id` is **stable across sources** for the same entity
  (`g_eldenring`, `st_fromsoft`, `pl_ps5`, `p_miyazaki_h`, etc.). The
  resolver uses these to fuse rows.
- `source_identifier` is the natural ID inside the source (igdb_id as
  string, gb_id as string, steam app_id as string).
- Each row carries 3-8 **source-specific raw fields** beyond the
  modeled slots. These flow through to `raw_payload jsonb`
  automatically and are not lost.
- Disagreements between sources are intentional. Examples:
  - `g_cyberpunk` release_year — igdb/gb say 2020, steam says 2020
    but with `early_access`-style tag noise.
  - Studio attribution — igdb says "Nintendo EAD", gb says "Nintendo",
    steam doesn't list the studio at all on some first-party titles.
  - Platform naming — "PlayStation 5" vs "PS5" vs "PlayStation®5".

## Layout

```
data/games/
  README.md
  igdb/
    studios.json games.json platforms.json
    releases.json persons.json credits.json
  giant_bomb/
    (same six)
  steam/
    studios.json    # publishers, not developers
    games.json      # PC-available subset
    platforms.json  # PC-skewed; mostly "PC" + a few consoles via remote-play
    releases.json
    persons.json    # sparse; Steam doesn't credit deeply
    credits.json    # sparse
```

## Overlap shape

- ~60% of igdb games also exist in giant_bomb and steam.
- ~50% of studios overlap across sources.
- Platforms overlap heavily between igdb + giant_bomb; steam knows
  mostly "PC".
- Persons + credits are dense in igdb/giant_bomb, sparse in steam.

## Scale

| File              | igdb | giant_bomb | steam |
|-------------------|------|------------|-------|
| studios.json      | ~30  | ~30        | ~30   |
| games.json        | ~60  | ~55        | ~50   |
| platforms.json    | ~25  | ~25        | ~10   |
| releases.json     | ~140 | ~120       | ~100  |
| persons.json      | ~70  | ~65        | ~20   |
| credits.json      | ~130 | ~110       | ~25   |
