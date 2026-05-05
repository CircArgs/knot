# Knot — goals

What knot is for. Read this before any other doc.

---

## What knot is

A control plane for a knowledge graph built from multiple, partially-redundant, partially-disagreeing data sources. Knot owns the **ontology**, the **provenance**, and the **audit walk-back** that lets you ask "why does this graph say X?" and get a deterministic answer in seconds. Knot does **not** own execution — it compiles workflows and delegates execution to whatever the team has wired up.

**Classification: a reflective ontology compiler.** "Reflective" in the PL-theory sense — the compiler validates each spec edit against the running system that will execute the spec (current registered impls, current trust policy, current source watermarks, current bound configs). The validator is the same runtime that interprets the spec. CI cannot do this work because CI does not have the runtime state.

## Who uses knot

knot is a **tool for one team** to manage and evolve their knowledge graph. There are no tenants. The team owns:

- The knot deployment.
- The ontology spec (authored programmatically via UI/API).
- All bound DI implementations (ER, materialization, custom DQ, query execution backends, translator).
- Everything outside knot's seams that the impls touch (lake infrastructure, graph stores, model files, secrets, etc.).

**External users come into the picture only at three surfaces, all narrow:**

1. **Reading from finished outputs** — apps / services / analysts hitting the materialized graph (Neo4j, Iceberg, vector store, parquet) that the team's bound materialization impls produce.
2. **Reading via the translator** — consumer-facing query expansion against `resolved_facts`.
3. **Submitting corrections via the UI** — corrections land in postgres-control briefly and migrate to the lake at the next pipeline run via the `_user_corrections` source (high-trust). Constrained surface; users don't author impls or edit the spec.

**Implications:**

- **Trusted-author posture is real, not aspirational.** Spec authors and impl authors are members of the operating team. No sandboxing. No multi-tenant safety. Full Python access for impls.
- **No marketplace, no plugin ecosystem, no third-party impl distribution.** The team writes their impls (or imports libraries inside them). Knot isn't shipping reference impls for external adoption.
- **Comparative claims like "every team would reinvent X" don't apply.** There's no every-team. The one team writes their thing once.
- **Open-ecosystem interop (RDF, SHACL export, LinkML YAML)** is not a goal unless the team specifically chooses to publish to that ecosystem — and even then, it's a bound impl decision, not a knot deliverable.

---

## Goals

### 1. Unify ontology, data pipeline, and audit into one system.

Schema, sources, entity-resolution, data-quality checks, materialization, human corrections — every concern that touches the graph is modeled as a typed Pydantic spec entity (or a knot-hosted DI impl, which is itself a versioned spec entity). One system; one revision model; one audit chain.

### 2. Make audit walk-back deterministic and instant.

Any fact in the graph traces back through: which run produced it → which compiled workflow → pinned spec revisions → contributing sources → trust state at the moment → ER decision → corrections that participated. Mechanical, not exploratory. Order-of-magnitude finer than what general orchestrators provide.

### 3. Treat the spec as a single source of truth — readable both as data and as code.

The Pydantic spec model drives:
- Generated typed Python SDK classes that bound impls import.
- Generated SQL for normalize / merge / validate / derive / materialize.
- Generated structural and constraint validation queries.
- Impact analysis, dependency-graph computation, content-addressed compile hashes.

One source of truth; everything else is derived.

### 4. Handle multi-source data with ER, trust, and provenance as first-class.

The same canonical entity may be described by N sources that disagree. Knot treats every property as implicitly multi-valued (one contribution per source); trust resolution happens at query time; lineage lives in the data itself, not in a sidecar table.

### 5. Make ontology authoring engineer-friendly and runtime-editable.

Users author the ontology programmatically via UI and API — no YAML, no second authoring layer. Spec edits go through draft → publish; the publish gate validates everything (Pydantic parse, reference resolution, DataContext cross-checks, impact preview). Modeling is a runtime activity; nothing requires a code redeploy.

### 6. Allow customization via dependency injection without forking knot.

Every team-bound stage (entity resolution, custom data-quality checks, materialization to specific targets, query execution against the lake, consumer query translation) is a bound DI impl following one universal pattern: protocol + typed `DataContext` class attributes + pure-data runtime context + impl-defined `Config`. The bound impls are knot-hosted Python source — browser-editable, no separate deploy step.

### 7. Compile; never execute.

Knot reads spec, emits a content-addressed `WorkflowSpec`, hands it to an orchestrator (Maestro / Airflow / Argo / toy). Knot polls for completion and records the run. Knot has no internal queue, no internal scheduler, no internal SQL engine. Dispatch via bound `QueryReader` / `Materializer` / `Introspector` / `ViewManager` impls.

### 8. Survive at scale.

Spark + Trino on the lake side. Postgres on the control plane. Arrow at the cross-engine boundary. Content-addressed compile hashes deduplicate identical workflows and enable reproducible reruns. SCD2 entity bindings + canonical-id lineage support merge / split history without recursive CTEs.

### 9. Make the polymorphic / discriminator pattern tractable.

A class like `Identifier` polymorphically points at any entity. Knot's static dependency graph can't enumerate concrete connections from the spec alone — the consumer (e.g., an ER impl using `Identifier` as a signal) must declare the dependency explicitly. The principle: anything knot needs to track must be declared *somewhere*.

### 10. Fail loud.

Pydantic `extra="forbid"` everywhere. Reference resolution at parse time. Compile-time cross-validation between configs and DataContexts. Errors raise; never swallow. Validation rules surface offending rows in a uniform shape; publish gates enforce.

---

## What knot is NOT

- **Not an orchestrator.** Knot dispatches; Maestro / Airflow / Argo / toy run things.
- **Not a SQL engine.** Knot generates SQL; bound `QueryExecutor` impls execute.
- **Not a multi-target codegen platform.** Spark + Trino. JSON Schema falls out of Pydantic for free; descriptor SDK and validation/derivation SQL are the deliverables.
- **Not LinkML / SHACL / OWL.** Knot borrows LinkML's vocabulary because it's good ontology terminology; the metaschema is Pydantic-native, no LinkML library dependency, no SHACL escape hatch, no DL reasoner.
- **Not multi-tenant SaaS hostile-author safe.** Trusted-author posture: bound impls are full Python; no sandboxing.
- **Not an alerting / notification system.** Failures surface; the orchestrator (or an optional bound notifier impl) routes alerts.
- **Not opinionated about ingestion.** Sources land in the lake however the team wants; knot starts at normalize.

---

## Read next

- `core-design.md` — the architectural commitments that make knot knot.
- `knot-as-compiler.md` — the compiler/runtime split, the canonical pattern.
- `source-layer-contract.md` — what knot expects from teams about source data.
- `compiled-workflow-hashing.md` — how runs are identified and reproduced.
- `staging/` — working drafts of the rest of the design.
