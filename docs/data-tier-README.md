# Test fixtures — the 9-tier matrix

Synthetic test data for verifying knot's architectural commitments. Tier matrix is **ontology richness × source count**:

|            | **1 source per class** | **3 sources per class** | **many (~10) sources per class** |
|---|---|---|---|
| **1 class** (Movie) | **A1** ✅ implemented (smoke) | A2 — template | A3 — template |
| **3 classes** (Movie/Person/Credit) | B1 — template | **B2** ✅ implemented (default) | B3 — template |
| **10+ classes** (Title hierarchy + Person + Credit + Identifier + Studio + Award + Country) | C1 — template | **C2** ✅ implemented (stress) | C3 — template |

✅ = built out with full fixture data and tests for week 1
template = directory scaffold + README only; fixture data not generated until needed

## What each tier is for

| Tier | Purpose | Why it's irreplaceable |
|---|---|---|
| **A1** | Smoke. Degenerate ER (single-source pass-through). Minimum compile path. | Only fixture that exercises no-ER / no-relation code paths. |
| A2 | Multi-source resolution focus, no relation noise. | Easier debugging of trust-CTE / multi-valued tests in isolation. |
| A3 | Scaling source count without ontology complexity. | Catches source-coverage-drop, many-watermark coordination. |
| B1 | Relations without multi-source noise. | Easier debugging of cross-class-pinning / derivation tests. |
| **B2** | The default integration tier — every commitment exercised at once. | Where most regressions surface first. |
| B3 | Relations + scaling source count. | Stress on source-side coordination within relation context. |
| C1 | Rich ontology, single-source. | Subclass hierarchies, polymorphic refs, deep DataContext expressions, derivation chains in isolation. |
| **C2** | Rich ontology + multi-source. Stress on structural primitives. | The full kitchen sink. |
| C3 | Max stress. | Performance regression detection (manual / weekly). |

## Synthetic ≠ shortcut

Each tier's source data **deliberately seeds edge cases** from `EDGE-CASES.md`. Synthetic data is generated, but the generator produces problematic real-world-shaped inputs by design — nulls, whitespace variants, unicode, mixed case, date-format drift, designed-in disagreement, designed-in dupes with ground truth, designed-in conflicts.

A test fixture is "complete" only when it triggers every edge case relevant to its tier (per the per-tier coverage matrix in `EDGE-CASES.md`).

## Directory structure per tier

```
tests/fixtures/<TIER>/
├── README.md            # what this tier covers, edge-case coverage matrix
├── spec.py              # Pydantic spec (classes, slots, derivations, sources)
├── sources/             # CSVs, one per (source × class)
│   ├── imdb_movies.csv
│   ├── tmdb_movies.csv
│   └── ...
├── impls/               # bound DI impls used by this tier's tests
│   ├── er_movie.py
│   ├── er_person.py
│   ├── er_credit.py
│   └── neo4j_publisher.py
├── configs/             # impl configs as Python literals or yaml
│   └── ...
├── expected_facts.yaml  # ground-truth assertions tests use
└── edge_cases.yaml      # which cases are seeded where (canonical_id → category → description)
```

For template tiers (A2, A3, B1, B3, C1, C3), only `README.md` exists — the rest is empty until the tier is built.

## Generator

`tests/fixtures/generator.py` is a single parameterized synthesizer. Tier configs (`tier_configs.py`) declare what each tier looks like. Running the generator with a tier name produces all the files under that tier's directory.

Implemented tiers (A1, B2, C2) are checked in; their sources/spec/impls are committed. Re-running the generator should reproduce the checked-in files byte-for-byte (deterministic seeds).
