# 4. Quality and publish

Three commitments — 14, 15, and 16 — describe how knot validates data quality, publishes outputs to consumers, and surfaces failures. Each one is a deliberate constraint on what knot ships out of the box and what's left to bound impls.

The shared theme: **knot ships logical defaults plus the seam to extend them; failures raise; nothing silently degrades.**

---

## Commitment 14 — Two-layer DQ: built-in bundle + custom DqRunner.

Knot ships a configurable bundle of common DQ check types (freshness, drift, cluster-size outliers, cross-source agreement, cycle detection, null-rate trends, source-coverage drop). Each is a typed Pydantic check definition with sensible defaults; runtime-editable per deployment / per class. Knot generates SQL; the bound `QueryReader` runs it.

Custom domain-specific or ML-based checks bind via the `DqRunner` protocol — same DI pattern, declared DataContexts, impl-defined Config. Failures from both surface in a uniform `(rule_id, class_name, slot_name, offending_pk, detail, severity)` shape. Built-ins are opt-out per check; custom impls compose alongside or replace.

### Rationale

Layer 1 doesn't have a "DQ hard problem" page; the load-bearing motivation comes from [Hard problem 5](../1-why/the-hard-problems.md#hard-problem-5-audit-walk-back-is-the-load-bearing-user-benefit) (audit walk-back) and [Hard problem 6](../1-why/the-hard-problems.md#hard-problem-6-the-ontology-is-itself-live-data) (the ontology as live data). Two implications:

1. **DQ failures must surface in the audit chain.** They are part of the run record, walkable from any fact's `pipeline_run` through the validation report.
2. **DQ is part of the publish gate.** Severity-ERROR failures block publish; this is what keeps the spec-edit-validates-against-runtime-state property real.

The two-layer model resolves a common tension. Shipping no DQ leaves the obvious bundle (freshness, drift, cluster-size outliers) for the team to write from scratch. Shipping a giant fixed bundle locks in choices that don't fit the team's domain. Two layers — opt-out built-ins + custom impl seam — gives both: hit-the-ground-running for the obvious, full extensibility for the domain-specific.

The "knot doesn't run SQL itself" property (per [Commitment 1 in 1-foundation.md](1-foundation.md) and [Commitment 12 in 6-execution-delegation.md](6-execution-delegation.md)) means built-in DQ runs the same way custom DQ runs: knot generates SQL, the bound `QueryReader` runs it, failures arrive in a uniform shape. Same dispatch pattern as everything else; nothing reinvented.

### Comparator anchor

| System | DQ model | Custom check shape |
|---|---|---|
| **Great Expectations** | Library of expectation classes; declarative checks per dataset | Custom expectations subclass; runs in-process. |
| **Soda Core / Soda Cloud** | YAML-declared checks; SQL generated; runs against warehouse | Custom checks via SQL templates. |
| **dbt tests** | YAML-declared `tests:` blocks (column-level + custom); runs as SQL | Custom tests as SQL macros / `dbt test` runs. |
| **Monte Carlo / Anomalo** | Hosted DQ + ML drift detection | Configured per-dataset; not a knot-comparable shape. |
| **knot** | Built-in bundle (typed Pydantic) + `DqRunner` protocol | Bound impl with declared DataContexts + Config. |

Great Expectations and Soda are the closest neighbors on the structural axis. dbt tests is the closest *anti-pattern* — YAML-declared checks tied to SQL macros, with no declarative footprint that integrates with a typed metaschema's impact analysis. Knot's bundle is a typed Pydantic-shaped equivalent of GE/Soda; the `DqRunner` is the extensibility seam.

### Tradeoffs / honest costs

- **The built-in bundle locks in a list of check types.** Adding a new check type to the bundle is a knot-core change; until then, teams that need it write a `DqRunner`. The list ships as: freshness, cluster-size outlier, degree outlier, cross-source agreement, cycle detection, null-rate trend, validation failure rate trend, source-coverage drop. Reasonable but opinionated.
- **Severity is a binary (ERROR / WARNING).** A team that wants graduated severity (info / minor / major / blocker) has to map onto the binary. Probably fine; flagging.
- **No graph-engine DQ in the bundle.** PageRank, community detection, connectivity checks need a real graph engine. Not shipped (knot ships no graph runtime). A team that needs this writes a `DqRunner` with NetworkX / GraphFrames / igraph.
- **Knot doesn't handle alerting.** Severity gates the publish gate; routing failures to humans is the orchestrator's job (Maestro / Airflow / Argo each have native alerting) or an optional bound `Notifier` impl. A team expecting "knot pages me when a check fails" has to wire that up; the architecture supports it.

??? details "Deep-dive: built-in shapes and the DqRunner protocol"

    From [`design/staging/dq-design.md`](../../../design/staging/dq-design.md):

    Built-in check shape (typed Pydantic):

    ```python
    class Severity(Enum):
        ERROR = "error"           # blocks publish gate; pipeline_run.status = failed
        WARNING = "warning"       # recorded; doesn't block

    class FreshnessCheck(BaseModel):
        enabled: bool = True
        default_threshold_days: int = 30
        per_class: dict[OntologyClass, int] = {}    # real OntologyClass refs as keys
        severity: Severity = Severity.WARNING

    class ClusterSizeOutlierCheck(BaseModel):
        enabled: bool = True
        z_score_threshold: float = 3.0
        minimum_cluster_size: int = 5
        severity: Severity = Severity.ERROR

    class BuiltinDQConfig(BaseModel):
        freshness: FreshnessCheck = FreshnessCheck()
        cluster_size: ClusterSizeOutlierCheck = ClusterSizeOutlierCheck()
        # ... etc.
    ```

    `BuiltinDQConfig` is a deployment-level configuration object stored in postgres-control, runtime-editable. Real Pydantic refs to `OntologyClass` and `Slot` so impact analysis walks them naturally.

    Each built-in:

    1. Knot's SQL generator emits the check's SQL given the spec + config.
    2. Knot dispatches via `QueryReader.execute(QueryRequest(sql=..., dialect=..., invoking_run_id=..., invoking_node_name=...))`.
    3. Arrow result rows are interpreted as failure records in the uniform `(rule_id, class_name, slot_name, offending_pk, detail, severity)` shape.
    4. Failures aggregated into the run's validation report; recorded in `pipeline_runs`.

    Custom DqRunner (same DI pattern as ER, Materialization):

    ```python
    class DqRunner(ProtocolBase):
        candidates: ClassVar[DataContext[...]] = ...

        class Config(BaseModel):
            ...

        def check(self, ctx, candidates) -> list[DqFailure]:
            ...

    class DqFailure(BaseModel):
        rule_id: str
        class_name: str
        slot_name: str | None = None
        offending_pk: str
        detail: str
        severity: Severity
    ```

    Composition: built-ins and custom DqRunners run sequentially during the validate stage. Failures unified into one report. Each carries its own `rule_id` for routing.

    Failures and alerting:

    - knot records failures in postgres alongside `pipeline_run`. Visibility via the API.
    - knot does not handle alerting itself. Two paths:
      - Orchestrator-native (default). Maestro / Airflow / Argo each have native alerting.
      - Bound `Notifier` impl (optional). Same DI pattern.

### Cross-links

- [`design/staging/dq-design.md`](../../../design/staging/dq-design.md) — full design.
- [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) §"Validation SQL shape" — the uniform 5-column failure SELECT shape.
- [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"Validate" — the validate stage where DQ runs.
- [`design/core-design.md`](../../../design/core-design.md) commitment 14.

---

## Commitment 15 — Free-form materialization.

A materialization is just a bound DI impl. Same protocol + DataContext + Config pattern. The impl declares whatever data it needs (single class, multi-class joined, denormalized, filtered), publishes to whatever target however it wants (Neo4j via S3 + LOAD CSV; Iceberg; vector store; CSV exports). One impl can target multiple downstream systems. **Not per-class.**

Knot's role: provide the data through QueryExecutor; let the impl publish.

### Rationale

The design-thinking pattern is direct (from [`design/_meta/design-thinking-patterns.md`](../../../design/_meta/design-thinking-patterns.md), pattern 5): "I framed materialization as 'one impl per (class, target)'. He generalized: I wasn't quite thinking about materialization as like one entity sort of thing. I kind of imagined that impls can have the data contexts like we discussed and materialization would also have some and then the implementer of the impl just exactly targets what data they want with the data contexts."

The universal DI pattern (per [Commitment 4 in 2-the-di-seam.md](2-the-di-seam.md)) covers the case. A materialization that joins Movie + Credit + Person and writes to Neo4j is structurally the same shape as one that reads Movie alone and writes a parquet export — they just declare different DataContexts and write to different targets. Inventing a per-class binding model would be a separate mechanism for what the universal pattern already handles.

The "knot publishes to whatever, however" framing keeps the seam clean. Knot owns providing the data through the QueryExecutor protocols ([Commitment 12 in 6-execution-delegation.md](6-execution-delegation.md)); the impl owns the publish path. A team that wants both Neo4j + a parquet export can do both in one impl, or split into two; knot doesn't prescribe.

### Comparator anchor

| System | Materialization model | Per-class assumption |
|---|---|---|
| **dbt** | Models with `materialized: 'table' / 'view' / 'incremental'` config | Per-model. Materialization is a per-model decision. |
| **dbt-utils + dbt-spark / dbt-snowflake adapters** | Adapter-specific incremental strategies | Adapter handles the destination. |
| **DataJunction** | Materialization as a typed concern in the semantic layer | Per-node. |
| **knot** | Free-form bound impl per target. One impl can span classes / targets. | Not per-class. The impl declares its DataContexts and targets. |

The cleanest comparator is dbt's materialization model, but dbt is opinionated about per-model materialization; multi-target publish typically requires multiple models or a `dbt-utils` macro. Knot's free-form approach is structurally different: a materialization is a Python impl, not a SQL model with a config knob.

### Tradeoffs / honest costs

- **The team writes the publish path.** A simple "write Movie to Neo4j" impl is real Python code: read DataContext, format CSV, push to S3, run `LOAD CSV` via Cypher. Knot doesn't ship a Neo4j adapter or a vector-store adapter; the impl is what bridges. For a team with a single graph-store target, this is real work the first time. Subsequent impls can inherit / share helpers.
- **No materialization framework or DSL.** dbt's `materialized:` config is convention-light; knot's "you write the impl" is convention-free. The free-form posture is right for the trust posture (commitment 5) and matches the universal-DI commitment (4); it leaves more on the team.
- **Snapshot-rebuild is the default; incremental is the impl's choice.** Per [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"Materialization model: snapshot-rebuild (default)". A team that needs incremental materialization for cost / latency reasons writes it; the architecture supports it (knot has structural information that dbt doesn't — typed Pydantic spec, derivation rules as structured Pydantic objects, computable reference graph) but doesn't ship it as the default.
- **One impl can publish to N targets.** This is flexibility, not a constraint. A team that wants per-target isolation has to split impls deliberately.

??? details "Deep-dive: targets and the snapshot-rebuild default"

    From [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"Materialization":

    | Target | Consumer | Pattern |
    |---|---|---|
    | Graph store (Neo4j, custom) | Apps, services, search | Forward-chained — impl reads denormalized DataContexts + emits node/edge CSVs (often via S3 + LOAD CSV) or pushes via Bolt MERGE. |
    | Vector store | Similarity search | Impl reads relevant slots, computes embeddings, writes vectors. |
    | Lake analytics table (Iceberg / parquet) | Analysts, BI, ad-hoc queries | Forward-chained — impl writes a denormalized view via the Materializer protocol. |
    | CSV / parquet export | Downstream BI, ML pipelines | Impl reads relevant DataContexts, dumps to a known location. |

    Operates over canonical entities post-merge. Materialization impls read from `resolved_facts` (post-merge) via their DataContexts; knot's QueryExecutor materializes the views that fulfill the DataContexts.

    Snapshot-rebuild default:

    - Each publish run computes the full set of derived facts via one-shot SQL queries against `resolved_facts` plus the spec-declared derivation rules.
    - The impl writes the result to the target (Neo4j database alias, Iceberg branch, partitioned parquet directory) and atom-swaps the alias / branch / partition pointer.
    - Previous publishes remain available until garbage-collected per retention policy.
    - Not incremental by default. No DELETE-old-then-INSERT-new merging, no incremental view maintenance.

    Forward / backward chain symmetry, per [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md):

    - **Backward-chain SQL** (translator at query time): `SELECT what should be true`.
    - **Forward-chain SQL** (materializer at publish time): `INSERT INTO target SELECT what should be true` — same query, different wrapping.
    - Both compile from the same derivation rule. They agree by construction.

    Embeddings note (from [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md)):

    - Embedding generation impl: same DI pattern as any materialization impl. Declares DataContexts over the entities it embeds, runs the embedding model, writes vectors.
    - Embedding query impl (consumer-facing, parallel to the translator): surfaces similarity search against the embedding store.
    - Same shape as graph-store materialization: one DI to produce, one to query.

### Cross-links

- [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) §"Materialization".
- [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) — the universal DI pattern.
- [`design/reification-strategy.md`](../../../design/reification-strategy.md) §"Cost" — why reification doesn't translate to runtime joins for general consumers (downstream of materialization).
- [`design/core-design.md`](../../../design/core-design.md) commitment 15.

---

## Commitment 16 — Failure modes are loud.

- Pydantic `extra="forbid"` everywhere; unknown fields raise at parse.
- Reference resolution at parse time; dangling refs raise.
- DataContext registration validates against spec; broken refs fail registration.
- Config edits cross-checked against the impl's DataContext-reachable slots; mismatches fail compile.
- ER post-processing: conflicts and splits surface for review by default; auto-merge / auto-split is opt-in per impl.
- Validation queries return offender rows; built-in DQ checks emit failures in the uniform shape; severity gates publish.
- QueryExecutor errors raise typed exceptions, never swallow.

### Rationale

This is a facet of the **reflective ontology compiler** classification: the publish gate validates each spec edit against the runtime that interprets it. For the gate to be keepable, every check has to fail loudly. Silent degradation breaks the audit promise — a fact in the graph that came from a partially-applied edit, a dangling ref, or a swallowed exception cannot be walked back.

The pattern is consistent across knot. From [`design/goals.md`](../../../design/goals.md) §"Goals" #10:

> Pydantic `extra="forbid"` everywhere. Reference resolution at parse time. Compile-time cross-validation between configs and DataContexts. Errors raise; never swallow. Validation rules surface offending rows in a uniform shape; publish gates enforce.

The DataJunction comparison is sharp here. DJ's legacy materialize methods "log-and-swallow non-2xx responses, returning empty handles — the only signal is empty `urls`" ([`design/staging/query-executor.md`](../../../design/staging/query-executor.md) §"Errors raise; never swallow"). DJ's newer pre-agg path raises with typed errors. Knot adopts the latter pattern uniformly.

The ER post-processing posture (conflicts and splits surface for review by default) is a specific case of the same principle: stability by default; impls opt in to auto-applying. Both conflict and split are cases where ER changed its mind about the cluster shape; the question is whether knot trusts ER's revised opinion enough to silently adjust canonical_ids. Default no.

### Comparator anchor

| System | Failure default | Configuration |
|---|---|---|
| **DataJunction (legacy)** | Log-and-swallow non-2xx responses on materialize | Subclass override required to raise. |
| **DataJunction (newer)** | Typed exceptions on errors | Default. |
| **dbt** | Test failures: configurable (warn / error); compilation errors raise | Per-test severity. |
| **Pydantic v2 (default)** | `extra="ignore"` | `extra="forbid"` opt-in. |
| **knot** | Always raise; loud everywhere; severity gates publish | Severity per check; `extra="forbid"` everywhere. |

Pydantic's default of `extra="ignore"` is an interesting comparison: knot uses `extra="forbid"` everywhere because the alternative invites silent spec drift. The cost is real (every new field needs explicit handling); the audit promise is what justifies it.

### Tradeoffs / honest costs

- **`extra="forbid"` everywhere is a strict regime.** Adding a new field to a Pydantic model means every place that constructs that model must be updated, even places that previously didn't care. The discipline is right for the audit story; the cost is real engineering work.
- **Reference resolution at parse time means no lazy schemas.** A schema that depends on an unresolved external reference can't parse-and-warn; it has to parse-and-fail. For knot's posture (single team, no external schema federation) this is fine; flagging.
- **ER conflicts and splits surface by default.** A team running ER and getting "12 conflicts surfaced for review" has to actually review them. Auto-merge / auto-split is opt-in per impl. Stability by default; honest stewardship cost.
- **No graceful degradation mode.** A team that wants "if DQ fails, publish anyway with a warning" has to mark the relevant checks as `Severity.WARNING` deliberately. There's no "degrade gracefully on transient errors" knob. Probably right; flagging.

??? details "Deep-dive: failure shapes across surfaces"

    Spec parsing (per [`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md)):

    ```python
    class SpecBase(BaseModel):
        model_config = ConfigDict(
            extra="forbid",            # unknown fields raise at parse
            populate_by_name=True,
            ser_json_bytes="base64",
            ser_json_inf_nan="strings",
            validate_assignment=True,
            ...
        )
    ```

    Reference resolution (per [`design/staging/spec-model.md`](../../../design/staging/spec-model.md) §"Persistence boundary"):

    > Two-pass parse. First pass: build all entities (classes, slots, types) by name. Second pass: resolve every reference field to the real object. Reference resolution failures (e.g., `slot.range` names a class that doesn't exist) surface during the rehydration pass.

    Registration (per [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md)):

    1. DataContext refs validated against the current spec.
    2. Dangling refs → registration error.
    3. Cross-check: config-referenced spec entities ⊆ DataContext-reachable slots. Mismatch → compile error.
    4. SDK pin captured at registration; on every spec republish, re-validate every registered impl. Incompatible registrations are flagged "needs new registration"; publish gate refuses workflows using broken impls.

    QueryExecutor errors (per [`design/staging/query-executor.md`](../../../design/staging/query-executor.md) §"Errors raise; never swallow"):

    - Connection failures, syntax errors, dialect mismatches → raise typed `QueryError`.
    - Timeouts → raise `QueryTimeoutError(timeout_seconds)`.
    - Backend-specific errors → raise wrapped `BackendError(backend=<dialect>, detail=<str>)`.
    - Materialization-side workflow failures → `MaterializationStatus.state == "failed"` with `error` populated.

    Validation SQL shape (per [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) §"Validation SQL shape"):

    ```sql
    SELECT
        rule_id,          -- VARCHAR
        class_name,       -- VARCHAR
        slot_name,        -- VARCHAR
        offending_pk,     -- VARCHAR
        detail            -- VARCHAR
    FROM ...
    ```

    Empty result = pass. Built-in DQ checks emit failures in the same shape with a `severity` column added. Severity-ERROR blocks the publish gate; `pipeline_runs.status = failed`.

    ER post-processing default (per [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md)):

    > Both conflict and split policies follow the same posture: stability by default; impls opt in to auto-applying. The reasoning is symmetric — both are cases where ER changed its mind about the cluster shape, and the question is whether knot trusts ER's revised opinion enough to silently adjust canonical_ids.

### Cross-links

- [`design/goals.md`](../../../design/goals.md) §"Goals" #10 — fail loud.
- [`design/staging/spec-versioning.md`](../../../design/staging/spec-versioning.md) — `extra="forbid"` Pydantic Config.
- [`design/staging/query-executor.md`](../../../design/staging/query-executor.md) §"Errors raise; never swallow".
- [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) §"Validation SQL shape".
- [`design/staging/er-and-storage.md`](../../../design/staging/er-and-storage.md) §"Knot's post-processing".

---

## Open tensions on this page

- **Built-in DQ list is opinionated.** The bundle ships with a fixed set of check types. New types require knot-core changes; until then, teams write `DqRunner`s. If the team's domain commonly needs a check type that isn't in the bundle, they end up with a custom impl that everyone re-implements. The architectural fix is "promote to bundle"; the operational fix is "share helpers." Flagging because the cost is real even though the seam is right.
- **Snapshot-rebuild as the materialization default trades simplicity for cost at scale.** A multi-class graph rebuild that runs every publish run is expensive on large graphs. The architecture supports incremental (knot has the structural information dbt doesn't); incremental impls are the team's choice. Flagging because the default cost is real for any team with non-trivial scale.
- **No knot-shipped alerting.** The team has to wire alerting through the orchestrator or a `Notifier` impl. Probably right (matches single-team posture, doesn't tie knot to one alerting backend), but a team expecting "DQ failure alert in Slack" out of the box will not get it.
