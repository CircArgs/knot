# Edge-case catalog for synthetic test data

All edge cases knot's machinery must handle. Each case belongs to a category; each tier's `edge_cases.yaml` declares which cases its synthetic data seeds.

Synthetic data generators MUST seed cases from this catalog. "Synthetic" is not an excuse for clean data.

---

## Category 1 — Source-data normalization

| Case | Description | Tiers covering |
|---|---|---|
| 1.1 | Null values in optional slots | A1, A2, B2, C2, all |
| 1.2 | Null values in required slots → fail validation | A1, B2 |
| 1.3 | Empty string vs null (semantically distinct) | A1, A2, B2 |
| 1.4 | Leading/trailing/internal multiple whitespace | A1, B2 |
| 1.5 | Mixed case for enum values (`DIRECTOR` / `Director` / `director` / `dIRECTOR`) | B2, C2 |
| 1.6 | Unicode names with diacritics (Søren, Peña, Wachowski) | A2, B2, C2 |
| 1.7 | Non-Latin scripts (Cyrillic, CJK, Arabic) for names/titles | B2, C2 |
| 1.8 | Date format drift across sources (`1999`, `1999-03-31`, `Mar 31 1999`, `31/3/1999`) | A2, B2 |
| 1.9 | Numeric edge values (year=0, year=-1, runtime=0, runtime=null) | A1, B2 |
| 1.10 | Encoding variants (utf-8 vs latin-1; en-dash `–` vs em-dash `—` vs hyphen `-`) | B2, C2 |
| 1.11 | Trailing whitespace in CSV cells | A1, B2 |
| 1.12 | Source rows with same identifier but conflicting payloads | A2, B2 |
| 1.13 | Source rows with missing identifier → drop or fail per source spec | A1, B2 |
| 1.14 | Discriminator-routed multi-class sources (rows route to Movie OR Series based on column) | C1, C2 |
| 1.15 | Wildcard-drop cases (rows that should be excluded by source mapping) | C1, C2 |

## Category 2 — ER / canonical_id resolution

| Case | Description | Tiers covering |
|---|---|---|
| 2.1 | Known dupes — different source IDs, same entity → must merge | A2, B2, C2 |
| 2.2 | Known non-dupes — similar names, different entities → must NOT merge | B2, C2 |
| 2.3 | Three-way dupes (same Movie in IMDB + TMDB + Wikidata) | A2, B2, C2 |
| 2.4 | Asymmetric coverage (Movie in IMDB+TMDB but missing from Wikidata) | A2, B2 |
| 2.5 | Surfaced conflicts (review-by-default per commitment 16) | B2, C2 |
| 2.6 | Forced merges via `_user_er_decisions` | B2 |
| 2.7 | Forced non-merges via `_user_er_decisions` | B2 |
| 2.8 | Splits — previously-merged canonical_id needs to split | B2 (mutation test) |
| 2.9 | Single-source classes — degenerate ER pass-through | A1, B1, C1 |

## Category 3 — Multi-valued / trust resolution

| Case | Description | Tiers covering |
|---|---|---|
| 3.1 | Full agreement (3 sources same value) — degenerate | A2, B2, C2 |
| 3.2 | 2-vs-1 majority (MODE wins; ARGMAX_TRUST may not) | A2, B2 |
| 3.3 | 3 distinct values (no MODE winner; ARGMAX_TRUST tiebreak) | A2, B2 |
| 3.4 | Single-source (degenerate; no resolution) | A1 |
| 3.5 | Source contributes null vs absent (semantically distinct) | A2, B2 |
| 3.6 | UNIQUE_OR_FAIL policy violation → typed exception | B2 |
| 3.7 | MEDIAN_NUMERIC on integer slot | A2, B2 |
| 3.8 | LATEST_WATERMARK with backdated `asserted_at` | B2 |
| 3.9 | WEIGHTED_VOTE with high-trust outlier vs majority | B2 |
| 3.10 | Each `ResolutionPolicy` enum value exercised at least once | A2, B2 |

## Category 4 — Cross-class relations

| Case | Description | Tiers covering |
|---|---|---|
| 4.1 | Credit references existing Movie + Person | B1, B2, C2 |
| 4.2 | Reference through merged canonical_id (lineage redirect resolves) | B2 |
| 4.3 | Orphan reference (parent doesn't exist) → loud failure | B1, B2 |
| 4.4 | Reference through Identifier polymorphic class | C1, C2 |
| 4.5 | Cyclical references → compile error | C1, C2 |
| 4.6 | Cross-class pinning composes with ER post-merge state | B2 |

## Category 5 — Derivation rules

| Case | Description | Tiers covering |
|---|---|---|
| 5.1 | Empty derivation result (no matching rows) | B2 |
| 5.2 | Single-result derivation (one director per movie) | B2, C2 |
| 5.3 | Multi-result derivation (co-directors; 2+ actors per movie) | B2, C2 |
| 5.4 | Derivation chain (`Movie.director.country.name`) | B2, C2 |
| 5.5 | Derivation referencing deleted slot → publish-gate failure | B2 (mutation) |
| 5.6 | Forward-chained at materialization (Neo4j edges from Credit) | B2, C2 |
| 5.7 | Backward-chained at translator (consumer query → SQL JOIN) | B2 |
| 5.8 | Derivation refs walk into compile network (recently decided) | B2 (mutation) |

## Category 6 — Spec evolution / impact analysis

| Case | Description | Tiers covering |
|---|---|---|
| 6.1 | Add new optional slot → backward compatible | B2 (mutation) |
| 6.2 | Add new required slot → existing rows fail validation | B2 (mutation) |
| 6.3 | Rename a slot → impact analysis surfaces affected workflows | B2 (mutation) |
| 6.4 | Delete a slot used by an impl → registration fails | B2 (mutation) |
| 6.5 | Change `resolution_policy` on a slot → CTE shape changes, hash changes | A2, B2 (mutation) |
| 6.6 | Change `disagreement_stance` on a Protocol → SDK regen, type errors at registration if violated | B2 (mutation) |
| 6.7 | Add new derived slot → derivation walks into network | B2 (mutation) |
| 6.8 | Subclass hierarchy edit (Episode adds parent_series) | C2 (mutation) |

## Category 7 — Config evolution

| Case | Description | Tiers covering |
|---|---|---|
| 7.1 | Edit primitive Config field referenced by DataContext → cache miss, fresh hash | B2 (mutation) |
| 7.2 | Edit Slot/Class-typed Config field → impact analysis on the slot/class | B2 (mutation) |
| 7.3 | Add new Config field (not referenced) → backward-compat | B2 (mutation) |
| 7.4 | Type change on Config field → registration fails | B2 (mutation) |
| 7.5 | Config references a Slot that gets deleted → registration loud-failure | B2 (mutation) |

## Category 8 — Polymorphic / subclass

| Case | Description | Tiers covering |
|---|---|---|
| 8.1 | Identifier with multiple `entity_class` values | C1, C2 |
| 8.2 | Polymorphic slot used as ER signal — must be explicitly declared | C2 |
| 8.3 | Subclass query semantics (Title query returns Movie + Series + Episode + Game) | C1, C2 |
| 8.4 | Override slot in subclass (Episode.parent_series, not on Movie) | C1, C2 |
| 8.5 | `is_a` chain of depth ≥ 3 (Movie → Title → CreativeWork) | C2 |
| 8.6 | Identifier merged across systems | C2 |

## Category 9 — Incremental / cache key

| Case | Description | Tiers covering |
|---|---|---|
| 9.1 | Source watermark advances → only that source's normalize cache-miss; downstream propagates | A1, B2 (mutation) |
| 9.2 | Source watermark unchanged → cache hit, full skip | A1, B2 (mutation) |
| 9.3 | Cross-class pinning composes with cache keys (Movie reruns; Credit's pinned hash matches → Credit cache-hits) | B2 (mutation) |
| 9.4 | Cache miss propagates through full toposort | B2 (mutation) |
| 9.5 | Spec edit invalidates only affected stages' cache keys | B2 (mutation) |
| 9.6 | Impl source bytes change → that impl's stage cache-misses | B2 (mutation) |
| 9.7 | Cache hit but artifact missing → loud failure (no silent recompute) | B2 (mutation) |

## Category 10 — Corrections overlay

| Case | Description | Tiers covering |
|---|---|---|
| 10.1 | Pre-run correction submitted → translator overlay shows it immediately | B2 (mutation) |
| 10.2 | Post-run correction migrated into lake via `_user_corrections` source | B2 (mutation) |
| 10.3 | Correction conflicts with existing source value → trust model resolves | B2 (mutation) |
| 10.4 | Forced ER decision (`_user_er_decisions`) applied at next run | B2 (mutation) |
| 10.5 | Correction on a field that's been removed from spec → graceful failure | B2 (mutation) |
| 10.6 | Pipeline impl never sees overlay (replay determinism) | B2 (mutation) |

## Category 11 — DataContext / ConfigRef edge cases

| Case | Description | Tiers covering |
|---|---|---|
| 11.1 | DataContext references a class that doesn't exist → registration fails | B2 (mutation) |
| 11.2 | DataContext walks across deleted slot → registration fails | B2 (mutation) |
| 11.3 | DataContext primary as empty list → loud failure | A1 (mutation) |
| 11.4 | DataContext primary with duplicate classes → dedup or fail | B2 |
| 11.5 | ConfigRef.field_path resolves to non-existent field → registration loud-failure | B2 (mutation) |
| 11.6 | ConfigRef typed as `int`, but bound impl Config has `str` → typecheck fails | B2 (mutation) |
| 11.7 | Multi-class primary — all 3 examples (single class / list / `spec.classes`) | A1, B2, C2 |
| 11.8 | DerivedSlot as primary → edge view emerges | B2, C2 |
| 11.9 | DataContext joins via `slot.range` traversal (`Movie.director.name`) | B2 |

## Category 12 — Compile / hash / publish-gate

| Case | Description | Tiers covering |
|---|---|---|
| 12.1 | Compile produces empty WorkflowSpec → loud failure | A1 (mutation) |
| 12.2 | Compile fails partway (broken ref) → no partial WorkflowSpec written | B2 (mutation) |
| 12.3 | Two semantically-equivalent specs hash identically (canonicalization) | A1 |
| 12.4 | RUNTIME field mutation (e.g., description) does NOT change hash | A1 |
| 12.5 | Adding optional field with default does NOT change hash (strip-defaults) | A1 |
| 12.6 | Publish-gate atomic across multi-edit (spec + impl + config) | B2 (mutation) |
| 12.7 | Publish-gate rejects spec edit that violates registered impl's DataContext | B2 (mutation) |
| 12.8 | Replay of a compile hash produces byte-identical output | A1, B2, C2 |

## Category 13 — Trigger model / API

| Case | Description | Tiers covering |
|---|---|---|
| 13.1 | `POST /runs/Movie` (per-class trigger) | B2 |
| 13.2 | `POST /runs/full` (whole-pipeline trigger; toposort) | B2, C2 |
| 13.3 | `POST /runs/<stage>` (single-stage trigger) | B2 |
| 13.4 | Trigger when parent class hasn't run → compile error (no parent run to pin) | B2 (mutation) |
| 13.5 | Override `?parents={Movie:hash_X,Person:hash_Y}` for replay/A-B | B2 (mutation) |

## Category 14 — Materialization

| Case | Description | Tiers covering |
|---|---|---|
| 14.1 | Single-class materialization (Movie → Iceberg analytics table) | A1, B2 |
| 14.2 | Multi-class materialization (whole graph → Neo4j) | B2, C2 |
| 14.3 | Multi-target — one impl writes Neo4j + Iceberg + parquet | B2 |
| 14.4 | Derived-edge materialization (Movie.director → `:DIRECTED_BY`) | B2, C2 |
| 14.5 | Materialization of subclass query (Title → all subclass nodes) | C2 |
| 14.6 | Materialization with `Config.exclude_classes` set | B2, C2 |

## Category 15 — Audit walk-back

| Case | Description | Tiers covering |
|---|---|---|
| 15.1 | Neo4j fact → run → compile_hash → spec_revs → contributing source rows | B2, C2 |
| 15.2 | Walk-back across ER merge (canonical_id_lineage redirect) | B2 |
| 15.3 | Walk-back for derived slot (Movie.director → which Credit row → which Person canonical_id) | B2 |
| 15.4 | Walk-back across cross-class pinning (Credit → pinned Movie run → Movie's contributing sources) | B2 |
| 15.5 | Walk-back when source has multiple watermarks across runs | B2 |
| 15.6 | Walk-back for a fact that was overlay-corrected at translator time | B2 |

---

## Category 16 — Ontology expansion (class additions)

Tested as **mutations applied to B2 / C2** rather than new tier fixtures. Each mutation is a `SpecEdit.add_class(...)` / `add_source(...)` / `add_derivation(...)` exercised through the publish gate.

| Case | Description | Tiers covering |
|---|---|---|
| 16.1 | Add new leaf class with no relations to existing classes (e.g., `Book`) | B2 / C2 (mutation) |
| 16.2 | Add new subclass under existing abstract superclass (e.g., `Podcast → Title`) | C2 (mutation) |
| 16.3 | Add new class with structural reference to existing (e.g., `Book.author → Person`) | B2 / C2 (mutation) |
| 16.4 | Add new derivation rule referencing new class (e.g., `Person.books_authored` from `Book.author`) | B2 / C2 (mutation) |
| 16.5 | Add new source feeding the new class | B2 / C2 (mutation) |
| 16.6 | Discriminator-routed source updated to include new class (`imdb_titles` now routes Movie / Series / Game / Podcast) | C2 (mutation) |
| 16.7 | Polymorphic `Identifier.entity_class` widened to point at new class | C2 (mutation) |
| 16.8 | Existing `pipeline_runs` from before expansion still walk-back-correctly (backward compat) | B2 / C2 (mutation) |
| 16.9 | Add parent-child class pair simultaneously (e.g., `Album` + `Track`, mirroring Series / Episode) | C2 (mutation) |
| 16.10 | Add new class then immediately use it as ER signal (`cross_references` config edit on existing impl) | B2 / C2 (mutation) |

## Category 17 — Ontology contraction (class removal / rename / restructure)

| Case | Description | Tiers covering |
|---|---|---|
| 17.1 | Remove a leaf class with no inbound references → publish-gate accepts | B2 / C2 (mutation) |
| 17.2 | Remove a class with inbound structural refs → publish-gate rejects | B2 / C2 (mutation) |
| 17.3 | Rename a class → impact analysis surfaces; cross-class pinning hashes change | B2 / C2 (mutation) |
| 17.4 | Move a class under a different superclass (e.g., `Game` from `Title` to a new `InteractiveWork` superclass) | C2 (mutation) |
| 17.5 | Existing `pipeline_runs` from before contraction still walk-back-correctly | B2 / C2 (mutation) |
| 17.6 | Subclass query on a removed-then-readded class (deprecation cycle) | C2 (mutation) |

---

## Coverage matrix summary

| Tier | Categories with seeded cases |
|---|---|
| **A1** | 1 (basic), 3 (degenerate), 9 (basic incremental), 11 (basic DataContext), 12, 14 (single-class), 15 (basic) |
| A2 | 1, 2, 3 (full multi-valued), 6.5, 9, 10 (subset), 12, 14, 15 |
| A3 | 1, 3, 9 (many watermarks) |
| B1 | 1, 2 (single-source), 4 (relations), 5 (derivations), 8, 9, 11, 12, 14, 15 |
| **B2** | ALL static + mutations 16/17 (subset) |
| B3 | All of B2 + scale-stress on watermarks |
| C1 | 1, 4 (relations), 5 (rich derivation web), 8 (subclass + polymorphic), 11 (deep), 14 (subclass), 15 |
| **C2** | ALL static + ALL mutations including 16/17 (full) |
| C3 | All of C2 + scale-stress |

The implemented set (A1 + B2 + C2) covers every static category. Mutation categories 16 and 17 are exercised on B2 (basic expansion / contraction) and C2 (full polymorphic + subclass-aware expansion / contraction). Template tiers exist for future expansion if specific categories need isolation.
