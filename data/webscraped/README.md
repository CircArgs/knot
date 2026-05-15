# data/webscraped/ — messy multi-source fixture

This dataset exists to prove one specific claim about knot:

> **The bronze-layer `raw_payload jsonb` preserves *everything* a source
> sent, even when the spec only models a handful of fields.**

Where `data/movies/`, `data/B2/sources/`, etc. carry clean per-source
tables (one schema per CSV, every row has every column), this dir
carries the opposite: **irregular, low-trust web-scraped sources** where
field names are inconsistent, rows are partial, fields vary across rows
*within* a single source, and most of what's scraped doesn't map onto a
knot-spec slot at all.

A scraper-vendor pipeline is the right mental model: an upstream
crawler hits Reddit / Letterboxd / a fan wiki / a blog aggregator, does
some entity-resolution on its side, and ships JSON. The compiler can
either (a) pretend the schema is regular and lose 90% of the payload,
or (b) keep the irregular payload verbatim in `raw_payload` and only
project the small subset of fields the spec declares. We do (b).

## Sources

```
data/webscraped/
  reddit_film_discussion/      132 rows
  letterboxd_user_reviews/     141 rows
  fan_wiki/                     96 rows
  blog_review_aggregator/      118 rows
```

| Source | Quirk |
|---|---|
| `reddit_film_discussion` | comment data scraped from r/movies, r/TrueFilm, r/criterion, etc. Subreddit-flavored fields (`subreddit`, `score`, `gilded`, `upvote_ratio`, `permalink`, optional `parent_id` / `depth`). Movie references come from comment *text*, often misspelled. |
| `letterboxd_user_reviews` | semi-structured user reviews. `rating_stars` 0.5-5.0 in half-star increments (with occasional null and a few malformed string values like `"4/5"`). Diary-entry, rewatch, spoilers flags. `letterboxd_uri` deep links. |
| `fan_wiki` | wiki-style entries with a nested `infobox` dict whose key set *varies row-to-row*. `categories` list, `contributors`, `last_edited`, sometimes `external_links`, sometimes `page_views_30d`. Some pages are about a person, most about a movie, a few are off-topic list pages. |
| `blog_review_aggregator` | scraped blog posts from indie film criticism sites. `url`, `author`, `headline`, `body_html_stripped`, sometimes `score_out_of_10` (a notoriously inconsistent field — sometimes float, sometimes int, sometimes a `"7/10"` string, sometimes `"N/A"`, sometimes absent). |

## Shape

Every row across all four sources carries the **same five "spec"
fields** — the only ones a knot spec for `Mention` would actually model:

| field | type | meaning |
|---|---|---|
| `canonical_id` | str | knot's stable ID for this scraped artifact (e.g. `mention_reddit_0042`) |
| `source_identifier` | str | URL or post ID, the source-native handle |
| `subject_movie` | str \| null | FK to canonical Movie (e.g. `m_pulpfiction`), or null |
| `subject_person` | str \| null | FK to canonical Person (e.g. `p_tarantino`), or null |
| `text_excerpt` | str | a short text snippet from the artifact |
| `sentiment` | str \| null | `"positive"`, `"negative"`, `"neutral"`, or null |

The subject FKs are the realistic bit: the source itself doesn't know
about `m_pulpfiction` natively — it knows about the title "Pulp Fiction"
(or "pulp fiction", or "Pulp Fiction (1994)"). The scraper-vendor's ER
pipeline has already resolved those mentions to knot canonical IDs
before delivering the rows. Some rows have a movie but no person; some
have a person but no movie; some have both (a comment about a director
and one of their films); some have neither (off-topic comments,
category index pages).

Every row *also* carries 5–15 source-specific raw fields beyond the
spec-modelled ones. **Those are the point of the fixture.** A knot
`SourceBinding` for `Mention` would project only the six spec fields
into typed columns; everything else lands in `raw_payload jsonb`. If
the spec evolves later to model `rating_stars` or `score`, the data is
already there in `raw_payload` — no re-scrape required.

## Deliberate messiness

The dataset embeds the kinds of problems that real scrapes have. Each
of these should be a no-op for a spec that only declares the six fields
above — the messy stuff sits in `raw_payload` and is the host's problem
if they ever want to lift it into the spec.

1. **Inconsistent field presence.** ~30% of rows are missing optional
   source-specific fields. Two rows from the same source can have wildly
   different key sets. There is no "schema" in the regular-CSV sense.

2. **Source-specific fields with no spec equivalent.** Examples:
   `upvote_ratio`, `gilded`, `awards_received`, `bot_detection_score`,
   `flair`, `is_submitter`, `edited`, `parent_id`, `depth` (Reddit);
   `liked`, `rewatch`, `is_diary_entry`, `contains_spoilers`,
   `letterboxd_uri`, `tags`, `likes_count` (Letterboxd);
   `infobox.runtime`, `infobox.director`, `infobox.year`,
   `infobox.cinematography`, `categories`, `contributors`, `is_stub`,
   `page_views_30d` (wiki); `headline`, `body_html_stripped`, `tags`,
   `external_links`, `score_out_of_10`, `word_count`,
   `read_time_minutes`, `share_count`, `paywalled`, `editor_pick`,
   `byline` (blog).

3. **Mixed types on the same field name.**
   - `blog.score_out_of_10` appears as `int`, `float`, AND `str` across
     rows (`8`, `8.5`, `"8/10"`, `"N/A"`).
   - `letterboxd.rating_stars` is usually `float`, sometimes `null`,
     occasionally a malformed `str` like `"4/5"`.
   - `reddit.bot_detection_score` is sometimes a category string
     (`"low"` / `"medium"` / `"high"`) and sometimes a stringified
     probability (`"0.12"`).
   - `wiki.infobox.runtime` is usually `"X min"` but ~4% of rows have
     a bare `int`.
   - `blog.byline` is sometimes a `str` and sometimes a `list[str]`.

4. **Multiple date conventions.** Within a single source:
   - Reddit: unix epoch int in `created_utc` on some rows, ISO-8601
     string in `created_at` on others, `"Apr 15, 2023"` on yet others.
   - Letterboxd: `"2024-11-02"`, `"Apr 15, 2024"`, `"2024/06/01"`,
     `"08-22-2024"`, `"March 2, 2025"` all appear.
   - Wiki: ISO-8601 with timezone, ISO-8601 without, `"Apr 22, 2025"`.
   - Blog: ISO date, ISO date with slashes, long-form English, and a
     unix-epoch-as-string.

5. **Title variants & misspellings in `text_excerpt`.** The canonical
   ID is correct (the ER pipeline already handled that), but the actual
   text references the title as `"Pulp Fiction"`, `"pulp fiction"`,
   `"PulpFiction"`, `"Pulp Fiction (1994)"`, `"기생충"`, `"Parasite
   (Gisaengchung)"`, `"Inseption"` (sic), etc. — verbatim what the
   source said.

## Distribution

- **~100 distinct Movie canonical IDs** referenced across ~487 total
  rows. Heavy concentration on popular films (Pulp Fiction, The Dark
  Knight, Parasite, etc.) with a long tail of obscure titles (Stalker,
  Satantango, Petite Maman).
- **~37 distinct Person canonical IDs** referenced. Directors are
  over-represented in the wiki source; actors are over-represented in
  Reddit comments.
- **Subject combinations**:
  - Reddit: mostly movie-only, ~5% person-only, ~11% both, ~12% neither
    (off-topic comments).
  - Letterboxd: movie-only or movie+person (film-centric site).
  - Wiki: ~68% movie pages, ~27% person pages, ~5% off-topic list/
    category pages.
  - Blog: mostly movie-only, ~21% mention both a film and its director.

## Canonical IDs referenced

The `subject_movie` / `subject_person` columns reference canonical IDs
of the form:

- Movies: `m_<slug>` — e.g. `m_pulpfiction`, `m_lotr_fotr`,
  `m_everythingeverywhere`, `m_stalker1979`.
- Persons: `p_<slug>` — e.g. `p_tarantino`, `p_nolan`, `p_bongjoonho`,
  `p_emmastone`.

These IDs are independent of the IMDb-style `tt#######` / `nm#######`
identifiers used in `data/B2/sources/`. The webscraped fixture assumes
the host has a canonical-ID space populated by upstream sources and the
scraper-vendor's ER pipeline has resolved mentions into it. A real
integration test would either share canonical IDs with `data/B2/` (by
co-loading them) or treat the `m_*` / `p_*` IDs as a parallel namespace
referenced only by this dataset's `Mention` rows.

## Regeneration

Source generator: `/home/nick/.scratch/webscraped-gen/gen.py`. Seed is
fixed (`random.seed(20260515)`) so the dataset is reproducible. Re-run
the script to overwrite the four `mentions.json` files.
