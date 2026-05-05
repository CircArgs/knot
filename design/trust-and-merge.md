# Trust and merge

**Status:** working draft — not locked. Captured during concretization pass.

How knot reconciles disagreement between sources, and how human corrections fit the same machinery.

## The model

Two layers of facts:

- **Per-source-facts.** Immutable, complete record of every claim every source ever made. One row per `(source, entity, property, value, asserted_at)`. Nothing is ever overwritten or thrown away.
- **Resolved-facts.** The multi-valued canonical view — per-source-facts joined to canonical_ids (from ER). Every source's contribution for a canonical entity is retained; nothing is discarded. Keyed by `canonical_id`.

**Knot does not write a "winner" column.** Default-value resolution (the trust-weighted highest-trust pick) happens at query time, inside knot's SDK, when an impl asks for `Movie.year` (rather than `Movie.year.all()` or `Movie.year.from_source(...)`). See `staging/auto-generated-sdk.md`.

Lineage is in the data: every value carries its source attribution. "Show alternatives" = `.all()`. "Why this value?" = inspect the contributions and the current trust scores. No separate audit-chain table, no merge-decision record per cell.

## Trust is per `(source, property)`, not per source

A single source-level trust number is the toy model. The real model is at least property-level:

- IMDb is high-trust for `runtime`.
- IMDb is *low*-trust for `release_date` (it collapses festival / theatrical / regional into one date).
- TMDb is high-trust for `franchise`.
- TMDb is medium-trust for `genres` (its taxonomy).
- A correction-source is very-high-trust for whatever property it touches.

Per-property weighting is what lets the canonical entity surface defaults cell-by-cell — `runtime` defaulting to one source, `release_date` to another, `franchise` to a third — rather than picking a winning *source* and inheriting all its (sometimes-wrong) values.

## Default-value resolution at query time

When an impl asks for `Movie.year`, knot returns the highest-trust contribution for that canonical entity's year. The resolution happens at query time, per-property, against the multi-valued canonical view:

| Property | Default surfaced | Why |
|---|---|---|
| `runtime` | criterion-source's value | highest `(source, runtime)` trust at query time |
| `release_date` | TMDb's value | TMDb is structured ISO; IMDb's collapsed date is lower-trust here |
| `title` | IMDb's value | highest title-trust |
| `franchise` | TMDb's value | only contributor |

This is **not** materialized as a "winner" column on resolved-facts. Knot computes it on demand from the multi-valued data + current trust state. Trust changes don't require re-running merge — the next query surfaces a different default automatically.

Audit walk-back is `Movie.year.all()`: it returns every contribution with attribution. No separate audit-chain machinery needed.

## Corrections are just another source

A human override is **not** a special case in the pipeline. It's a row contributed by a `_user_corrections` source at very high `(source, property)` trust (e.g., 0.95). Same per-source-facts table, same multi-valued canonical view, same query-time trust resolution. The correction surfaces as the default because its trust dominates — not because the pipeline branches on "is this a correction."

Consequences:

- Corrections are versioned the same way every other source's data is.
- Walking back from a resolved value lands on the correction the same way it would land on any source row.
- Disabling corrections or re-weighting them is a trust-config change, not a pipeline change.
- A team can model multi-author-correction trust (junior steward 0.7, senior steward 0.9) by treating `_user_corrections` as N sources rather than one.

## Tie-breaks

Equal trust on a property happens. Pick a default and document it:

- **Recency wins** — most-recently-asserted value.
- **Source-priority list** — ordered list of sources is the secondary key.
- **Surface as ambiguous** — knot's SDK default returns "ambiguous" (or null with a flag) for that property; impls can call `.all()` to see the tied contributions, or a correction can be added as a high-trust source to break the tie.

Defaulting to recency is probably right; tie-breaks are rare in practice.

## Initial trust assignment

**Pattern: source-level baseline + per-property overrides.**

The source node carries:

- A single source-level `default_trust` (e.g. IMDb = 0.85).
- An optional `trust_overrides` map keyed by property — only the known-fragile ones need to appear.

```yaml
default_trust: 0.85
trust_overrides:
  release_date: 0.5         # IMDb collapses festival/theatrical/regional into one date
  genres: 0.7               # IMDb's vocab is coarse
```

Properties not in `trust_overrides` inherit `default_trust`. Authoring is one number plus a small map.

**Trust lives on the source node, versioned with the source spec.** Same audit + revision treatment as every other spec node. Trust is *spec*, not derived data — humans set it, the pipeline reads it.

**Justifications belong with the score.** A bare number isn't auditable. The source node should carry a `trust_rationale` (or comment field) explaining why a value was chosen. "0.5 on release_date because IMDb structurally collapses multiple real dates into one." The number drives the merge; the rationale drives review.

## Trust evolution: Beta-Bernoulli bandit

**Framing.** Each `(source, property)` is a bandit arm. Trust is a posterior distribution — `Beta(α, β)` — with a prior set by the steward and updated as data rolls in. Mean = current best estimate of reliability; variance = uncertainty about it.

This dissolves the spec-vs-derived tension cleanly:

- **Prior is spec.** Steward-set, versioned, auditable. Encodes both "I think 0.85" and "how confident I am about 0.85" via the `(α, β)` parameters.
- **Posterior is derived data.** Updated from observations. Has its own audit chain (which run updated which arm, from which observations).
- **They compose.** The current trust = prior + observations up to now. Both layers have their own provenance.

**Replay is snapshot-based, not derivation-chain.** At each merge run, the trust state `(α, β)` per arm is snapshotted alongside the compiled workflow. Replaying yesterday's merge fetches yesterday's snapshot. No need to walk back through the entire update history.

**What naturally falls out:**

- **Cold-start.** Wide-variance prior expresses "I think 0.85 but I'm uncertain." Updates move quickly. Strong-prior expresses "I'm confident in this number," updates move slowly.
- **First-class uncertainty.** A new source's wide variance can flow into merge — pessimistic-mean weighting, Thompson sampling, etc. — instead of being silently treated as equally reliable as a long-validated source.

**Open mechanics (deferrable, not blocking the framing):**

1. **Observation signal.** What counts as a "win" or "loss" for an arm? Correction rate, consensus-agreement, hold-out validation against curated truth — likely a hybrid. Has real consequences for what the system actually rewards.
2. **Non-stationarity.** Reliability drifts over time (vendor issues, schema regressions). Vanilla Beta assumes stationary rewards. Pick a default: windowed updates, exponential decay on prior counts, or explicit drift detection.
3. **Query-time use.** When knot's SDK needs to surface a default value, does it use the posterior mean? Sample from it (Thompson)? Pessimistic mean (mean − k·stddev)? Real choice.
4. **Storage shape and bookkeeping scale.** 20 sources × 50 properties = ~1000 arms updating per cadence. Needs a `trust_state` keyed by `(source, property)` with `(α, β, last_updated, observation_count)`. Tractable.

These four are real questions but the framing is settled: trust is a Beta-Bernoulli bandit with steward-set priors and posterior updates from observed signal.
