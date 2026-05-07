# Tier C3 — 10+ classes, many sources per leaf (max stress)

**Status:** template only — fixture data not generated.

## Scope (planned)

- **Classes:** Same rich ontology as C2 (Title hierarchy + Person + Credit + polymorphic Identifier + Studio + Award + Country)
- **Sources:** ~10 sources per leaf class (where realistic)
- Maximum coordination stress

## Volumes (planned)

- Same canonical entity counts as C2
- ~10× source-row volume (~7000 source rows)
- Designed-in source-coverage variance across the rich ontology

## Edge cases additional to C2 (per `../EDGE-CASES.md`)

| Category | Cases |
|---|---|
| 1 — Source normalization | 1.7 at scale; encoding mixes |
| 3 — Multi-valued | 3.9 with many high-trust outliers |
| 9 — Incremental | 9.1, 9.4 with many parallel watermark advances across rich ontology |
| 14 — Materialization | 14.2 of whole rich graph at scale |

## Why this tier might be needed

Performance regression detection (run weekly, not per-PR). Catches scaling failures: cache-key recompute cost, compile-hash canonicalization cost on large WorkflowSpecs, JOIN explosion on multi-class derivation chains, watermark-aggregation drift.

## Files (when implemented)

- Full C2-shape directory layout with ~10× source CSV volume
- May share `spec.py` with C2 (only source data differs)
