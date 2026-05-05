# Auto-generated ontology SDK

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How knot exposes the ontology to bound DI implementations as a typed Python SDK.

## The pattern

Knot's codegen reads the Pydantic spec and produces Python classes corresponding to ontology classes, with typed attributes corresponding to slots and relationships. The SDK is what bound impls import; they reference ontology classes and attributes directly, not as strings. Bound impls declare DataContexts as class-level attributes built from these SDK classes (per `di-input-contract.md`):

```python
from typing import ClassVar
from knot.ontology import Movie, Credit, Identifier   # auto-generated from the spec
from knot.di import ERProtocol, DataContext, Edge

class MovieResolver(ERProtocol):
    candidates: ClassVar[DataContext[Movie]] = DataContext(
        primary=Movie,
        include=[
            Movie.credits.where(Credit.role.in_(["director"])),
            Movie.identifiers,
        ],
        filter=(Movie.year > 1900),
    )

    def resolve(self, ctx, candidates) -> list[Edge]: ...
```

The SDK class (`Movie`) is a generated descriptor-only class — *not* a Pydantic subclass. Class-level attributes overload comparison and composition operators (`>`, `<`, `==`, `&`, `|`, `~`, `.in_`, etc.) and provide traversal expressions like `Movie.credits`. The expression objects they produce are Pydantic-typed nodes (per `derivation-and-constraints.md`); the SDK is the fluent builder.

## Why typed SDK over string-based names

- **Type safety.** mypy catches `Movie.yr` typos, bad operators, wrong types at edit time.
- **IDE autocomplete.** `Movie.` lists actual slots; `Movie.credits.` lists Credit's attributes.
- **Compositional expressions.** Filters compose like SQLAlchemy / Django expressions — readable, lazy, type-checked.
- **Statically-discoverable references.** The SDK's class/attribute references can be analyzed by tools to compute the spec dependency graph — automatic impact analysis on ontology changes.

## Expression surface

Operators supported on the SDK expression tree:

- **Comparisons.** `Movie.year > 1900`, `Movie.title == "Godfather"`, `Movie.year.between(1990, 2000)`.
- **Boolean composition.** `&` (and), `|` (or), `~` (not).
- **Set membership.** `Credit.role.in_(["director", "writer"])`.
- **Relationship traversal.** `Movie.credits`, `Credit.person`, `Person.identifiers`.
- **Filtered traversal.** `Movie.credits.where(Credit.role == "director")`.
- **Aggregation.** `Movie.credits.where(Credit.role == "director").count()`, `Movie.credits.collect(Credit.person.name)`, `Movie.credits.where(...).agg(...)`.
- **Window functions.** Rank, row_number, dense_rank, etc., over relationship traversals (when needed).

Under the hood, expressions translate to AST nodes (sqlglot or equivalent — see `ontology-modeling.md`). Each operator maps to standard SQL primitives — comparisons, AND/OR/NOT, IN, JOIN, GROUP BY, window functions — emitted to Spark and Trino as the lake-side SQL targets.

## Multi-valued canonical facts

At the canonical layer, every property is implicitly **multi-valued** — one contribution per source that asserts it. `Movie.year` for canonical `mov_xyz` isn't a single value; it's a set: `[{source: imdb, value: 1972, asserted_at: ...}, {source: tmdb, value: 1972, asserted_at: ...}]`. Lineage lives IN the data — knot does not maintain a separate audit-chain table.

**Knot owns the resolution logic.** Bound impls don't write custom traceback or trust-application code. Which slot type and which access modes are available depends on the protocol's `disagreement_stance` (see `staging/multi-valued-semantics.md` for the full decision and per-protocol assignment).

Under `RESOLVED` protocols (Materializer, Translator, ConstraintEvaluator, DerivationEvaluator), slot references are `Resolved[T]` — comparison operators work directly and knot attaches a trust-resolution CTE. The reduction is per the slot's declared `resolution_policy` (default: `ARGMAX_TRUST`). Escape hatches remain available:

| Access | Returns | Notes |
|---|---|---|
| `Movie.year` | Resolved via `resolution_policy` | Trust-CTE attached |
| `Movie.year.from_source("imdb")` | That source's contribution | No CTE |
| `Movie.year.all_()` | All contributions: `[{source, value, asserted_at}, ...]` | No CTE |
| `Movie.year.contributions()` | Raw `list[Contribution[int]]` | No CTE |

Under `DISAGREEMENT_AWARE` protocols (ERProtocol, DqRunner), slot references are `MultiValued[T]` — bare comparison (`Movie.year > 1900`) is a type error. Callers spell their reduction:

| Access | Notes |
|---|---|
| `Movie.year.from_source("imdb") > 1900` | Specific source |
| `Movie.year.all_() > 1900` | Forall — every source agrees |
| `Movie.year.winner() > 1900` | Explicit opt-in to resolved behavior |
| `Movie.year.contributions()` | Raw bag |

What this dissolves:

- **Audit walk-back is just `Movie.year.all_()`.** No separate context method, no `AuditChain` model. The full lineage is queryable as data.
- **Temporal predicates are filters on `.all_()`** — `Movie.year.all_().filter(asserted_at <= X)`.
- **Trust-score updates don't require re-running merge.** Trust is applied at query time; the same canonical data with new trust scores surfaces different defaults.

The merge stage no longer "writes winners" — it ensures canonical_id linking is current and trust state is consistent. The actual default-value selection happens at query / display time, inside knot.

## Multi-class primary and slot-range joins

`DataContext.primary` accepts `OntologyClass | list[OntologyClass] | DerivedSlot | list[DerivedSlot]`. A list fans out at fulfill to N typed views, one per class or derived slot:

```python
core: DataContext = DataContext(primary=[Movie, Person, Credit])
# impl receives {Movie: <view>, Person: <view>, Credit: <view>}
```

Joins via `slot.range` traversal are first-class in `project=` and `where=`. Any writable slot path becomes a JOIN in the generated SQL:

```python
denormalized: DataContext = DataContext(
    primary=Movie,
    project=[Movie.canonical_id, Movie.title, Movie.director.name],
    # Movie.director.name → JOIN through Credit, Person
)
```

No `Graph` symbol, no `ClassSet` algebraic wrapper. `spec.classes` is `list[OntologyClass]`; `DataContext(primary=spec.classes)` produces N views automatically as the spec grows. See `staging/multi-class-datacontexts.md`.

## Reuse across contexts — lens semantics

The same SDK classes are used by every bound DI implementation, across every pipeline stage. What differs is (a) the **backing table** knot uses to fulfill the impl's DataContexts and (b) the **slot type** the SDK exposes, driven by the protocol's `disagreement_stance`. See `staging/multi-valued-semantics.md` for the canonical statement; the table below is the per-protocol summary.

| Protocol | `disagreement_stance` | Backing lens | Slot type | Trust-CTE attached? |
|---|---|---|---|---|
| `ERProtocol` | `DISAGREEMENT_AWARE` | `per_source_facts/Movie` | `MultiValued[T]` | No |
| `DqRunner` | `DISAGREEMENT_AWARE` | Per declared DataContext | `MultiValued[T]` | No |
| `Materializer` | `RESOLVED` | `resolved_facts/Movie` + derivations | `Resolved[T]` | Yes (no overlay) |
| `Translator` | `RESOLVED` | `resolved_facts/Movie` + correction overlay | `Resolved[T]` | Yes + overlay |
| `ConstraintEvaluator` | `RESOLVED` | `resolved_facts/Movie` | `Resolved[T]` | Yes (no overlay) |
| `DerivationEvaluator` | `RESOLVED` | `resolved_facts/Movie` | `Resolved[T]` | Yes (no overlay) |

Under `RESOLVED` protocols, bare `Movie.year > 1900` compiles and knot attaches the trust-resolution CTE (per the slot's `resolution_policy`). Under `DISAGREEMENT_AWARE` protocols, bare `Movie.year > 1900` is a type error — the impl must spell the reduction. The protocol the impl implements determines both the lens and the slot types; the impl writer does not choose either. See `di-input-contract.md` § "Cross-class traversals and the pinning lens" and `query-executor.md`.

### What the SDK carries vs. what the context provides

The SDK classes carry **ontology slots only** — `Movie.year`, `Movie.title`, `Movie.credits`. They do **not** carry knot's attribution or bookkeeping columns:

- **Per-source-facts attribution** (`source`, `src_key`, `knot_row_id`, `asserted_at`) — exposed as columns on the materialized DataContext view at the ER lens, not as ontology slots on the SDK class.
- **Resolved-facts bookkeeping** (`canonical_id`, per-cell provenance) — exposed as columns on the materialized DataContext view at the translator / materialization lens, not as ontology slots.

The SDK is the ontology surface, identical across contexts. Each context layers its own attribution / meta columns on top — accessed via context-specific methods or filters, not inherited into the SDK.

Concretely:

- `Movie.year > 1900` works in any context — ontology slot.
- `Movie.source == "imdb"` doesn't exist; `source` isn't an ontology concept. ER context exposes a separate filter for it.
- `Movie.canonical_id` doesn't exist; that's resolved-facts bookkeeping.

### Why this works: resolved is the multi-valued canonical view

`resolved_facts/Movie` has the same slots as `per_source_facts/Movie` (title, year, runtime). What differs:

- **Per-source-facts:** keyed by `(source, src_key)` — what each source said.
- **Resolved-facts:** keyed by `canonical_id` — every contribution from every source for that canonical entity, joined via ER's bindings. Implicitly multi-valued per slot.

The "default" single value (highest-trust contribution) is computed at query / display time when the impl does `Movie.year` — not pre-baked into resolved-facts as a winner column. This is why the SDK works across contexts: the underlying ontology shape is identical; only keying and attribution differ — attribution lives on the context, default-value resolution lives in knot's query layer.

## Result shape (denormalized, prefixed flat columns)

When `fetch` returns a denormalized join of multiple classes, columns are flat scalars with `<class>_<slot>` prefixes:

```
movie_src_key | movie_title  | movie_year | credit_role | credit_person_src_key | identifier_system | identifier_value
123           | "Godfather"  | 1972       | director    | nm_pers_xyz           | imdb              | tt0068646
123           | "Godfather"  | 1972       | lead_actor  | nm_pers_abc           | imdb              | tt0068646
...
```

Standard SQL JOIN convention. Cross-framework friendly (pyarrow / polars / pandas / spark all handle flat scalars natively). 1:N relationships produce cartesian-product rows; the impl aggregates with group-by if it wants list/struct shape.

### Recommended pattern: aggregation per-include for multi-1:N fetches

Multiple unrelated 1:N includes in one fetch produce **cartesian rows** — for a movie with N credits and M identifiers, you get N × M rows. That's correct SQL JOIN behavior, but rarely what an impl actually wants for feature engineering.

The recommended pattern for multi-1:N fetches is **aggregation per-include**:

```python
class MovieResolver(ERProtocol):
    candidates: ClassVar[DataContext[Movie]] = DataContext(
        primary=Movie,
        include=[
            Movie.credits.where(Credit.role == "director").collect(Credit.person.name).as_("directors"),
            Movie.identifiers.collect(Identifier.value).as_("ids"),
        ],
    )
# One row per Movie. directors: list[str], ids: list[str]. No cartesian.
```

SQL gen emits subqueries / `GROUP BY`, not cross joins. Bounded, one row per primary, list/scalar columns per aggregation.

When raw (cartesian) makes sense:

- Single include (only one 1:N).
- 1:1 includes only (no fanout).
- The impl genuinely wants to walk every (primary × related) tuple and is prepared for the row count.

When aggregation makes sense (almost always otherwise):

- Multi-1:N fetches.
- ML feature engineering (one row per primary entity is usually what you want).
- Anywhere you'd group-by-and-aggregate downstream anyway — push it into the fetch.

### Source attribution in aggregations

Default aggregation flattens across sources. `Movie.credits.collect(Credit.person.name)` returns a single list — useful for "give me all directors" but **loses which source said what**. For ER (where source agreement is the signal) or any consumer that needs source-correlation, use `group_by_source()`:

```python
# Default — flat list, no source attribution:
Movie.credits.where(Credit.role == "director").collect(Credit.person.name)
# → ["Coppola", "Coppola Jr.", "Lucas"]

# Source-keyed — map per source:
Movie.credits.where(Credit.role == "director").group_by_source().collect(Credit.person.name)
# → {imdb: ["Coppola"], tmdb: ["Coppola"], wikidata: ["Lucas"]}

# Pivoted — prefixed flat columns (for consumers wanting tabular output):
Movie.credits.where(Credit.role == "director").group_by_source().collect(Credit.person.name).pivot()
# → directors_imdb, directors_tmdb, directors_wikidata columns
```

**Map as default representation, `.pivot()` as an explicit operator.** Map shape handles sparsity natively (missing sources aren't keys, no NULL pollution) and is schema-flexible (new sources don't change the column count). Pivoted prefixed columns are an explicit choice for consumers that want flat tabular output (CSV export, basic BI, traditional ETL).

For ER specifically, raw per-source rows (no aggregation) are usually the right shape — every row carries source attribution naturally, and pairwise comparisons preserve correlation. Aggregation is mostly useful for downstream consumers that want summarized views per primary entity.

## Mechanically

- **Knot's internal spec is Pydantic models.** `OntologyClass`, `Slot`, `Constraint`, `ReferencePattern`, etc. are Pydantic classes; in-memory uses real object references between metaschema entities (no name-string lookups). At the persistence boundary, references flatten to names for JSONB storage and rehydrate on read. Pydantic validates on parse — typos / wrong types / missing required fields surface as `ValidationError` with field paths. See `spec-model.md`.
- **Authoring is programmatic.** Users construct specs via UI / API. No YAML authoring path. The spec is regenerated dynamically as it changes (ontology modeling is a runtime activity).
- **The SDK is generated as a SEPARATE class per ontology class** — not by extending the spec model. Knot's SDK codegen reads the Pydantic spec and emits a distinct SDK class (`Movie`) alongside the internal spec class (`MovieSpec`). Two artifacts, same source spec.
- **Bound impls import only the SDK classes** — `from knot.ontology import Movie, Credit, Identifier`. They never see the internal spec models.
- Knot resolves joins from the structural relationships in the spec; the impl doesn't specify FK columns.

### Two-class generation

Each ontology class generates **two separate Python classes**, decoupled by responsibility:

- **`MovieSpec` — Pydantic data class. Internal to knot.** Used for spec storage (JSONB serialization), validation on parse, and as input to the codegens. Standard Pydantic behavior — instances can be constructed, validated, dumped to JSON. Bound impls never import this.
- **`Movie` — SDK class. What bound impls import.** Pure query expression builder. Class-level attributes (`Movie.year`, `Movie.credits`) are descriptors that overload comparison and traversal operators, returning expression objects. **Not a Pydantic model.** You don't construct instances of `Movie`; you only use it for queries: `Movie.year > 1900`.

The two never collide because:

- `MovieSpec` is internal — bound impls never see it.
- `Movie` is the impl-facing surface — only used for query expressions, never instantiated.
- Both are generated from the same Pydantic spec, structurally consistent by construction.

This is exactly SQLAlchemy 2.x's pattern: the mapped class is for queries, separate from any data containers (Pydantic models, dataclasses, etc.).

The decoupling avoids Pydantic v2 metaclass conflicts entirely — there's no need to install class-level expression descriptors on a Pydantic class, because the SDK class isn't a Pydantic class. Real implementation work, no novel design.

### Codegen mechanics

Each slot on an SDK class gets a **per-slot generated descriptor class** subclassing `SlotDescriptor[T]`. The descriptor is what makes `Movie.year > 1900` return a `Compare` expression node, not a bool.

```python
# generated for Movie.year: int
class _Movie_year_Descriptor(SlotDescriptor[int]):
    _slot: Slot = <Slot ref injected at codegen>

    def __gt__(self, other: int) -> Compare: ...
    def __lt__(self, other: int) -> Compare: ...
    def __eq__(self, other: int) -> Compare: ...   # type: ignore[override]
    def between(self, lo: int, hi: int) -> BoolOp: ...
    def in_(self, values: list[int]) -> Compare: ...

class Movie:
    _spec: ClassVar[OntologyClass]              # set at codegen time; back-ref to spec
    year: _Movie_year_Descriptor = _Movie_year_Descriptor()
    ...
```

Relationship slots use `RelationshipDescriptor[T]` (a `SlotDescriptor` subclass) which additionally exposes `.where(...)`, `.collect(...)`, `.group_by_source()`, etc.:

```python
# generated for Movie.credits: list[Credit]
class _Movie_credits_Descriptor(RelationshipDescriptor[Credit]):
    _slot: Slot = <Slot ref>

    def where(self, predicate: BoolExpr) -> FilteredRelation: ...
    def collect(self, *fields, order_by=None, distinct=False) -> RelationAggregate: ...
    def group_by_source(self) -> RelationAggregate: ...
```

The expression nodes produced (`Compare`, `BoolOp`, `RelationRef`, `FilteredRelation`, `RelationAggregate`, etc.) are the same Pydantic types specified in `derivation-and-constraints.md`. The SDK is the in-Python fluent builder; the API receives the same tree as JSON.

**Aggregation chaining is type-gated.** Method composition is constrained by descriptor return type — `.pivot()` is only available on grouped relations, `.collect()` only after a relation traversal, etc. Stub types (see "Type checking" below) enforce this at edit time; runtime descriptors raise `TypeError` on misuse.

### Type checking — `.pyi` stubs

Generated `.pyi` stubs ship alongside `.py` in the same package directory. mypy reads stubs; the `.py` file contains the descriptor machinery.

```python
# Movie.pyi (generated)
from knot.sdk import Mapped

class Movie:
    year: Mapped[int]           # resolves to _Movie_year_Descriptor at runtime
    title: Mapped[str]
    credits: Mapped[list[Credit]]
    ...
```

`Mapped[T]` is a protocol carrying the descriptor interface; mypy uses it to verify that `Movie.year > 1900` produces an expression, not a bool, and that disallowed compositions are caught at edit time. Mirrors SQLAlchemy 2.x's `Mapped[T]`.

### Statically-discoverable references

Three pure-data hooks at module and class scope — AST-readable without executing generated code:

```python
# module level (Movie.py)
__spec_classes__: dict[str, type] = {"Movie": Movie, ...}

# class level
class Movie:
    __spec_ref__: ClassVar[str] = "Movie"   # matches OntologyClass.name
    ...

# descriptor level
class _Movie_year_Descriptor(SlotDescriptor[int]):
    __slot_ref__: ClassVar[tuple[str, str]] = ("Movie", "year")
    ...
```

Used by impact-analysis tooling to compute dependencies from impl code without executing it. Combined with the `_spec` / `_slot` ClassVars on the descriptor objects, knot can walk an impl's expression tree (in DataContexts, in derivation rules, in constraints) to enumerate every spec entity touched.

### Packaging

Generated `.pyi` stubs and `.py` files ship in the same package directory:

```
knot/ontology/
    __init__.py
    movie.py        # descriptor machinery
    movie.pyi       # type stubs (mypy reads these)
    credit.py
    credit.pyi
    ...
```

No separate `stubs/` package. mypy discovers stubs automatically via PEP 561 (inline stubs).

### Pydantic version coupling

Knot's internal spec depends on **Pydantic v2** semantics — JSONB serialization format, validation behavior, and field/model APIs are all v2-shaped. Major version migrations (v2 → v3 when it lands) have two implications worth flagging:

- **Stored spec migration.** Existing JSONB-stored specs may need a translation pass if Pydantic's serialization format changes between major versions.
- **Content-addressed compile hashes.** Every spec's hash depends on its canonical serialized form. A Pydantic-side change to that form would silently rehash every existing compiled spec — invalidating audit-walk-back from prior runs unless explicitly bridged.

Mitigation: pin the Pydantic major version in knot core. Treat major Pydantic upgrades as coordinated migrations — translate stored specs, re-hash, bridge prior-run audit links — not as in-place dependency bumps.

## Impact analysis falls out

Because references in impl code and downstream specs (sources, ER strategies, trust configs, derivation rules, materialization configs) all resolve through the same SDK / spec entities, knot can compute impact when the ontology changes:

- **Removed slot referenced anywhere** → spec invalid or impl code fails to compile.
- **Type change on a slot** → invalidates downstream assumptions; surface as warning.
- **Renamed class / slot** → all references need update; surface as actionable list.
- **New relationship** → opportunities for new cross-class queries; informational.

Two layers:

1. **Compile-time static analysis.** Workflow compile checks every reference; incompatibilities fail the compile.
2. **Pre-change impact preview.** Tooling diffs the in-progress ontology change against the current set of dependent specs and surfaces affected items before commit.

## Config namespace — expression-tree refs for impl Config fields

When a bound impl declares a `Config` class, knot generates a companion `Config` namespace that mirrors it and produces typed expression-tree refs of the same shape as SDK slot refs:

- `Config.min_year` (declared `int`) → `ConfigRef` node of type `int`; type checker sees `int`.
- `Config.signal_slots` (declared `list[Slot]`) → `ConfigRef` node of type `list[Slot]`; resolves to spec entity refs at compile.
- At parse / registration, the spec validator confirms each `Config.<field>` resolves to a real field on the bound Config class — typos raise (commitment 16).
- At compile, each `ConfigRef` is substituted with the bound impl's current Config snapshot: primitive fields become literals, slot / class refs become resolved spec entity references.

`ConfigRef` is a node in the same expression tree walked by existing single-dispatch visitors; no parallel machinery. Full mechanism: `staging/datacontext-config-binding.md`.

## What this doesn't cover

- Spec model details — see `spec-model.md`.
- Derivation rule shape — see `derivation-and-constraints.md`.
- SQL generation — see `sql-generation.md`.
- Impl-side DataContext + Config + protocol mechanics — see `di-input-contract.md`.
- SDK pinning per impl registration — see `di-input-contract.md` § "SDK pinned per registration."
