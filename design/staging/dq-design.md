# Data quality — built-in checks + custom DqRunner

**Status:** staging — captured for review, not yet integrated into authoritative docs.

Two-layer DQ. knot ships a bundle of common check types out-of-the-box (configurable, opt-out per check); teams supplement or replace via bound `DqRunner` impls. **Both layers run via `QueryReader`** (per `query-executor.md`) — knot generates the SQL, the executor runs it, failures surface uniformly.

## Why this shape

The two-layer model dissolves the "knot reinvents the wheel" concern:

- **knot doesn't run SQL itself.** It generates SQL (or fragments) and dispatches via the bound `QueryReader`. Same pattern as everything else in knot.
- **knot ships logical defaults.** Out of the box, deployments get freshness, drift, cluster-size outlier detection, etc., without writing any DQ code. Hit the ground running.
- **Custom DqRunners remain available.** For domain-specific checks (anomaly detection on the graph, ML-based drift, business rules), the bound DI pattern is right there. Composable with built-ins.
- **Built-ins are opt-out.** Any deployment that wants to fully replace a built-in with a custom impl can just disable the built-in.

## Layer 1: built-in DQ

A bundle of typed Pydantic check definitions shipped with knot. Each:

- Defines its own configuration shape.
- Knows how to emit its own SQL via knot's SQL generator (or how to compose with knot-emitted SQL fragments).
- Surfaces failures in the uniform shape `(rule_id, class_name, slot_name, offending_pk, detail, severity)`.

### Initial set of built-in checks

| Check | What it detects | Mechanic |
|---|---|---|
| **Freshness** | Entities not updated within a threshold | `SELECT canonical_id WHERE max(asserted_at) < now() - interval` |
| **Cluster size outlier** | ER over-merge: clusters with anomalously many sources | `GROUP BY canonical_id HAVING COUNT(DISTINCT source) > z_score_threshold * stddev_dev` |
| **Degree outlier** | Entities with anomalously high relation count | aggregation over reverse relations + percentile threshold |
| **Cross-source agreement** | Sources disagreeing on a slot value beyond threshold | `COUNT(DISTINCT value) per (canonical_id, slot)` + threshold |
| **Cycle detection** | Cycles in slot ranges declared acyclic | `WITH RECURSIVE` traversal over the slot's edges |
| **Null rate trend** | Slot null rate trending up across recent runs | per-run aggregate + slope threshold |
| **Validation failure rate trend** | Constraint failure rate trending up | per-run aggregate + slope threshold |
| **Source coverage drop** | A source's row count drops anomalously between runs | row count delta + threshold |

This is an initial set; more can be added without architectural change — each is just another typed check class knot's SQL generator handles.

### Pydantic shape

```python
class Severity(Enum):
    ERROR = "error"           # blocks publish gate; pipeline_run.status = failed
    WARNING = "warning"       # recorded; doesn't block

class FreshnessCheck(BaseModel):
    enabled: bool = True
    default_threshold_days: int = 30
    per_class: dict[OntologyClass, int] = {}            # real OntologyClass refs as keys
    severity: Severity = Severity.WARNING

class ClusterSizeOutlierCheck(BaseModel):
    enabled: bool = True
    z_score_threshold: float = 3.0
    minimum_cluster_size: int = 5
    severity: Severity = Severity.ERROR

class CycleDetectionCheck(BaseModel):
    enabled: bool = True
    slots: list[Slot] = []                               # which acyclic-required slots to check
    severity: Severity = Severity.ERROR

class CrossSourceAgreementCheck(BaseModel):
    enabled: bool = True
    max_distinct_values_per_slot: int = 3
    per_slot: dict[Slot, int] = {}                       # per-slot overrides
    severity: Severity = Severity.WARNING

# ... etc.

class BuiltinDQConfig(BaseModel):
    freshness: FreshnessCheck = FreshnessCheck()
    cluster_size: ClusterSizeOutlierCheck = ClusterSizeOutlierCheck()
    degree_outlier: DegreeOutlierCheck = DegreeOutlierCheck()
    cross_source_agreement: CrossSourceAgreementCheck = CrossSourceAgreementCheck()
    cycle_detection: CycleDetectionCheck = CycleDetectionCheck()
    null_rate_trend: NullRateTrendCheck = NullRateTrendCheck()
    validation_failure_rate_trend: ValidationFailureRateTrendCheck = ValidationFailureRateTrendCheck()
    source_coverage_drop: SourceCoverageDropCheck = SourceCoverageDropCheck()
```

`BuiltinDQConfig` is a deployment-level configuration object stored in postgres-control, **runtime-editable** (no redeploy). Real Pydantic refs to `OntologyClass` and `Slot` (per `spec-model.md` § "References") so impact analysis walks them naturally.

### How they run

Each enabled built-in check participates in the validate stage (per `pipeline-stages.md`):

1. knot's SQL generator emits the check's SQL given the spec + the check's config.
2. knot dispatches via `QueryReader.execute(QueryRequest(sql=..., dialect=..., invoking_run_id=..., invoking_node_name=...))`.
3. The Arrow result rows are interpreted as failure records: each row is one offender in the uniform `(rule_id, class_name, slot_name, offending_pk, detail, severity)` shape.
4. Failures aggregated into the run's validation report; recorded in `pipeline_runs` and queryable via API.

Same dispatch pattern as the structural validation SQL emitted from spec slot constraints (year range, required, etc., per `sql-generator.md` SQL5). Built-in DQ checks are just additional generated SQL with their own check definitions.

## Layer 2: custom DqRunner

Bound impl, same DI pattern as ER and Materialization (per `di-input-contract.md`).

```python
class DqRunner(ProtocolBase):
    """Custom DQ check. Returns failures in the uniform shape."""

    candidates: ClassVar[DataContext[...]] = ...        # what data the impl reads

    class Config(BaseModel):
        # impl-specific knobs — whatever this DQ check needs
        ...

    def check(self, ctx, candidates) -> list[DqFailure]:
        ...

class DqFailure(BaseModel):
    rule_id: str
    class_name: str
    slot_name: str | None = None        # None for class-level checks
    offending_pk: str
    detail: str
    severity: Severity
```

The impl can:

- Read its declared DataContexts (knot fulfills via the QueryReader; rows arrive at runtime).
- Use the `QueryReader` itself for additional ad-hoc queries (the bound impl receives one in its context).
- Run anything in-process — ML inference, statistical analysis, anomaly detection — using the rows it received.
- Return a list of typed `DqFailure` records.

Knot stores the failures in the same shape as built-ins, so they aggregate uniformly.

## Composition

Built-ins and custom DqRunners run sequentially during the validate stage. Their failures are unified into one report. Each carries its own `rule_id` so consumers can route alerts per rule.

```yaml
builtin_dq:
  freshness:
    enabled: true
    default_threshold_days: 30
    per_class:
      Movie: 90
      Person: 365
    severity: warning
  cluster_size:
    enabled: true
    z_score_threshold: 3.0
    severity: error
  cycle_detection:
    enabled: true
    slots: [Movie.prequel]
    severity: error
  cross_source_agreement:
    enabled: false                       # disabled — handled by a custom impl below

dq_runner_impls:
  - movie_release_date_consistency       # bound impl: domain-specific
  - imdb_tmdb_runtime_diff_outlier       # bound impl: cross-source numeric outlier
```

Built-ins handle the obvious; custom impls handle domain-specific. A team can disable specific built-ins to take over with a richer custom impl.

## Failure handling and alerting

Failures surface in the uniform shape. knot records them in postgres alongside the `pipeline_run`. Visibility via the API (queryable, paginated, filterable by rule / severity / class / time).

**knot does not handle alerting itself.** Two paths:

1. **Orchestrator-native.** knot reports stage results (succeeded / failed / partial-with-warnings) to the orchestrator via its run handle. Maestro / Airflow / Argo / Toy each have native alerting (Slack, email, PagerDuty, custom hooks). For most production deployments this is the right answer — the team's existing on-call story already wires through the orchestrator. **Default path.**
2. **Bound notifier impl.** If a deployment wants knot-aware alerting (severity-routing per rule, per-class thresholds, custom destinations), bind a `Notifier` impl. Same DI pattern. Receives failures + context; sends notifications. **Optional, opt-in.**

knot's responsibility ends at "record + report stage outcome to the orchestrator + expose failures via API." Routing to humans is a downstream concern.

## Severity

- **ERROR** — blocks the publish gate; downstream materialization waits or fails per orchestrator policy. `pipeline_runs.status = failed`.
- **WARNING** — recorded; downstream proceeds. Visible in API; routable via the orchestrator's or notifier's filtering.

Built-in checks ship with default severities (cluster-size = ERROR, freshness = WARNING, etc.); user can override per-check via config. Custom DqRunners declare severity per emitted failure.

## Graph algorithms in DQ

Most checks reduce to SQL aggregations + thresholds. A few useful patterns need a real graph engine:

- **PageRank / centrality** — finding "central but mismatched" entities.
- **Community detection** — finding tight clusters that should be ER-merged but aren't (or vice versa).
- **Strong / weak connectivity** — for relations expected to form connected subgraphs.

These are out of scope for the built-in bundle (no shipped graph runtime). A bound DqRunner impl can use whatever graph library it wants (NetworkX, GraphFrames, igraph) — the impl reads its DataContexts, runs the algorithm in-process or via its own dispatcher, returns failures.

Graph-engine-based checks are not in the built-in bundle — knot ships no graph runtime. Deployments that need them write a custom `DqRunner` with whatever graph library suits.

## Cross-references

- `query-executor.md` — `QueryReader` executes all DQ SQL; built-ins and custom DqRunners both rely on it.
- `pipeline-stages.md` — the validate stage where DQ runs.
- `spec-model.md` — `Constraint` nodes (per-row + cross-row invariants from the spec) run alongside built-in DQ; same uniform failure shape.
- `di-input-contract.md` — the bound impl pattern DqRunner follows.
- `sql-generator.md` — SQL5 (uniform validation SQL shape).
