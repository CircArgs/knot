# Protocol return shapes — typed knot-controlled result objects

**Status:** decided. Cross-references commitments 4, 5, 6, 14.

---

## Core principle

Every protocol method returns a typed knot-controlled object.  Impls cannot
drift the contract by renaming columns — the result type owns the mapping spec
that declares which column carries which semantic meaning.

The fixture impl writer writes:

```python
def score(self, ctx, imdb, tmdb, wikidata) -> ERResult:
    ...
    return ERResult(
        table=Path("/lake/runs/abc123/movie_scores.parquet"),
        column_map=ScoreColumnMap(
            a_canonical="entity_a_id",
            b_canonical="entity_b_id",
            score="jaccard_sim",
        ),
    )
```

The column names `entity_a_id`, `entity_b_id`, `jaccard_sim` are the impl's
choice.  The `ScoreColumnMap` tells knot which column means what.  knot reads
`column_map.a_canonical` to find the "entity A" column — never guesses.

---

## Why the mapping spec lives on the result (not the protocol)

The protocol defines the *shape contract* — what fields the result must carry.
The impl controls the *output naming* — it wrote the table and named the
columns.  Putting the map on the result keeps both concerns in the right place:

- Protocol: enforces the contract (extra=forbid; required fields present).
- Result: declares what the impl actually produced.

A map on the protocol would require impls to pre-declare column names before
writing, or require knot to enforce naming conventions.  Neither fits.

---

## Uniform shape across protocols

Every result type follows: **output table location** + **column map** +
**protocol-specific status fields**.

| Result type | Table / location | Column map | Status fields |
|---|---|---|---|
| `ERResult` | `table: Path` | `ScoreColumnMap` | — |
| `MaterializeResult` | (target name, not a Path) | — | `status`, `watermark` |
| `DqResult` | `offenders_table: Path \| None` | `DqColumnMap` | `passed`, `summary` |
| `TranslateResult` | `result_table: Path` | `dict[str, str]` | — |
| `ConstraintResult` | `offenders_table: Path \| None` | — | `passed`, `summary` |
| `DerivationResult` | `output_table: Path` | `dict[str, str]` | — |

`MaterializeResult` omits a Path because materialization targets (Neo4j,
Iceberg, vector stores) don't uniformly express as filesystem paths — `target`
is a logical name.  `watermark` is present when the target supports advancing
watermarks for incremental execution.

---

## Worked example — ERResult with ScoreColumnMap

```python
from pathlib import Path
from knot.protocols import ERResult, ScoreColumnMap

# Impl returns this from score():
result = ERResult(
    table=Path("/lake/runs/abc123/movie_scores.parquet"),
    column_map=ScoreColumnMap(
        a_canonical="entity_a_id",   # impl chose this name
        b_canonical="entity_b_id",   # impl chose this name
        score="jaccard_sim",          # impl chose this name
    ),
)

# knot reads the output without guessing column names:
df = read_parquet(result.table)
pairs = df[[
    result.column_map.a_canonical,
    result.column_map.b_canonical,
    result.column_map.score,
]]
```

The impl is free to include additional columns (debug signals, sub-scores).
knot uses only the three declared in `ScoreColumnMap`; extra columns are
ignored.

---

## Cross-references

- `core-design.md` § 4 (universal DI seam) — result shapes are the output
  side of the seam contract.
- `core-design.md` § 5 (trusted authors, knot-hosted impls) — extra=forbid
  on result types catches structural mistakes at return time, not silently.
- `core-design.md` § 6 (impl IS the strategy) — no separate result-shape
  registry; result types live in `knot.protocols`.
- `core-design.md` § 14 (two-layer DQ) — `DqResult` + `DqColumnMap` is the
  uniform failure shape for both built-in and custom DQ runners.
- `staging/di-input-contract.md` — input side of the same seam.
- `staging/multi-valued-semantics.md` — `disagreement_stance` on each protocol
  drives the input SDK type; result shapes are the output complement.
