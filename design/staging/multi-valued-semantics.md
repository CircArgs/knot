# Multi-valued slot semantics — lens-driven defaults, protocol-encoded

**Status:** decided. Integrates with commitments 4, 7, 11. Supersedes the loose "three SDK access modes" sketch in earlier drafts of `core-design.md` § 7.

---

## The shape of the problem

At the canonical layer every property is implicitly multi-valued. `Movie.year` for canonical entity `mov_x9k2` is a set of contributions:

```text
[
    {source: "imdb",     value: 1999, trust: 0.91, asserted_at: ...},
    {source: "tmdb",     value: 2000, trust: 0.62, asserted_at: ...},
    {source: "wikidata", value: 1999, trust: 0.78, asserted_at: ...},
]
```

When an SDK expression refers to `Movie.year`, that name *cannot* mean only one thing across all call sites. The reference reaches the SDK from places with different jobs:

- **ER scoring** wants source-by-source comparison; if `Movie.year` silently resolves to a winner, ER can't see the disagreement it exists to resolve.
- **Materialization** wants one value per canonical entity; resolved facts in Neo4j can't be `[1999, 2000, 1999]`.
- **Translator** answers consumer queries that have no concept of multi-source disagreement; the consumer typed "year > 1990" once.
- **Constraint and derivation evaluation** operates on a single canonical entity at a time; constraints over multi-valued bags are a different shape.
- **DQ** specifically wants disagreement: cross-source agreement, drift, source-coverage drop are *about* the bag.

A single fixed default fails at one extreme or the other. A "no default ever, always be explicit" rule is correct but punishes the 90% case where the impl writer wants the curated single answer.

## The decision — protocol-encoded disagreement stance

Every Protocol declares a `disagreement_stance`. The SDK type generator reads it and emits different shapes for the same slot reference:

```python
class ProtocolKind(str, Enum):
    DISAGREEMENT_AWARE = "disagreement_aware"  # ER, DqRunner: source bag is the input
    RESOLVED = "resolved"                      # Materializer, Translator, ConstraintEval, DerivationEval: single curated answer
```

Per-protocol assignment:

| Protocol | Stance | Why |
|---|---|---|
| `ERProtocol` | `DISAGREEMENT_AWARE` | ER's input IS source disagreement |
| `DqRunner` | `DISAGREEMENT_AWARE` | Cross-source agreement is half the built-in checks |
| `Materializer` | `RESOLVED` | Targets store one value per canonical entity |
| `Translator` | `RESOLVED` | Consumer queries don't model sources |
| `ConstraintEvaluator` (knot-internal) | `RESOLVED` | Constraints reason about canonical state |
| `DerivationEvaluator` (knot-internal) | `RESOLVED` | A derived `decade = year // 10 * 10` wants one year |

The stance is a property of the protocol, not the pipeline stage. ER and Materialization are both impl-facing, but they take opposite stances because their jobs differ.

## What the SDK emits per stance

For a slot `Movie.year` of range `int`:

### Under `RESOLVED` lens

```python
class Movie:
    year: Resolved[int]
    # __gt__, __lt__, __eq__, .in_, .between, etc. all work
    # Bare `Movie.year > 1900` compiles to a Compare node over the resolved view
```

The bare form resolves via the slot's `resolution_policy` (see below). Escape hatches still exist:

```python
Movie.year.from_source(Source.imdb) > 1900   # specific source
Movie.year.all_() > 1900                     # forall — every source agrees
Movie.year.any_() > 1900                     # exists — any source agrees
Movie.year.contributions()                   # raw list[Contribution[int]]
```

### Under `DISAGREEMENT_AWARE` lens

```python
class Movie:
    year: MultiValued[int]
    # __gt__, __lt__, __eq__ are NOT defined on MultiValued
    # Bare `Movie.year > 1900` is a type error from mypy
```

The user must spell their reduction:

```python
Movie.year.from_source(Source.imdb) > 1900   # works
Movie.year.all_() > 1900                     # works
Movie.year.contributions()                   # works
Movie.year.winner() > 1900                   # works — explicit opt-in to RESOLVED behavior
```

`Resolved[T]` and `MultiValued[T]` are real Pydantic generics; `from_source` / `all_` / `any_` / `contributions` / `winner` return typed expression nodes. The type system carries the disambiguation; no string discriminators.

## Resolution policy — declared on `Slot`

The `RESOLVED` lens reduces a contribution bag to one value via the slot's declared `resolution_policy`:

```python
class ResolutionPolicy(str, Enum):
    ARGMAX_TRUST = "argmax_trust"          # default: highest-trust contribution wins
    MODE = "mode"                          # most-frequent value wins (ties → ARGMAX_TRUST tiebreak)
    WEIGHTED_VOTE = "weighted_vote"        # value with highest sum of trust scores
    MEDIAN_NUMERIC = "median_numeric"      # numeric only; median of values
    LATEST_WATERMARK = "latest_watermark"  # contribution with the latest `asserted_at`
    UNIQUE_OR_FAIL = "unique_or_fail"      # all sources must agree; disagreement raises

class Slot(SpecBase):
    name: str
    range: OntologyClass | TypeDefinition
    multivalued: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    # ...
```

Per-slot, declared in the spec, statically inspectable. The compiler emits the SQL for each policy as part of the trust-resolution CTE. **Not per-call-site** — the same `Movie.year` always reduces the same way under the `RESOLVED` lens.

`UNIQUE_OR_FAIL` exists for slots where disagreement is itself an error (a stable `imdb_id`, a `release_year` that the team has decided sources must agree on). It surfaces as a typed runtime exception, never silent NULL.

`MEDIAN_NUMERIC` only valid for numeric ranges; the spec validator rejects `MEDIAN_NUMERIC` on a string-range slot at parse time.

## SQL shape

Under the `RESOLVED` lens, knot rewrites the consumer query to attach a trust-resolution CTE in front of the `resolved_facts/<Class>` table. One CTE per class touched, regardless of how many properties the predicate references:

```sql
WITH movie_resolved AS (
    SELECT
        canonical_id,
        argmax(year_value,  year_trust)  AS year,
        argmax(title_value, title_trust) AS title,
        ...
    FROM resolved_facts_movie
    GROUP BY canonical_id
)
SELECT * FROM movie_resolved WHERE year > 1900;
```

Different `resolution_policy` enums emit different reduction expressions in the CTE:

| Policy | Reduction expression |
|---|---|
| `ARGMAX_TRUST` | `argmax(value, trust)` (or backend-specific `array_agg` + sort) |
| `MODE` | `mode() WITHIN GROUP (ORDER BY value)` (Postgres / Trino) |
| `WEIGHTED_VOTE` | `argmax(value, sum_of_trust_per_value)` over a sub-group-by |
| `MEDIAN_NUMERIC` | `percentile_cont(0.5) WITHIN GROUP (ORDER BY value)` |
| `LATEST_WATERMARK` | `argmax(value, asserted_at)` |
| `UNIQUE_OR_FAIL` | `CASE WHEN COUNT(DISTINCT value) > 1 THEN error('disagreement') ELSE first(value) END` (or equivalent) |

Under the `DISAGREEMENT_AWARE` lens, knot does not attach the CTE. Bare slot references resolve directly against `per_source_facts/<Class>`. Reductions in the user's expression (`from_source`, `all_`, `winner`) compile to the appropriate SQL primitive.

## Lens-correction interaction

Trust-resolution CTE composes with the consumer-only correction overlay (commitment 13):

```sql
WITH movie_resolved AS (
    -- as above
),
movie_corrected AS (
    SELECT canonical_id,
           COALESCE(uc.year,  mr.year)  AS year,
           COALESCE(uc.title, mr.title) AS title
    FROM movie_resolved mr
    LEFT JOIN postgres_user_corrections.movie uc USING (canonical_id)
)
SELECT * FROM movie_corrected WHERE year > 1900;
```

Pipeline impls (`Materializer`, `ConstraintEvaluator`, `DerivationEvaluator`) get the resolved CTE *without* the correction overlay — replay stays deterministic at the pinned watermark. Only the `Translator` lens stacks the overlay on top.

## What was rejected and why

- **No-default-ever (Type Maximalist's full position).** Verbose at every site that doesn't reason about disagreement; punishes the dominant case.
- **Trust-winner-everywhere (silent default in all lenses).** Silently broken in ER and DQ — `Movie.year == X` for source comparison would compare winner-vs-winner instead of cross-source.
- **Exists-by-default (RDF / SPARQL pattern).** Multiplies result cardinality in materialization and consumer queries; consumers asking "movies after 1990" don't want one row per source agreement.
- **Per-call-site policy override.** Allowing `Movie.year(policy="mode") > 1900` in expressions creates per-query drift — different impls would use different policies for the same slot, and audit walk-back loses determinism. Policy lives on the Slot, full stop.

## What's still implementation detail (not architectural)

- The exact name of the type wrappers (`Resolved` / `MultiValued`) — could be `Curated` / `Bag`, naming bikeshed.
- Whether `winner()` and `from_source()` exist as methods or attribute accessors — code-shape choice.
- Whether knot also emits a static error for `MultiValued.__gt__` via a Pydantic validator at registration time vs. relying on the type checker. Both are reasonable; both are loud.
- The exact SQL emission per backend — that's the QueryExecutor impl's concern below the SQL-generation layer.

## Cross-references

- `core-design.md` § 7 (multi-valued canonical facts) — strengthened by this doc; protocol-kind distinction added.
- `core-design.md` § 4 (universal DI seam) — `disagreement_stance` is a Protocol-level field.
- `core-design.md` § 11 (polymorphic-reference principle) — independent; this doc doesn't change polymorphic refs.
- `staging/auto-generated-sdk.md` — lens semantics section incorporates this.
- `staging/er-and-storage.md` — multi-valued fact storage stays; reduction now lives in this doc.
- `staging/spec-model.md` — `Slot.resolution_policy` field defined here.
- `staging/di-input-contract.md` — protocol declares `disagreement_stance`.
- `staging/sql-generation.md` — the CTE rewrite emits per-policy reductions.
