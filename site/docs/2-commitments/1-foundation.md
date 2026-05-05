# 1. Foundation

The four commitments that establish what kind of system knot is: a compiler whose source-of-truth is a typed Pydantic spec authored programmatically at runtime, with content-addressed run identity that makes audit walk-back mechanical.

These four — commitments 1, 2, 3, and 17 — are the foundation. The remaining 13 commitments stand on top of them.

---

## Commitment 1 — Knot is a compiler.

Knot reads a typed Pydantic spec, emits a content-addressed `WorkflowSpec`, and dispatches it to an external orchestrator. **Knot does not execute work.** No internal queue, no internal scheduler, no internal SQL engine, no internal HTTP loop that touches data. The boundary is the bound DI interfaces; everything that touches data plane crosses through one.

### Rationale

Layer 1 establishes that the audit promise — "every fact in the graph traces back deterministically to spec, impl, config, source, ER, trust, correction" — is the load-bearing user-facing benefit ([the-hard-problems.md, Hard problem 5](../1-why/the-hard-problems.md#hard-problem-5-audit-walk-back-is-the-load-bearing-user-benefit)). For audit to be mechanical, every contributing artifact has to be *pinned and content-addressed at the moment of the run*.

A system that executes work directly tends to interleave dispatch and execution: the same process compiles, runs SQL, updates state, and writes results. That blurs the run-identity primitive. Compile-and-dispatch with no internal execution means the compile step is the only thing that produces a hash, and the hash is what audit walks. There is exactly one identity primitive.

A system with an internal queue accumulates *operational* identity (queued at, picked up at, retried N times) that is separate from the *logical* identity (what spec produced this output). Two identity systems is one too many for the audit story.

### Comparator anchor

| System | Compile vs execute | Where audit lives |
|---|---|---|
| **dbt** | Compiler. Hands SQL to a warehouse; warehouse executes. | dbt manifest + run results; warehouse is opaque. |
| **Bazel** | Compiler. Action graph + content-addressed cache; runner executes actions. | Action hash IS identity. |
| **Airflow** | Both. Schedules and executes via workers. Run identity is the trigger, not the spec. | Sidecar via OpenLineage / Marquez. |
| **knot** | Compiler. Hands `WorkflowSpec` to Maestro / Airflow / Argo / toy. | Compile hash IS identity. |

dbt is the closest neighbor structurally; both compile a typed model into runnable artifacts and dispatch. Bazel's hashing model is the intellectual ancestor of knot's run-identity primitive. Airflow's combined model is what knot is explicitly *not*.

### Tradeoffs / honest costs

- **Knot does not own the runtime.** A team without a working orchestrator cannot run knot. The toy scheduler bridges local-dev, but production needs Maestro / Airflow / Argo or equivalent.
- **No "knot worker" daemon.** A team that wants knot to be a single deployable that schedules, executes, and serves cannot have it. The compile/execute split is non-negotiable.
- **Some kinds of operations need awkward dispatch.** A built-in DQ check has to flow through the orchestrator like everything else, even when it would be simpler to run it inline. Commitment 12 (delegated execution) is what makes this tractable; the awkwardness is real.

??? details "Deep-dive: the compile-dispatch flow"

    The flow from [`design/knot-as-compiler.md`](../../../design/knot-as-compiler.md):

    ```mermaid
    flowchart LR
        spec[Spec nodes<br/>postgres-control] --> compile[Compiler<br/>pure transform]
        compile --> ws[WorkflowSpec<br/>content-addressed]
        ws --> dispatch[Orchestrator.submit_workflow]
        dispatch --> orch[Maestro / Airflow / Argo / toy]
        orch --> tasks[Tasks invoke bound DI impls]
        tasks --> qx[QueryExecutor / ER / Materializer / DqRunner]
        orch --> status[Knot polls for completion]
        status --> runs[(pipeline_runs)]
    ```

    Pure transform: `compile(spec_graph) -> WorkflowSpec`. No randomness, no timestamps in the hashed payload, no environment dependency. Otherwise hashing is meaningless. `compile_at` and `compile_id` go in the run record (`pipeline_runs`), not in the hashed spec.

    Architectural rules that follow:

    1. Knot core code never executes work — it dispatches.
    2. The compiler is pure. Testable as `compile(spec_graph) -> WorkflowSpec`.
    3. `Orchestrator` impls are translators — `WorkflowSpec` to native workflow format. No business logic.
    4. Tasks call back into knot's bound DI interfaces, not into knot's HTTP API.

### Cross-links

- [`design/knot-as-compiler.md`](../../../design/knot-as-compiler.md) — canonical statement.
- [`design/source-layer-contract.md`](../../../design/source-layer-contract.md) — where the team-owned ingestion layer ends and knot's compile begins.

---

## Commitment 2 — Spec-as-data, with real Pydantic object references in-memory.

The spec is Pydantic models. Between metaschema entities (classes, slots, types, constraints, derivation expressions, configs), references are **real Python object references** — not name-string lookups. `Slot.range` holds an `OntologyClass | TypeDefinition` directly; `OntologyClass.slots` holds `list[Slot]` of real objects.

Where strings live: the **persistence boundary**. JSONB writes flatten refs to names; reads rehydrate. The API / UI / postgres layer owns this round-trip.

### Rationale

Layer 1 ([Hard problem 6](../1-why/the-hard-problems.md#hard-problem-6-the-ontology-is-itself-live-data)) establishes that the ontology is itself live data — runtime-edited, programmatically authored, part of the audit chain. For impact analysis to walk the spec graph mechanically, the graph must be the actual typed entity tree, not a parallel meta-structure built on top of it.

The design-thinking pattern is direct (from [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md), pattern 2): "kill parallel meta-structures when the typed graph IS the graph." A `Reference` type with `SpecLocator` / `ConfigLocator` / `ImplLocator` was an early dead-end; the typed entities already carry the structure. Single-dispatch visitor functions over real Pydantic refs cover impact analysis without introducing a second graph.

The strings-at-the-boundary discipline is what SQLAlchemy 2.x does for ORM relationships: in-memory uses `relationship()` references directly; the FK-by-id flattening is a persistence concern. The pattern is well-precedented.

### Comparator anchor

| System | Reference style | Impact analysis |
|---|---|---|
| **LinkML** | YAML names; in-Python uses string lookups via `SchemaView` | Walks string-keyed lookups; impact analysis is a separate library concern. |
| **dbt** | Jinja `ref('model_name')` strings throughout; lineage parsed from rendered SQL | Lineage at model granularity; column-level requires extra parsing. |
| **SQLAlchemy 2.x ORM** | Real `relationship()` refs in-memory; FK at the persistence layer | Type-checker-friendly; mypy resolves the graph statically. |
| **knot** | Real Pydantic refs in-memory; names at JSONB boundary | Single-dispatch visitor over the typed tree; mypy-friendly throughout. |

LinkML is the closest neighbor on the ontology-vocabulary axis but takes the YAML-names approach, which is why knot borrows the vocabulary but not the runtime ([why-existing-systems.md, LinkML](../1-why/why-existing-systems.md#linkml--vocabulary-not-runtime)). SQLAlchemy is the structural precedent.

### Tradeoffs / honest costs

- **The persistence boundary is real work.** The two-pass parse (build entities by name, then resolve references) is hand-rolled — cycles in the ref graph need careful handling. Pydantic's native serialization doesn't do this for you.
- **Reference equality matters.** Two `Slot` objects with the same `name` are *not* the same entity if they came from different parses. The spec parser is responsible for ensuring exactly one in-memory instance per `name` within a `Spec`.
- **Diffing is non-trivial.** Comparing two specs structurally requires walking through references without infinite recursion. The canonicalization pipeline ([`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md)) builds a Pydantic-independent intermediate to handle this.

??? details "Deep-dive: the metaschema shape"

    From [`design/staging/spec-model.md`](../../../design/staging/spec-model.md):

    ```python
    class SpecBase(BaseModel):
        model_config = ConfigDict(extra="forbid", populate_by_name=True, ...)

    class Spec(SpecBase):
        id: str
        version: str
        classes: list[OntologyClass] = []
        slots: list[Slot] = []
        types: list[TypeDefinition] = []
        constraints: list[Constraint] = []

    class OntologyClass(SpecBase):
        name: str
        is_a: OntologyClass | None = None       # real ref
        mixins: list[OntologyClass] = []        # real refs
        slots: list[Slot] = []                  # real Slot refs into Spec.slots
        slot_overrides: list[SlotOverride] = []
        unique_keys: list[UniqueKey] = []
        abstract: bool = False

    class Slot(SpecBase):
        name: str
        range: TypeDefinition | OntologyClass   # real ref — discriminated union
        identifier: bool = False
        required: bool = False
        multivalued: bool = False
        derivation: DerivationExpr | None = None
        reference: ReferencePattern | None = None
    ```

    Three levels of class — naming map (per [`design/staging/spec-model.md`](../../../design/staging/spec-model.md)):

    | Level | Example | What it is |
    |---|---|---|
    | Metaschema class | `OntologyClass`, `Slot` | Pydantic classes describing the shape of an ontology spec |
    | Per-ontology Pydantic spec class | `MovieSpec` | Generated from a specific ontology; used for storage + validation |
    | SDK descriptor class | `Movie` | Descriptor-only generated class; bound impls import for query expressions |

    The SDK class (`Movie`) is **not** a Pydantic subclass — it's a descriptor-only class, generated alongside `MovieSpec`. SQLAlchemy 2.x precedent: the mapped class is for queries, separate from any data-container class. This avoids Pydantic v2 metaclass conflicts entirely. See [Commitment 4](2-the-di-seam.md) for how impls use the SDK.

### Cross-links

- [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) — Pydantic spec metaschema.
- [`design/staging/auto-generated-sdk.md`](../../../design/staging/auto-generated-sdk.md) — SDK class generation.
- [`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md) — canonicalization for hashing.

---

## Commitment 3 — Content-addressed compile hashes are run identity.

Every workflow trigger is a fresh compile. The compiler reads the *current* revisions of every spec node in the compiled workflow's dependency graph, emits a `WorkflowSpec` with revision IDs baked in, hashes the canonical form (RFC 8785 JCS), and stores it. **The hash is the run's identity.**

Reruns reference the hash, not the trigger. Identical input → identical hash → deduplicated storage. Spec edits between triggers produce a different hash naturally.

### Rationale

Layer 1 ([Hard problem 5](../1-why/the-hard-problems.md#hard-problem-5-audit-walk-back-is-the-load-bearing-user-benefit)) establishes that audit walk-back must be mechanical — a system query, not a human investigation. For that to work, every contributing artifact has to be *pinned at the moment the value was produced*: spec revisions, impl revisions, configs, source watermarks, ER decisions, trust state, correction overlay state.

The hard part is not collecting these. The hard part is making them flow from the **same primitive**. Content-addressed compile hashes are that primitive. A `WorkflowSpec` is canonicalized via JCS, hashed via sha256, and stored. Audit walks the hash; the hash dereferences to every pinned revision; the pinned revisions dereference to the actual specs that were active.

Without this, audit walks a sidecar — and sidecars drift. Atlas / OpenLineage / Marquez approximate audit by capturing what producers report ([why-existing-systems.md, Atlas](../1-why/why-existing-systems.md#apache-atlas-and-openlineage--marquez--sidecar-not-control-plane)). Knot is the producer and uses content-addressed identity by construction.

### Comparator anchor

| System | Run identity | Replay determinism |
|---|---|---|
| **Bazel** | Action hash (content-addressed) | Bit-exact replay if actions are pure. |
| **Nix derivations** | Derivation hash (content-addressed) | Bit-exact build replay. |
| **dbt manifests** | Manifest checksum + run id | Replay re-renders Jinja and re-runs SQL; not bit-exact unless underlying SQL is. |
| **Terraform plans** | Plan + state-locked apply | Plan replay is by file, not hash; state drift is real. |
| **Airflow** | Trigger ID (timestamp / DAG run id) | Replay is "rerun this DAG"; spec drift between runs is invisible. |
| **knot** | sha256 of canonical `WorkflowSpec` | Replay deterministic modulo the impl's own determinism. |

Bazel and Nix are the structural ancestors. Airflow is what knot is explicitly *not* — trigger-based identity drifts because spec edits between triggers are silent.

### Tradeoffs / honest costs

- **Canonicalization is fragile.** RFC 8785 JCS pins the JSON form, but Pydantic minor-version bumps can shift `model_dump_json` defaults. Knot builds a Pydantic-independent intermediate (per [`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md)) precisely to insulate from this — but it's real engineering work the team owns.
- **Description / metadata fields are excluded from the hash.** Typo fixes shouldn't rehash the universe (matches dbt manifest convention). The choice of which fields are RUNTIME vs CANONICAL is a real taxonomy and could be wrong; the test corpus has to keep it honest.
- **`compiled_workflows` accumulates.** Each row is small (kilobytes) but append-only. Garbage-collection retention is a deployment concern, not a knot architectural decision. A team that never GCs will eventually have a large `compiled_workflows` table. Reasonable defaults are documented; the team picks.
- **Algorithm lock-in on sha256.** Future-flexibility (BLAKE3, multihash, `sha256:<hex>` prefixed) would require widening the column. A forced flag-day is a real cost knot does not take on speculatively. If sha256 ever needs replacement, it's a coordinated migration.

??? details "Deep-dive: storage shape and replay"

    From [`design/compiled-workflow-hashing.md`](../../../design/compiled-workflow-hashing.md):

    ```sql
    CREATE TABLE compiled_workflows (
        hash             CHAR(64) PRIMARY KEY,    -- sha256 of canonical-form spec
        spec             JSONB    NOT NULL,       -- the full WorkflowSpec
        target_kind      TEXT     NOT NULL,
        target_name      TEXT     NOT NULL,
        first_compiled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        pinned_revisions JSONB    NOT NULL        -- {"ontology:Movie": "rev_a1", ...}
    );

    CREATE TABLE pipeline_runs (
        id                    UUID PRIMARY KEY,
        compiled_spec_hash    CHAR(64) NOT NULL REFERENCES compiled_workflows(hash),
        orchestrator_run_id   TEXT,
        triggered_at          TIMESTAMPTZ NOT NULL,
        triggered_by          TEXT NOT NULL,
        status                TEXT NOT NULL,
        finished_at           TIMESTAMPTZ,
        error                 TEXT
    );
    ```

    A run *is* `(triggered_at, hash, orchestrator_run_id)`. Two runs with identical state share a `compiled_workflows` row and produce two `pipeline_runs` rows.

    Canonical form rules ([`compiled-workflow-hashing.md`](../../../design/compiled-workflow-hashing.md)):

    - JSON serialization with sorted keys at every nesting level.
    - No insignificant whitespace.
    - Sorted task list by `task.name`; sorted `depends_on` within each task.
    - No timestamps, run IDs, or compile-time metadata in the hashed payload.
    - `pinned_parent_runs` for relation classes ([Commitment 8 in 3-data-and-resolution.md](3-data-and-resolution.md)).

    API surface:

    ```
    POST  /runs/{target}              # fresh compile + dispatch
    POST  /runs/{target}?parents=...  # pin specific parent runs (relation classes)
    POST  /runs/replay/{hash}         # re-dispatch existing compiled spec
    GET   /workflows/{hash}           # inspect a compiled spec
    GET   /workflows/{hash}/runs      # all runs that used this hash
    ```

    `POST /runs/replay/{hash}` is not a debugging affordance — it is the contract for reproducibility.

### Cross-links

- [`design/compiled-workflow-hashing.md`](../../../design/compiled-workflow-hashing.md) — canonical statement.
- [`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md) — JCS canonicalization, RUNTIME vs CANONICAL field taxonomy, Pydantic-version isolation.
- [`design/staging/cross-class-pinning.md`](../../../design/staging/cross-class-pinning.md) — `pinned_parent_runs` is part of the canonical form.

---

## Commitment 17 — Programmatic-first authoring; no YAML; no fork of LinkML/SHACL/OWL.

Spec is authored via UI/API as Pydantic models. The vocabulary borrows from LinkML (class / slot / range / mixin / `permissible_values` / `is_a` / `multivalued`) because it's good ontology terminology. Knot is **not** LinkML-compatible: no LinkML library dependency, no YAML import/export, no SHACL escape hatch, no OWL DL reasoner. Cross-row and cross-class constraints use the same expression-tree machinery as derivations; knot's SQL generator emits validation queries.

JSON Schema export falls out of Pydantic for free.

### Rationale

Layer 1 ([Hard problem 6](../1-why/the-hard-problems.md#hard-problem-6-the-ontology-is-itself-live-data)) establishes that modeling is exploratory: a team wiring up a new source discovers ontology gaps in real time. YAML hand-editing for a non-trivial ontology produces typos, misaligned references, and silent semantic errors. A typed, programmatic authoring surface catches reference errors at parse time, validates cross-class consistency at publish time, and shows impact preview before the change goes live.

This is the **reflective ontology compiler** classification: the publish gate validates each spec edit against the running system that interprets the spec — current registered impls, current trust policy, current bound configs. CI cannot do this work because CI does not have the runtime state.

LinkML's vocabulary is the right ontology terminology, and knot borrows it. Knot does not adopt the LinkML library because LinkML's runtime is YAML-first, file-based, and oriented toward generating artifacts. Adopting it would inherit YAML round-trips and decouple the metaschema from knot's identity model.

SHACL is a check for RDF graphs; knot's structural validation is part of the pipeline that produces tabular fact data. The data residency mismatch — RDF triples vs Spark/Trino on Iceberg/parquet — is dispositive ([why-existing-systems.md, SHACL](../1-why/why-existing-systems.md#shacl--wrong-primitive-for-tabular-lake-data)).

OWL DL is a reasoner; knot is a pipeline. "Why does the graph say X" answered by "the OWL reasoner inferred it from these axioms" is not the deterministic walk-back the audit promise needs ([why-existing-systems.md, OWL DL](../1-why/why-existing-systems.md#owl-dl--wrong-primitive-for-deterministic-replay)).

### Comparator anchor

| System | Authoring surface | Constraint mechanism | Open-ecosystem interop |
|---|---|---|---|
| **LinkML** | YAML files in a repo | Native LinkML constraints + SHACL export | Multi-target codegen (Python, JSON Schema, SHACL, OWL); explicit goal. |
| **SHACL** | RDF / Turtle | Shape constraint language for RDF | Native RDF/SHACL ecosystem. |
| **OWL DL** | OWL files; Protégé authoring | DL reasoner-derived inferences | Native OWL/RDF ecosystem. |
| **dbt** | YAML schema files + Jinja-templated SQL | `tests:` blocks (column-level + custom) | dbt-native. |
| **knot** | Programmatic UI/API over Pydantic | Same expression tree as derivations + DataContexts | Non-goal unless the team chooses; bound impl decision. |

The pattern: knot's authoring surface is more constrained than LinkML's (no YAML, no multi-target codegen) and the constraint mechanism is more uniform (one expression tree, not separate constraint sub-language). The cost of cross-ecosystem interop is borne explicitly per-target via bound materialization impls, not absorbed as a knot deliverable.

### Tradeoffs / honest costs

- **No LinkML YAML import.** A team migrating from a LinkML-authored ontology has to translate to knot's Pydantic shape. The vocabulary lines up; the surface does not.
- **No SHACL escape hatch.** A constraint that's natural in SHACL but awkward in the expression tree is awkward in knot. The unified-expression-tree commitment ([Commitment 10](5-pipeline-and-sources.md)) is what makes constraint authoring uniform; it's also what rules out SHACL-style escape hatches.
- **No DL reasoner.** Classification rules (rule-based class membership) are rejected at compile time per [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) — "complexity vs. value; use a derived slot instead." A team that wants OWL-style classification has to model the membership as a derivation.
- **JSON Schema is the only out-of-the-box export.** It falls out of Pydantic for free. Anything else (LinkML YAML, SHACL, OWL) is a bound materialization impl decision the team makes per binding, not a knot deliverable.

??? details "Deep-dive: programmatic authoring + constraints share machinery"

    Authoring is via a typed UI/API over the Pydantic spec. A constraint is a `Constraint` Pydantic node whose body is the same expression tree as derivations and DataContexts:

    ```python
    Constraint(
        name="movie_year_range",
        primary=Movie,
        body=BoolOp(op=BoolOpKind.AND, args=[
            Compare(op=CompareOp.GE, left=SlotPath(from_class=Movie, slots=[year_slot]),
                    right=Literal_(value=1888)),
            Compare(op=CompareOp.LE, left=SlotPath(from_class=Movie, slots=[year_slot]),
                    right=Literal_(value=2100)),
        ]),
        severity=Severity.ERROR,
    )
    ```

    The same `BoolExpr` / `Compare` / `SlotPath` types appear in slot derivations, ER signal slots, DataContext bodies, and translator backward-chain queries. One canonical form, one SQL generator, one impact-analysis visitor — see [Commitment 10 in 5-pipeline-and-sources.md](5-pipeline-and-sources.md).

    The publish gate at runtime ([`design/_meta/`](../../../design/_meta) reflects this) does Pydantic parse → reference resolution → DataContext cross-checks against every registered impl → impact preview, atomically. Failure raises; nothing partially-applies. See [Commitment 16 in 4-quality-and-publish.md](4-quality-and-publish.md).

### Cross-links

- [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) — Pydantic metaschema and feature support summary.
- [`design/staging/derivation-and-constraints.md`](../../../design/staging/derivation-and-constraints.md) — the unified expression tree.
- [`design/goals.md`](../../../design/goals.md) §5 — "make ontology authoring engineer-friendly and runtime-editable."
- [`1-why/why-existing-systems.md`](../1-why/why-existing-systems.md) — LinkML, SHACL, OWL DL positioning.

---

## Open tensions on this page

- **Programmatic-first vs ecosystem export.** Knot's posture is single-team / no-ecosystem, but a team that wants to publish the ontology to LinkML YAML or SHACL for a downstream consumer can do so via a bound materialization impl. The architecture supports it; the deliverable is the team's choice. This is consistent with the trust posture (commitment 5) and the free-form materialization commitment (commitment 15) but worth flagging: if an external consumer ever pressures the team toward "must export LinkML," the cost lands on the team's bound impl, not on knot core.
- **Content-hash stability across Pydantic majors.** The Pydantic-independent intermediate insulates knot from Pydantic v2 → v3 churn for compute_content_hash, but a v3 migration is still a coordinated event (per [`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md)). The plan is in place; the cost is real.
