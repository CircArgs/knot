# Reification strategy

**Status:** working draft — not locked. Captured during concretization pass.

The rule for deciding whether a multi-valued thing on an ontology class should become its own class.

## The rule

Reify when **any** of these hold:

- It has its own lifecycle (created, updated, deprecated independently of the parent).
- It has its own properties (not just a value — attributes attached to it).
- It has its own provenance worth tracking per-instance.
- It needs ER across sources (different sources name or shape it differently).

Otherwise, leave it as an array attribute on the parent.

## Examples

| Thing | Reify? | Why |
|---|---|---|
| Localized titles (akas) | Yes — `LocalizedTitle` | `(movie, region, language, type)`; per-row provenance; source already ships it as its own table. |
| Cast/crew credits | Yes — `Credit` | Per-credit attributes (character, billing, role category); source ships separately; ER across sources. |
| Cross-references / external IDs | Yes — `Identifier` | Sources revise identifier claims; per-fact provenance; cross-source agreement on `(system, value)` strengthens entity-level ER. |
| Ratings | Yes — `Rating` | Mutable, point-in-time, multiple raters; each rating is its own fact. |
| Genres | Yes — `Genre` | Cross-source vocabulary mismatch; Netflix micro-taxonomy is a first-class asset; worth attaching descriptions, hierarchy. |
| Regional age ratings (MPAA / BBFC / FSK) | Yes — `RegionalRating` | `(movie, region)`; separate authority per region; real lifecycle. |
| Availability windows (region × time) | Yes — `AvailabilityWindow` | `(movie, region, start, end)`; deal-level provenance. |
| Subtitle language codes | No — `Movie.subtitle_languages: List[str]` | Tag list. No per-entry attributes, no lifecycle, no ER. |
| Source's raw genre strings (pre-normalize) | No — stays as source-shape data | The reified `Genre` is what the ontology exposes; raw strings are normalize-stage input. |

## Cost

Effectively none, because of where each consumer sits:

- **Lake-side storage.** Reified things are already separate tables in source data; reifying preserves native shape, doesn't split Movie. No new tables, no fan-out.
- **Analyst queries on the lake.** Joins are expected — the lake is for analytics. Translator emits SQL when consumers query the ontology directly; even without it, an analyst writing SQL can join. No real cost.
- **General consumers (apps, services, search).** Don't hit the lake. They hit a materialized graph (the output of a bound materialization impl per `staging/pipeline-stages.md` § "Materialization"), either a generally-optimized resolved-entity graph or a purpose-built materialization for a hot query path. The graph is pre-shaped; reification at the ontology layer doesn't translate to runtime joins for these consumers.

Reification's tax is theoretical; the architecture absorbs it at every consumer surface.

## The one exception

`Genre` is the one case in the running examples where reifying requires work the source didn't pre-do. IMDb ships `genres` as a comma-string in `title.basics`. Reifying means normalize explodes the list (one row per `(title, genre)`).

Verdict: still reify. The cross-vocabulary ER win and the micro-taxonomy story justify the explode step. But it's the lone class where reification has a normalize-stage cost; everywhere else it's free.
