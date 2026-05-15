# Podcasts sample data — multi-source

Realistic three-source view of a podcast catalog, intended as a fixture
for knot's multi-source compile / resolve / merge path.

## Sources

- **`apple_podcasts/`** — feed-centric, US-iTunes-style metadata (Apple genre IDs, RSS feed URL, `country: "USA"`, language as `en-US`).
- **`spotify/`** — distribution-centric (Spotify URIs, market availability, externally-hosted flag, language as `en`).
- **`listennotes/`** — aggregator metadata (listen score, total plays, listennotes catalog URL, language as `English`).

Each source carries the same four entity files:

```
{source}/
  podcasts.json
  persons.json
  episodes.json
  credits.json
```

## Entities and required slots

| Entity   | Required slots (modelled)                                                                |
| -------- | ---------------------------------------------------------------------------------------- |
| Person   | `canonical_id`, `name`, `role_description`                                               |
| Podcast  | `canonical_id`, `title`, `publisher`, `language`, `category`, `start_year`               |
| Episode  | `canonical_id`, `podcast`, `episode_number` (nullable), `title`, `air_date`, `duration_minutes` |
| Credit   | `canonical_id`, `role`, `episode` (nullable), `podcast` (nullable), `person`             |

Every row also carries:

- **`source_identifier`** — the source-native natural ID (Apple ID, Spotify URI, listennotes ID).
- **3-8 source-specific raw fields** — unmodelled extras that knot routes into `raw_payload jsonb` on the source-bound staging table.

### Source-specific raw fields

| Source            | Raw fields                                                                                     |
| ----------------- | ---------------------------------------------------------------------------------------------- |
| `apple_podcasts`  | `apple_id`, `feed_url`, `track_count`, `country`, `artwork_url`, `genre_ids` (list)            |
| `spotify`         | `spotify_uri`, `total_episodes`, `external_url`, `is_externally_hosted`, `available_markets` (list) |
| `listennotes`     | `listennotes_id`, `listen_score`, `total_episodes`, `total_plays`, `extra_url1`, `country`     |

## Volumes

| Source            | Podcasts | Persons | Episodes | Credits |
| ----------------- | -------: | ------: | -------: | ------: |
| `apple_podcasts`  |       30 |      88 |      123 |     177 |
| `spotify`         |       28 |      88 |      109 |     162 |
| `listennotes`     |       31 |      90 |      125 |     182 |

## Overlap (average pairwise, by canonical_id)

| Entity        | Overlap |
| ------------- | ------: |
| Podcasts      |   ~55%  |
| Persons (blended) | ~61%  |
| Persons (hosts only) | ~86% |

Hosts overlap far more than guests — every source that carries a given
show carries its hosts too, while episode-level guest credits frequently
drop on one source and survive on another. This mirrors the real world:
aggregators agree on who hosts a show, but disagree on the long tail of
named guests per episode.

## Disagreement patterns seeded

These are the disagreements knot's resolver/merger must handle:

1. **Language code spelling.** A podcast in English appears as
   `language="en-US"` on Apple, `"en"` on Spotify, `"English"` on
   listennotes. UK shows: `en-GB` / `en-GB` / `British English`.

2. **Category vocabulary.** The Lex Fridman Podcast is `"Tech"` on Apple,
   `"Technology"` on Spotify, `"Science & Technology"` on listennotes. Every
   category has a per-source spelling (`CATEGORY_VARIANT` in the generator).

3. **Episode duration off-by-one.** Apple ships RSS-reported runtime,
   Spotify trims intro/outro telemetry so it lands a minute short ~60% of
   the time, listennotes occasionally rounds up. 92 episodes (~75%) carry
   a duration disagreement across at least two sources.

4. **Episode air-date by one day.** Spotify timestamps in UTC; for shows
   that publish late in a US timezone, the rollover lands the prior day on
   Spotify but the listed day on Apple/listennotes. 22 episodes (~18%)
   carry a date disagreement.

5. **Source-specific natural IDs.** `source_identifier` is wholly
   different per source — knot's resolver matches on `canonical_id` (the
   pre-supplied truth in this fixture), not on `source_identifier`.

6. **Role-string drift.** A small fraction of credits use synonyms on
   Spotify (`"presenter"` for `"host"`, `"composer"` for `"music"`) and
   listennotes (`"interviewee"` for `"guest"`).

7. **Episode-level guest credit drop-out.** Apple may have a credit row
   for Andrej Karpathy on Lex #398 while listennotes lacks it — realistic
   given aggregators don't always backfill named guest lists.

## How a typical disagreement looks

For `ep_jre_2120` ("Andrew Huberman" on JRE):

| Source            | duration_minutes | air_date     |
| ----------------- | ---------------: | ------------ |
| `apple_podcasts`  |              178 | `2024-03-12` |
| `spotify`         |              177 | `2024-03-12` |
| `listennotes`     |              178 | `2024-03-12` |

For `pod_lex` (Lex Fridman Podcast):

| Source            | language      | category               |
| ----------------- | ------------- | ---------------------- |
| `apple_podcasts`  | `en-US`       | `Tech`                 |
| `spotify`         | `en`          | `Technology`           |
| `listennotes`     | `English`     | `Science & Technology` |

## Notes

- The fixture pre-supplies `canonical_id` on every row. In a real
  pipeline, those IDs are what the entity-resolution stage produces; here
  we hand them out so downstream tests can ignore ER and focus on
  trust-resolved merging, constraint validation, and materialization.
- All FKs (`Episode.podcast`, `Credit.person`, `Credit.episode`,
  `Credit.podcast`) refer to canonical_ids, not source identifiers.
- File format: pretty-printed JSON arrays of dicts, UTF-8, one file per
  (source, entity) pair. No CSV, no NDJSON.
