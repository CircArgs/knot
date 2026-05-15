# `data/tv/` — TV sample data

Tight, hand-curated TV sample data for knot examples and tests. Three
sources (`tvdb`, `tmdb`, `imdb`), five entities each
(persons, shows, seasons, episodes, credits) for **15 JSON files
total**.

## Layout

```
data/tv/
  README.md
  tvdb/
    persons.json   shows.json   seasons.json   episodes.json   credits.json
  tmdb/
    persons.json   shows.json   seasons.json   episodes.json   credits.json
  imdb/
    persons.json   shows.json   seasons.json   episodes.json   credits.json
```

Each file is a JSON array of flat dicts. `canonical_id` is stable
across sources for the same real-world entity; `source_identifier` is
each source's natural ID (TVDB numeric id, TMDB numeric id, IMDB `tt…`/
`nm…` accession).

## Entities and slots

- **Person**: `canonical_id`, `source_identifier`, `name`,
  `birth_country`, `birth_year`
- **Show**: `canonical_id`, `source_identifier`, `title`, `start_year`,
  `end_year` (nullable), `network`, `status`
- **Season**: `canonical_id`, `source_identifier`, `show` (FK to Show),
  `season_number`, `episode_count`, `year`
- **Episode**: `canonical_id`, `source_identifier`, `season` (FK to
  Season), `episode_number`, `title`, `air_date`, `runtime_minutes`
- **Credit**: `canonical_id`, `source_identifier`, `role`, `show` (FK,
  may be null), `episode` (FK, may be null), `person` (FK to Person)

Credits hang off the show *or* the episode (exactly one, generally),
never both.

## Per-source extra raw fields

Each source carries 3-6 raw fields beyond the slot set, so binding
configs have something realistic to map / ignore:

- **tvdb**: `tvdb_id`, `slug`, `image_url`, `language`,
  `network_country`
- **tmdb**: `tmdb_id`, `vote_average`, `popularity`, `tagline`,
  `genres` (list), `original_language`
- **imdb**: `imdb_rating`, `votes`, `parental_rating`

## Scale

| entity   | tvdb | tmdb | imdb |
|----------|------|------|------|
| shows    |   30 |   36 |   20 |
| persons  |  121 |  113 |   50 |
| seasons  |   35 |   40 |   21 |
| episodes |   87 |   81 |   60 |
| credits  |  132 |  111 |   80 |

Episodes focus on Season 1 of a handful of shows (rather than
exhaustively covering every show's run) so the sample stays small and
tractable.

## Overlap (by canonical_id)

| entity   | tvdb&tmdb | tvdb&imdb | tmdb&imdb | all three |
|----------|-----------|-----------|-----------|-----------|
| shows    |        30 |        14 |        14 |        14 |
| persons  |        87 |        35 |        34 |        34 |
| seasons  |        35 |        15 |        15 |        15 |
| episodes |        74 |        39 |        35 |        35 |
| credits  |        92 |        30 |        30 |        30 |

`tvdb` and `tmdb` are near-complete mirrors (the canonical-id set was
seeded from tvdb), so almost every tvdb row has a tmdb counterpart.
`imdb` is the trimmed source — about half its rows match an
already-seen canonical_id; the rest are imdb-only entities (e.g.
*The Sopranos*, *Chernobyl*, *Game of Thrones*, *Peaky Blinders*,
*Better Call Saul*, *The Crown* and their cast/crew) so resolver tests
have a realistic mix of overlap and source-exclusive content.

## Disagreements between sources

Small, deliberate disagreements so resolver / trust-ordering tests have
something to chew on:

- `start_year` off-by-one for older shows (e.g. Twin Peaks: 1990 in
  tvdb/tmdb, 1989 in imdb).
- `status` strings differ in vocabulary across sources
  (`"ended"` / `"Ended"` / `"completed"` / `"continuing"` /
  `"Returning Series"` / `"Canceled"` / `"cancelled"`).
- `runtime_minutes` ±1 minute on some episodes.
- `title` casing/wording variants (e.g.
  `"Star Trek"` vs `"Star Trek: The Original Series"`,
  `"Scenes from a Marriage"` vs `"Scenes From a Marriage"`).
- `network` granularity varies (`"BBC Three"`,
  `"Channel 4 / Netflix"`, `"FX"` vs `"FX on Hulu"`).
- `birth_country` formatting (`"United States"` vs
  `"United States of America"`).

## Real shows in the mix

Breaking Bad, The Wire, Succession, Severance, Fleabag, Black Mirror,
The Bear, Twin Peaks, Star Trek (TOS), Squid Game, Lupin, Arcane,
Mad Men, BoJack Horseman, Atlanta, Cheers, The Last of Us, The Office
(US), Seinfeld, The Americans, True Detective, Scenes from a Marriage,
Parks and Recreation, The Marvelous Mrs. Maisel, Ted Lasso, The Morning
Show, Fargo, Hacks, Westworld, The Queen's Gambit, The White Lotus,
Andor, Schitt's Creek, Money Heist, Abbott Elementary, Somebody
Somewhere, The Sopranos (imdb-only), Chernobyl (imdb-only), Game of
Thrones (imdb-only), Peaky Blinders (imdb-only), Better Call Saul
(imdb-only), The Crown (imdb-only).

## What this data is *not*

- Not the result of an API pull — values are plausible but
  hand-authored. Numeric IDs (tvdb_id, tmdb_id, imdb tt-numbers) are
  real where convenient and synthetic where not; treat them as test
  fixtures, not as a source of truth.
- Not exhaustive — episodes only cover S1 of a few shows, and credits
  are sparse cast/creator lists, not full crew listings.
- Not consistent in language across all sources — that's the point;
  the resolver has to handle small disagreements.
