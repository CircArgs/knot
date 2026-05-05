# 6. Execution delegation

One commitment — 12 — is large enough to need its own page. It establishes the seam through which **all** lake-side execution flows: knot-internal machinery (built-in DQ, structural validation, materialized DataContext views, merge SQL) and team-bound stages alike.

The split into four orthogonal protocols, rather than one fat ABC, is a deliberate response to the DataJunction `BaseQueryServiceClient` precedent.

---

## Commitment 12 — Knot delegates execution. Four orthogonal QueryExecutor protocols.

Lake-side execution goes through bound impls of `QueryReader` (sync SELECT → Arrow), `Materializer` (async INSERT INTO target SELECT → handle + status), `Introspector` (column metadata), `ViewManager` (DDL). Typed Pydantic inputs throughout. Errors raise. One impl per protocol per deployment; multi-backend handled internally if needed. No HTTP boundary.

Implication: knot core is dialect-agnostic; bound impls translate. Knot's own machinery (built-in DQ checks, structural validation, materialized views fulfilling DataContexts) runs through the same QueryExecutor. Nothing reinvented.

### Rationale

This commitment is the engineering contract that makes [Commitment 1 (knot is a compiler)](1-foundation.md) tractable. The compile-not-execute discipline is the architectural posture; the QueryExecutor protocols are how lake-side work actually happens.

Three load-bearing properties:

1. **Knot's own machinery uses the same seam.** Built-in DQ checks, structural validation queries, materialized views that fulfill bound impls' DataContexts — all dispatch through `QueryReader` / `Materializer` / `ViewManager`. This is what makes "no internal SQL engine" real. Knot has nothing of its own; everything goes through the boundary.
2. **Orthogonal protocols, not one fat ABC.** DataJunction ships one `BaseQueryServiceClient` ABC with ~10 methods; subclasses cherry-pick which to implement; defaults raise `NotImplementedError`. Most bindings only implement 30% of the surface. Knot's split — separate protocols for read, materialize, introspect, view DDL — has each binding implement only what it provides. The split isn't decorative; it's the difference between "this impl supports this concern" and "this impl raises on most methods."
3. **Single-team posture rules out HTTP federation.** DataJunction's `BaseQueryServiceClient` is HTTP-mediated for Netflix's internal scheduler integration. Knot is single-team; the executor binds in-process and reaches Spark/Trino/DuckDB directly. No HTTP boundary, no separate microservice.

The DataJunction error-handling comparison is sharp ([`design/staging/query-executor.md`](../../../design/staging/query-executor.md) §"Errors raise; never swallow"):

> DJ's legacy materialize methods log-and-swallow non-2xx responses, returning empty handles — the only signal is empty `urls`. The newer pre-agg path raises with typed errors. We adopt the latter.

This aligns with [Commitment 16 in 4-quality-and-publish.md](4-quality-and-publish.md): failure modes are loud.

### Comparator anchor

| System | Execution interface | Read result shape | Multi-backend |
|---|---|---|---|
| **DataJunction** | Single fat `BaseQueryServiceClient` ABC (~10 methods) | List of dicts (JSON-over-HTTP legacy) | HTTP-mediated DJQS dispatches internally |
| **Trino client / pyhive** | Cursor-based read; no materialize concept | Tuples / DataFrames | Per-client per-backend |
| **dbt adapters** | Per-warehouse adapter (`dbt-snowflake`, `dbt-spark`) | Native cursor | One adapter per warehouse; framework handles dispatch |
| **Apache Beam I/O connectors** | `Read` / `Write` PTransforms per source/sink | PCollection | Per-connector |
| **knot** | Four orthogonal protocols (`QueryReader`, `Materializer`, `Introspector`, `ViewManager`) | Arrow record batches | One impl per protocol per deployment; routes internally |

DataJunction's `BaseQueryServiceClient` is the closest *anti-pattern* — informed knot's design directly. dbt's adapter pattern is the closest *positive comparator*: per-warehouse adapter, framework handles dispatch. Knot's choice of Arrow over list-of-dicts is the deliberate greenfield-vs-legacy decision.

### Tradeoffs / honest costs

- **Four protocols means the team binds four things.** Even a simple deployment that wants Spark for batch + Trino for interactive ends up implementing read + materialize + introspect + view DDL. The mitigation: a deployment can implement all four in one binding class that fans out internally. The cost is upfront wiring; the benefit is each method has clear semantics.
- **Arrow is the read result shape.** Cross-engine, cross-language interop (pyarrow, polars, pandas, duckdb-arrow) — but a deployment without arrow tooling has to either install it or build out from raw rows. Modern data ecosystems have arrow available; flagging because it's a real dependency.
- **Async materialization via handle + status.** The polling pattern is correct (matches Spark / Trino reality) but the impl has to handle the polling loop. A team that wants webhook-style completion can wire it up via the impl's `status()` returning state populated by callbacks; the architecture allows it. Not the default.
- **No HTTP boundary.** Single-team posture rules out the DataJunction-style HTTP-mediated executor. A team that ever needed HTTP federation between knot and the executor (e.g., for security boundary reasons) would have to add it; the architecture doesn't ship it.
- **Identity / credentials are out of band.** The binding is constructed with whatever auth it needs at deployment time. Knot does not propagate end-user identity into the backend; the executor runs as its own principal. Simpler audit (knot's `pipeline_runs` + the backend's own logs), but a team that wanted "this query ran as user X" must wire it themselves.

??? details "Deep-dive: protocol shapes and DJ-vs-knot pattern table"

    From [`design/staging/query-executor.md`](../../../design/staging/query-executor.md):

    The four protocols:

    ```python
    class QueryReader(ABC):
        """Synchronous SELECT execution. Returns Arrow."""
        @abstractmethod
        def execute(self, request: QueryRequest) -> ArrowResult: ...

    class Materializer(ABC):
        """Async INSERT INTO target SELECT (or CREATE TABLE AS).
        Returns a handle; completion via status polling or callback."""
        @abstractmethod
        def materialize(self, request: MaterializeRequest) -> MaterializationHandle: ...
        @abstractmethod
        def status(self, handle: MaterializationHandle) -> MaterializationStatus: ...

    class Introspector(ABC):
        """Column metadata for a lake table or view."""
        @abstractmethod
        def get_columns(self, table_ref: TableRef) -> list[ColumnInfo]: ...

    class ViewManager(ABC):
        """Lake DDL — CREATE OR REPLACE VIEW, DROP VIEW. Synchronous."""
        @abstractmethod
        def create_view(self, view: ViewSpec) -> None: ...
        @abstractmethod
        def drop_view(self, view_name: str) -> None: ...
    ```

    Typed Pydantic inputs:

    ```python
    class Dialect(Enum):
        SPARK = "spark"
        TRINO = "trino"
        DUCKDB = "duckdb"
        POSTGRES = "postgres"

    class QueryRequest(BaseModel):
        sql: str                                  # knot-emitted, dialect-specific
        dialect: Dialect
        catalog: str | None = None
        schema_: str | None = Field(None, alias="schema")
        invoking_run_id: UUID
        invoking_node_name: str
        timeout_seconds: int = 300

    class MaterializeRequest(BaseModel):
        sql: str
        dialect: Dialect
        target: TableRef
        invoking_run_id: UUID
        invoking_node_name: str

    class ViewSpec(BaseModel):
        view_name: str
        sql: str
        dialect: Dialect
    ```

    Read returns Arrow:

    ```python
    class ArrowResult(BaseModel):
        batches: Iterable[pa.RecordBatch]
        schema: pa.Schema
        row_count_estimate: int | None = None
    ```

    Materialization handle + status:

    ```python
    class MaterializationHandle(BaseModel):
        handle_id: str
        submitted_at: datetime
        target: TableRef
        backend_run_url: str | None = None

    class MaterializationStatus(BaseModel):
        state: Literal["queued", "running", "succeeded", "failed", "unknown"]
        started_at: datetime | None = None
        finished_at: datetime | None = None
        rows_written: int | None = None
        error: str | None = None
    ```

    Errors raise (per [`design/staging/query-executor.md`](../../../design/staging/query-executor.md) §"Errors raise; never swallow"):

    - Connection failures, syntax errors, dialect mismatches → typed `QueryError`.
    - Timeouts → `QueryTimeoutError(timeout_seconds)`.
    - Backend-specific errors → wrapped `BackendError(backend=<dialect>, detail=<str>)`.
    - Materialization-side workflow failures → `MaterializationStatus.state == "failed"` with `error` populated.

    DJ patterns adopted vs avoided ([`design/staging/query-executor.md`](../../../design/staging/query-executor.md) §"DJ patterns we adopted vs avoided"):

    | Pattern | DJ does | knot does |
    |---|---|---|
    | Single fat ABC vs orthogonal protocols | Single ABC (~10 methods, 30% impl rate) | Orthogonal protocols, bind per concern |
    | Input shape | Typed Pydantic | Typed Pydantic ✓ |
    | Read result shape | List of dicts (JSON-over-HTTP legacy) | Arrow record batches |
    | Error handling | Legacy: log-and-swallow; newer: raise | Raise, always (newer pattern) |
    | Multi-backend | HTTP-mediated DJQS dispatches internally | One impl per deployment, routes internally; no HTTP |
    | Read/write asymmetry | Same client, separate methods | Same — separate protocols, distinct shapes |
    | Materialization completion | Async via `POST /availability/` callback | Async via handle + `status()` polling (or callback if the impl wires it up) |
    | Identity propagation | Executor runs as own principal | Same |
    | Subclass discovery | `__subclasses__()` (fragile per survey) | Explicit DI binding / registration |

### How knot uses its own protocols

This is what makes "no internal SQL engine" load-bearing. Every place inside knot that needs lake-side work goes through the same seam:

| Knot-internal use | Protocol used |
|---|---|
| Built-in DQ checks (freshness, cluster-size outlier, etc.) emit SQL → execute | `QueryReader` |
| Structural validation queries from spec constraints (cardinality, uniqueness, FK) | `QueryReader` |
| Materialized views fulfilling bound impls' DataContexts | `Materializer` + `ViewManager` |
| Merge stage's canonical_id linking + trust-state snapshot | `Materializer` |
| Translator backward-chain queries | `QueryReader` |
| Trust-resolved CTE rewriting | knot's runtime; final SQL goes through `QueryReader` |

The bound team-side stages (ER, custom DQ, materialization) also use the QueryExecutor protocols to fulfill their declared DataContexts. From [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided":

> A DataContext is the contract (what the impl wants). When knot fulfills it for a run, knot materializes a view (or temp table) in the lake at a stable name keyed to run + impl + DataContext, and hands the impl the name. The impl reads from that name using its own engine — no knot-side query execution; no SQL strings handed across the seam; no in-memory bytes (pyarrow / DataFrame) shipped from knot. Knot's responsibility is DDL (create view, drop view) and lifecycle.

### Cross-links

- [`design/staging/query-executor.md`](../../../design/staging/query-executor.md) — full protocol design, DJ comparison.
- [`design/staging/sql-generation.md`](../../../design/staging/sql-generation.md) — what knot emits before dispatch.
- [`design/staging/dq-design.md`](../../../design/staging/dq-design.md) — built-in DQ + custom DqRunner both use `QueryReader`.
- [`design/staging/pipeline-stages.md`](../../../design/staging/pipeline-stages.md) — materialization stage uses `Materializer` + `ViewManager`.
- [`design/staging/di-input-contract.md`](../../../design/staging/di-input-contract.md) §"Decided" — DataContext fulfillment via Materializer + ViewManager.
- [`design/knot-as-compiler.md`](../../../design/knot-as-compiler.md) — the compile-not-execute discipline this commitment serves.
- [`design/core-design.md`](../../../design/core-design.md) commitment 12.

---

## Open tensions on this page

- **Streaming SQL is out of scope.** Reads are request-response; long-running materializations use the handle + status pattern. True streaming SQL (continuous queries, pub/sub on lake updates) is not part of this protocol set. A team with a streaming use case has to handle it outside knot's QueryExecutor — probably via a bound materialization impl that consumes from a stream and lands rows in the lake. Flagging because the architecture rules this out by shape.
- **DataContext fulfillment as a separate concern.** The DataContext fulfillment (per [Commitment 4 in 2-the-di-seam.md](2-the-di-seam.md)) sits on top of the four QueryExecutor protocols — knot uses `Materializer` + `ViewManager` to materialize a view at a stable name and hands the impl the name. The two-layer model (low-level QueryExecutor + higher-level DataContext) is intentional but a team has to understand both layers to debug a fulfillment issue. Surfacing in deep-dive docs is enough; flagging.
- **One impl per protocol per deployment.** A team with a hard requirement to run two QueryReaders simultaneously (e.g., Spark for some reads, Trino for others) has to fan out internally in the bound impl. The architecture supports this; the impl handles the routing. The cost is real for teams with non-trivial routing logic; the alternative (knot-managed multi-binding) was deliberately rejected for posture reasons.
