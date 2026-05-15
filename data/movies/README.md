# data/movies — multi-source movie sample data

Synthetic, hand-curated movie-domain data covering three sources:
**imdb**, **tmdb**, and **rottentomatoes**. Built to exercise knot's
per-(source, class, slot) trust resolver against realistic overlap
and disagreement patterns.

Not real scraped data. All `source_identifier` values are deterministic
hashes shaped like real IDs (`tt0123456`, tmdb integer ids, slug strings),
and the raw-payload values for things like `imdb_rating`, `tagline`, or
`aspect_ratio` are randomized — they are present to demonstrate that the
bronze layer preserves raw source fields, not to be factually accurate.

## Schema (the slots knot models)

Three classes, all with `canonical_id` as the stable cross-source identity:

| Class    | Stored slots                                                |
|----------|-------------------------------------------------------------|
| `Person` | `name`, `birth_country`, `birth_year`                       |
| `Movie`  | `title`, `year`, `runtime_minutes`, `director` (FK Person)  |
| `Credit` | `role`, `movie` (FK Movie), `person` (FK Person)            |

Plus on every row: `canonical_id` (knot's identity) and
`source_identifier` (the source's natural id).

`role` values present: `actor`, `director`, `writer`, `cinematographer`,
`composer`.

## Source-specific raw fields

Any field on a row beyond the schema slots above lands in the bronze-layer
`raw_payload jsonb` column on ingest. Each source publishes its own grab-bag:

**imdb** — `imdb_rating`, `num_votes`, `mpaa_rating`, `aspect_ratio`,
`color_info`, `box_office_usd`, `country_of_origin`, `primary_profession`,
`known_for_titles`, `height_cm`, `imdb_credit_category`, `ordering`.

**tmdb** — `tmdb_id`, `popularity`, `vote_average`, `vote_count`,
`original_language`, `tagline`, `genres`, `adult`, `status`,
`tmdb_person_id`, `known_for_department`, `gender`, `profile_path`,
`also_known_as`, `tmdb_credit_id`, `department`, `job`, `order`.

**rottentomatoes** — `tomatometer`, `audience_score`, `critics_consensus`,
`synopsis`, `review_count`, `audience_count_thousands`, `rt_slug`,
`highest_rated_movie_score`, `lowest_rated_movie_score`,
`filmography_count`, `billing_position`, `character_name`.

## Layout

```
data/movies/
  README.md                        # this file
  imdb/
    persons.json                   # 107 rows
    movies.json                    #  70 rows
    credits.json                   # 132 rows
  tmdb/
    persons.json                   #  80 rows
    movies.json                    #  68 rows
    credits.json                   #  93 rows
  rottentomatoes/
    persons.json                   #  81 rows
    movies.json                    #  65 rows
    credits.json                   # 133 rows
```

Each file is a JSON array of dicts (one dict per entity).

## Overlap and disagreement (designed to exercise the resolver)

### Coverage / overlap

- **98 distinct canonical Movies** across the three sources.
  - 30 appear in all three sources (`imdb` ∩ `tmdb` ∩ `rottentomatoes`).
  - 75 appear in two or more sources.
  - 23 appear in exactly one source (single-source).
- **111 distinct canonical Persons** across the three sources.
  - 59 appear in all three sources.
  - 98 appear in two or more sources.
  - 13 appear in exactly one source.

Source-specific catalog quirks:

- **imdb** is the broadest catalog (people index especially).
- **tmdb** drops a couple of pre-1930 titles entirely
  (e.g., *Battleship Potemkin*, *The Gold Rush*) and omits a small
  set of niche writers / composers (e.g., Sergei Eisenstein has no
  tmdb person record).
- **rottentomatoes** is patchier on pre-1960 classics, omits a few
  arthouse titles (e.g., *Taxi* (Panahi 2015), *Close-Up*, *Persona*),
  and its people index doesn't cover everyone who appears in its films.

### Slot-level disagreements (intentional)

These are wired in to give the resolver real conflicts to choose between
based on per-(source, class, slot) trust:

**Year disagreements (`Movie.year`)**

| canonical_id     | imdb | tmdb | rottentomatoes | note                              |
|------------------|------|------|----------------|-----------------------------------|
| `m_battleship`   | 1925 | 1925 | 1926           | rt off-by-one                     |
| `m_generalkeaton`| 1926 | 1927 | 1927           | imdb dissents                     |
| `m_boyandheron`  | 2023 | 2023 | 2024           | JP release vs. US release         |
| `m_paindglory`   | 2019 | 2019 | 2020           | festival vs. wide release         |

**Runtime disagreements (`Movie.runtime_minutes`)**

Most movies have small (±1 min) jitter to simulate per-source minor
inconsistency. A few are deliberately split because real catalogs publish
different cuts:

| canonical_id      | imdb | tmdb | rottentomatoes | note                        |
|-------------------|------|------|----------------|-----------------------------|
| `m_2001`          | 149  | 149  | 161            | rt picks original 1968 cut  |
| `m_apocalypsenow` | 147  | 153  | 202            | theatrical / Redux / Final  |
| `m_godfather`     | 175  | 177  | 175            | tmdb counts extra credits   |
| `m_sevensamurai`  | 207  | 207  | 208            | minor frame-rate variation  |

**Title disagreements (`Movie.title`)**

13 movies have title variants. Examples:

- *Kill Bill: Vol. 1* (imdb, rt) vs. *Kill Bill: Volume 1* (tmdb)
- *8½* (imdb) vs. *8 1/2* (tmdb, rt)
- *Seven Samurai* (imdb, rt) vs. *Shichinin no samurai* (tmdb, original-language)
- *Breathless* (imdb, rt) vs. *À bout de souffle* (tmdb)
- *Dr. Strangelove* (tmdb, rt) vs. *Dr. Strangelove or: How I Learned to Stop Worrying and Love the Bomb* (imdb)
- *Metropolis* (imdb, tmdb) vs. *Metropolis (restored)* (rt)

**Person-name spelling variants (`Person.name`)**

13 persons have name spelling variants. Examples:

- `p_kurosawa`: *Akira Kurosawa* (imdb, tmdb) vs. *Kurosawa, Akira* (rt, last-name-first)
- `p_almodovar`: *Pedro Almodóvar* (imdb, tmdb) vs. *Pedro Almodovar* (rt, no diacritic)
- `p_chalamet`: *Timothée Chalamet* (imdb, tmdb) vs. *Timothee Chalamet* (rt)
- `p_song_kh`: *Song Kang-ho* (imdb), *Song Kang-Ho* (tmdb), *Kang-ho Song* (rt, given-first)
- `p_godard`: *Jean-Luc Godard* (imdb, rt) vs. *Jean Luc Godard* (tmdb)

**Birth-year disagreements (`Person.birth_year`)**

| canonical_id    | imdb | tmdb | rottentomatoes | note                          |
|-----------------|------|------|----------------|-------------------------------|
| `p_brando`      | 1924 | 1924 | 1923           | rt off-by-one                 |

(Most other classic persons agree across sources; only Brando dissents
here, by design.)

**Birth-country disagreements (`Person.birth_country`)**

| canonical_id    | imdb    | tmdb              | rottentomatoes | note                                              |
|-----------------|---------|-------------------|----------------|---------------------------------------------------|
| `p_lang`        | Austria | Austria-Hungary   | Austria        | tmdb uses historical name                         |
| `p_eisenstein`  | Russia  | Russian Empire    | Latvia         | actual ambiguity — born in Riga                   |

### Cross-source FK references

Some `Movie.director` FKs point to a `Person` canonical_id that *this
source* does not publish (e.g., a tmdb movie row whose director is only
in imdb's persons file). This is realistic — sources publish partial
slices of the same logical graph — and it's exactly the kind of
cross-source link the knot resolver follows by canonical_id:

| source         | movies whose director is not in this source's persons.json |
|----------------|------------------------------------------------------------|
| imdb           |  4                                                         |
| tmdb           | 22                                                         |
| rottentomatoes |  2                                                         |

## Canonical id conventions

- Persons: `p_<lastname-or-tag>` (e.g., `p_kurosawa`, `p_anderson_pt`
  for Paul Thomas, `p_anderson_wes` for Wes — disambiguated by tag).
- Movies: `m_<slug>` (e.g., `m_pulpfiction`, `m_sevensamurai`,
  `m_bladerunner2049`).
- Credits: `c_<movie-slug>_<role>_<person-slug>`.

The same `canonical_id` always refers to the same logical entity
across sources; each source-row also publishes its own
`source_identifier` (imdb tconst/nconst, tmdb integer id, rt slug).

## Coverage range

Year span: **1925 - 2024** (silents from Eisenstein, Chaplin, Keaton,
Lang through 2024's *Dune: Part Two*). Filmmakers represented include
Tarantino, Kurosawa, Miyazaki, Scorsese, Kubrick, Spielberg, Coppola,
Nolan, Villeneuve, PT Anderson, Wes Anderson, Fincher, Lynch, Jarmusch,
Bong Joon-ho, Park Chan-wook, Almodóvar, Haneke, Chazelle, Gerwig,
Jenkins, Peele, Eggers, Aster, Lanthimos, Zhao, Panahi, Kiarostami,
Eisenstein, Chaplin, Keaton, Lang, Welles, Hitchcock, Godard, Truffaut,
Bergman, Fellini, Tarkovsky, Ozu.

## How a host would use this

```python
import json
from pathlib import Path

for source in ("imdb", "tmdb", "rottentomatoes"):
    for entity in ("persons", "movies", "credits"):
        rows = json.loads(Path(f"data/movies/{source}/{entity}.json").read_text())
        # Each row has knot's required (canonical_id, source_identifier)
        # plus the schema slots and arbitrary source-specific raw fields.
        # The host emits them through knot's compiled write path; per-slot
        # trust then resolves conflicts when the same canonical_id is
        # published by multiple sources.
        ...
```

The library does not consume these files directly — they're fixture data
a reference adapter (or a notebook) can hand to knot's compiled
ingest/resolver path.
