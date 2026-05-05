# Modeling walkthrough — worked example

**Status:** staging — illustrative walkthrough captured for design review. Not authoritative.

A concrete end-to-end modeling exercise showing how knot's authoring + spec mechanics work for a small media-graph ontology. Used to ground the design discussion; not the canonical Movie/Person/Credit ontology.

## Pre-existing state

Sources `imdb_movies`, `imdb_persons`, `imdb_credits`, `xref_identifiers` are already declared in the lake (team-side ingestion, out of scope per `source-layer-contract.md`).

## Ontology so far

```
Movie       slots: title, year(min=1888,max=2100)
Person      slots: name
Credit      slots: role, work→Movie, person→Person, character
Identifier  slots: entity_class, entity_src_key, system, value
            identifier_pattern: class_slot=entity_class, key_slot=entity_src_key
```

## Source bindings so far

```
imdb_movies        → Movie    (title→title, release_year→year)
imdb_persons       → Person   (primary_name→name)
imdb_credits       → Credit   (role→role, character→character,
                               tt_id FK→work via imdb_movies.tt_id,
                               nm_id FK→person via imdb_persons.nm_id)
xref_identifiers   → Identifier (entity_class, entity_src_key, system, value)
```

The spec was published with these. One source per class — ER is degenerate at this point.

---

## New source: wikidata (heterogeneous, discriminator-routed)

Wikidata produces rows for many entity types. We use the `instance_of` column as a discriminator to dispatch each row to the right ontology class.

### Source declaration

```
POST /sources
{
  "name": "wikidata_extract",
  "location": "lake://wikidata_extract",
  "shape": {
    "columns": [
      {"name": "qid",          "range": "string", "identifier": true},
      {"name": "label_en",     "range": "string"},
      {"name": "instance_of",  "range": "string"},
      {"name": "release_year", "range": "integer"},
      {"name": "runtime_min",  "range": "integer"},
      {"name": "imdb_id",      "range": "string"},
      {"name": "tmdb_id",      "range": "string"},
      {"name": "birth_year",   "range": "integer"},
      {"name": "ingested_at",  "range": "datetime", "watermark": true}
    ]
  }
}
```

### Mapping (discriminator-routed)

```
POST /sources/wikidata_extract/mapping
{
  "discriminator": "instance_of",
  "mappings": {
    "Q11424": {                                  # wikidata's class for "film"
      "ontology_class": "Movie",
      "fields": {
        "qid":          "source_natural_key",
        "label_en":     "title",
        "release_year": "year"
      }
    },
    "Q5": {                                      # wikidata's class for "human"
      "ontology_class": "Person",
      "fields": {
        "qid":      "source_natural_key",
        "label_en": "name"
      }
    },
    "*": { "drop": true }                        # all other instance_of values dropped
  }
}
```

In-memory Pydantic shape:

```python
Source(
    name="wikidata_extract",
    location="lake://wikidata_extract",
    columns=[<col>, <col>, ...],
    mapping=DiscriminatorRoutedMapping(
        discriminator_column=<instance_of col>,
        routes=[
            DiscriminatorRoute(
                value="Q11424",
                target=SourceMapping(
                    ontology_class=movie_class,
                    fields=[
                        FieldMap(source_column=<qid>, target=SourceNaturalKey()),
                        FieldMap(source_column=<label_en>, target=movie_title_slot),
                        FieldMap(source_column=<release_year>, target=movie_year_slot),
                    ],
                ),
            ),
            DiscriminatorRoute(
                value="Q5",
                target=SourceMapping(
                    ontology_class=person_class,
                    fields=[
                        FieldMap(source_column=<qid>, target=SourceNaturalKey()),
                        FieldMap(source_column=<label_en>, target=person_name_slot),
                    ],
                ),
            ),
            DiscriminatorRoute(
                value="*",
                target=DropRoute(),                    # explicit drop wildcard
            ),
        ],
    ),
)
```

When normalize runs against `wikidata_extract`, knot reads each row, looks up `instance_of`, applies the matched route's mapping. Q11424 rows produce per-source-facts under Movie; Q5 rows under Person; everything else dropped.

**Strictness:** any `instance_of` value not in `mappings` (and no `"*"` wildcard) fails the run loudly per `source-layer-contract.md`. Here we have the wildcard drop.

### Identifier extraction (separate concern)

Wikidata's strength is its cross-reference columns (`imdb_id`, `tmdb_id`). To turn those into Identifier facts, knot would have a separate source mapping derived from the same lake table (or a separate normalize step that emits Identifier rows). That's a richer pattern than this walkthrough explores — for now we leave wikidata producing Movie + Person and treat cross-references as a follow-on.

---

## New source: netflix_internal (homogeneous, single-class)

Netflix's internal title catalog. Just movies for this walkthrough.

### Source declaration

```
POST /sources
{
  "name": "netflix_internal",
  "location": "pg://netflix_warehouse.titles_movie",
  "shape": {
    "columns": [
      {"name": "nflx_id",     "range": "string", "identifier": true},
      {"name": "title",       "range": "string"},
      {"name": "year",        "range": "integer"},
      {"name": "runtime_min", "range": "integer"},
      {"name": "ingested_at", "range": "datetime", "watermark": true}
    ]
  }
}
```

(Note the postgres-backed lake URI — different scheme than imdb's lake://. Resolved by the bound `QueryReader` (per `query-executor.md`) for this deployment.)

### Mapping (homogeneous)

```
POST /sources/netflix_internal/mapping
{
  "ontology_class": "Movie",
  "fields": {
    "nflx_id": "source_natural_key",
    "title":   "title",
    "year":    "year"
  }
}
```

In-memory:

```python
Source(
    name="netflix_internal",
    location="pg://netflix_warehouse.titles_movie",
    columns=[...],
    mapping=SourceMapping(
        ontology_class=movie_class,
        fields=[
            FieldMap(source_column=<nflx_id>, target=SourceNaturalKey()),
            FieldMap(source_column=<title>, target=movie_title_slot),
            FieldMap(source_column=<year>, target=movie_year_slot),
        ],
    ),
)
```

Single-class mapping — a thin Pydantic case of the discriminator-routed mapping (one route, no discriminator column needed). The API surface might offer this as a shorthand (`mapping:` singular per `source-layer-contract.md`'s "Homogeneous shorthand").

---

## State after both sources added

```
Movie       sources:  imdb_movies, wikidata_extract (Q11424), netflix_internal
Person      sources:  imdb_persons, wikidata_extract (Q5)
Credit      sources:  imdb_credits
Identifier  sources:  xref_identifiers
```

Movie now has **three sources**. Person has **two**. Credit and Identifier still have one each.

ER becomes meaningful:

- For Movie: "is the imdb tt0068646 row the same canonical Movie as the wikidata Q47703 row as the netflix nflx_12345 row?" This is the dedup question ER strategies exist to answer (blocking on year/runtime, matching on title, cross-refs via shared identifiers).
- For Person: same — imdb's nm0000338 vs wikidata's Q42410 might both represent the same person.
- For Credit / Identifier: still degenerate (one source each); ER would be a no-op until additional sources arrive.

### Republish

```
POST /spec/publish
```

The published spec now carries three sources for Movie and two for Person. Workflow compiles for Movie or Person ER will see all relevant per-source-facts at resolve time. The compile hash changes (new sources = new spec content).

If any registered ER impl had config referencing the new sources (or relied on slot/range constraints that the new sources don't conform to), the publish gate would catch it. Here we have no impls registered yet, so it goes through.

---

## What this exercises

- **Heterogeneous source dispatch** via discriminator (`wikidata.instance_of`).
- **Strict-with-wildcard-drop** dropout pattern (`"*": {drop: true}`) — explicit, auditable.
- **Different lake URI schemes** in the same deployment (`lake://`, `pg://`).
- **Homogeneous single-class shorthand** (netflix_internal).
- **Multi-source same class** — Movie now has 3 sources, ER becomes a real concern.
- **All Pydantic, real refs** — the API receives JSON; the persistence boundary rehydrates name-keyed strings (e.g., `"Movie"`) into real `OntologyClass` references.

## What this doesn't yet exercise

- Identifier extraction from wikidata's cross-ref columns.
- Netflix's series / episode hierarchy (would need additional ontology classes).
- Discriminator routing for sources with overlapping target classes (e.g., a row mapping to BOTH Movie AND Identifier).
- Source-derivation (one source's rows producing other sources' rows, e.g., normalize emitting both Movie facts and Identifier facts from a single imdb row).

These are open follow-ons; the walkthrough above is enough to reach a state where ER on Movie and Person becomes a meaningful design problem to discuss.
