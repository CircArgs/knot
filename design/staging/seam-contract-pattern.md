# Seam contract pattern

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How knot's contracts with bound DI implementations are shaped at the seam.

## The pattern

Each seam interface is **a protocol (ABC) the impl implements + a context object the impl receives**. The impl is bound to the protocol; the context mediates every interaction with knot's data plane.

Sketch (using ER as the example, per the design refined in `di-input-contract.md`):

```python
from typing import ClassVar
from knot.ontology import Movie, Identifier   # auto-generated SDK; see auto-generated-sdk.md

class ERProtocol(abc.ABC):
    @abstractmethod
    def resolve(self, ctx: ERRuntimeContext, **datacontexts) -> Iterable[Edge]:
        ...

class MovieResolver(ERProtocol):
    # DataContexts: declared as ClassVar attrs; knot reads at registration,
    # walks for impact analysis, materializes per-run via QueryExecutor.
    candidates: ClassVar[DataContext[Movie]] = DataContext(
        primary=Movie,
        filter=(Movie.year > 1900),
    )
    movie_identifiers: ClassVar[DataContext[Identifier]] = DataContext(
        primary=Identifier,
        filter=(Identifier.entity_class == "Movie"),
    )

    class Config(BaseModel):
        # impl-defined runtime knobs; knot stores in postgres-control,
        # threads into ctx as pure data per run
        blocking: list[Slot]
        matching: list[Slot]
        threshold: float = 0.85

    def resolve(self, ctx, candidates, movie_identifiers) -> list[Edge]:
        # ctx: pure data (config + binding info + any upstream-job state)
        # candidates / movie_identifiers: pre-materialized rows per the declared DataContexts
        ...
```

Two surfaces compose the contract:

- **Protocol** — what the impl must implement (`resolve(ctx, **datacontexts) -> list[Edge]`).
- **Context** — pure data (per `di-input-contract.md` § "Context is pure data"); the impl's config + binding info + upstream job state. **No methods object**; `ctx.config.threshold`, not method calls. There is no separate "strategy spec" entity — the impl IS the strategy, with its config attached to the binding.
- **DataContexts** — class-level attributes typed as `DataContext[...]`; declare what data the impl reads. Knot walks them at registration; materializes them at runtime via QueryExecutor; passes the resulting rows to the impl as named arguments matching the attribute names.

Impls reference ontology classes and attributes as typed objects (`Movie`, `Movie.year`, `Movie.credits`), not as strings. See `auto-generated-sdk.md` for the SDK pattern, `di-input-contract.md` for the DataContext + context + config mechanics.

## Why this over function-signature-only contracts

- **Hides knot's internal complexity.** The impl declares a DataContext like `DataContext(primary=Movie, include=[Movie.credits], filter=Movie.year > 1900)`. Knot resolves the request — generates SQL from the structural relationships in the spec, materializes a view via the bound `Materializer`, hands the impl a table/view name (per `query-executor.md`). Reification, partitioning, change streams, mutable-fields ledger, SCD2 bindings, cross-class pinning lens: invisible to impl. Knot's storage can evolve without changing the contract.
- **Auditability falls out for free.** Every context method call is logged. The audit IS the call log — what was asked for, with which filters, when, by whom. Better than "impl read static table X," because you see actual per-run data needs including filters.
- **Extensible.** Adding a method to context (e.g., `get_embeddings(class)`) doesn't change the protocol signature or the storage layout. Impls use new methods or ignore them.
- **Testable.** The context is a typed interface; mock it for unit-testing impls in isolation.

## Generalization across seam interfaces

The pattern applies to every seam interface — `ERProtocol`, `DqRunner`, `MaterializationProtocol`, `Translator`, the four `QueryExecutor` protocols (`QueryReader`, `Materializer`, `Introspector`, `ViewManager`), etc. Each gets its own `(protocol, context)` pair:

- **Protocol** declares what the impl must implement.
- **Context** is **pure data** knot fills in per run (per `di-input-contract.md` § "Context is pure data") — strategy / config fields, binding info, upstream-job state. **Not a methods object.**
- **DataContexts** on the impl class declare what data the impl reads (per `di-input-contract.md`); knot walks them at registration for tracing and impact analysis.

Exact shapes for each interface are designed per-interface. This doc captures the meta-pattern only.

### What "interface" doesn't mean here

The earlier framing of "seven seam interfaces" (LakeWriter, LakeReader, MergeEngine, SchemaValidator, DqRunner, MaterializationWriter, Translator) was a loose architectural-primitive framing. **It's not a fixed set, and the per-binding scope is free-form.** Concretely:

- `MergeEngine` — collapses into knot-internal logic (joins per-source-facts to canonical_ids using ER bindings + Beta-Bernoulli trust update). No team impl required.
- `SchemaValidator` — mostly knot-internal SQL emitted from spec constraints + `Constraint` nodes (per `spec-model.md`); optional team-bound `DqRunner` for what the spec can't express.
- `MaterializationWriter` — **not per-class**. A materialization impl declares whatever DataContexts it needs (single or multi-class, joined / projected / filtered) and publishes to whatever target however it wants. One impl can span classes, produce multiple outputs, target multiple downstream systems. See `di-input-contract.md` and the `dq-design.md` patterns.
- `LakeReader` / `LakeWriter` — replaced by the orthogonal `QueryExecutor` protocols (`QueryReader`, `Materializer`, `Introspector`, `ViewManager`) per `query-executor.md`.
- `Translator` — bound impl for consumer-facing query expansion. Same DI pattern.

The unifying claim: **all team-bound stages follow the protocol+context+DataContexts pattern with knot-hosted source code (per `di-input-contract.md`).** The list of named "interfaces" is just a naming convention; the architectural primitive is the DI pattern itself.

## Granularity of context methods

Resolved: **typed SDK expressions, not string DSLs or string-keyed filters.** The impl composes queries using auto-generated SDK classes (`Movie.year > 1900`, `Movie.credits.where(Credit.role.in_(["director"]))`); knot resolves them against the appropriate backing table. No `query(q, lang)` escape hatch — the SDK's expression surface is rich enough to express what impls need without resorting to dialect-specific strings.

Why this beats the alternatives:

- **Coarse-grained "give me everything"** transfers far more than needed; doesn't scale.
- **String-DSL escape hatches** make knot a multi-dialect query engine, kill auditability, couple impls to a specific dialect.
- **Pydantic-typed string-keyed filters** (e.g., `Filter(year__gt=2000)`) are auditable but lose IDE autocomplete and mypy checking.
- **Typed SDK expressions** are auditable (each expression is structured data), type-safe (mypy catches typos and bad operators), IDE-friendly (autocomplete on `Movie.`), and statically discoverable (impact analysis on ontology changes).

## What this doesn't cover

- The specific `(protocol, context)` shape for each seam interface — those are per-interface designs.
- The impl side — the team's bound impls implement the protocols; their internal logic is out of scope.
- How knot internally resolves context method calls into storage reads/writes — its own design concern.
