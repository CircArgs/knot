# Query executor — protocols for lake reads, materializations, DDL, introspection

**Status:** staging — captured for review, not yet integrated into authoritative docs.

The bound DI protocol(s) knot uses for executing SQL against the lake. **knot generates SQL; the executor runs it.** This is the seam through which all knot-internal operations that need lake execution flow — built-in DQ, structural validation, materialization, ER data fetch fulfillment, etc.

Informed by DataJunction's `BaseQueryServiceClient` (per `legacy/datajunction-{di,materialization}-survey.md`); deliberately avoids DJ's pitfalls.

## Why split into multiple protocols

DJ ships one fat ABC (`BaseQueryServiceClient`) with ~10 methods covering read, write, materialize, schedule, view DDL, introspect. Subclasses cherry-pick which methods to implement; defaults raise `NotImplementedError`. Most bindings only implement 30% of the surface — code smell.

Knot splits into **orthogonal protocols**. Each binding implements only what it provides. Deployments compose multiple bindings if a single backend doesn't cover everything.

## The four protocols

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

Each binding picks which protocols it implements. A simple DuckDB binding might implement all four. A production deployment might bind a Spark Materializer + a Trino QueryReader + a single Introspector.

## Typed Pydantic inputs

All requests are typed Pydantic — no raw SQL strings + dict, no opaque kwargs. Carries enough context for the executor to log, route, and trace.

```python
class Dialect(Enum):
    SPARK = "spark"
    TRINO = "trino"
    DUCKDB = "duckdb"
    POSTGRES = "postgres"

class TableRef(BaseModel):
    catalog: str | None = None
    schema_: str | None = Field(None, alias="schema")
    table: str

class QueryRequest(BaseModel):
    sql: str                                  # knot-emitted, dialect-specific
    dialect: Dialect
    catalog: str | None = None
    schema_: str | None = Field(None, alias="schema")
    invoking_run_id: UUID                     # the pipeline_runs row that's calling
    invoking_node_name: str                   # which spec node is the consumer
    timeout_seconds: int = 300

class MaterializeRequest(BaseModel):
    sql: str                                  # INSERT INTO ... SELECT or CREATE TABLE AS
    dialect: Dialect
    target: TableRef                          # where rows land
    invoking_run_id: UUID
    invoking_node_name: str

class ViewSpec(BaseModel):
    view_name: str
    sql: str                                  # the SELECT body
    dialect: Dialect

class ColumnInfo(BaseModel):
    name: str
    sql_type: str                             # backend-native type string
    nullable: bool
```

## Returns

**Reads → Arrow.** Cross-engine, cross-language interop (pyarrow, polars, pandas, duckdb-arrow, etc.). Streaming-capable via Arrow record batches if the backend supports it. DJ's list-of-dicts is a JSON-over-HTTP legacy; we're greenfield.

```python
class ArrowResult(BaseModel):
    batches: Iterable[pa.RecordBatch]         # iterable for streaming; eager-realized for small results
    schema: pa.Schema
    row_count_estimate: int | None = None     # optional; not all backends provide
```

**Materializations → typed handles.** Async; status fetched separately.

```python
class MaterializationHandle(BaseModel):
    handle_id: str                            # opaque to knot; the impl's identity for this run
    submitted_at: datetime
    target: TableRef
    backend_run_url: str | None = None        # deep-link to backend UI (Spark / Trino / etc.)

class MaterializationStatus(BaseModel):
    state: Literal["queued", "running", "succeeded", "failed", "unknown"]
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows_written: int | None = None
    error: str | None = None
```

DDL and introspection have natural shapes (None / list[ColumnInfo]).

## Errors raise; never swallow

DJ's legacy materialize methods log-and-swallow non-2xx responses, returning empty handles — the only signal is empty `urls`. Survey explicitly flags this. The newer pre-agg path raises with typed errors. **We adopt the latter.**

- Connection failures, syntax errors, dialect mismatches → raise typed `QueryError`.
- Timeouts → raise `QueryTimeoutError(timeout_seconds)`.
- Backend-specific errors → raise wrapped `BackendError(backend=<dialect>, detail=<str>)`.
- Materialization-side workflow failures → `MaterializationStatus.state == "failed"` with `error` populated; the impl's `status()` method returns this; the call to `materialize()` itself only raises if the *submission* fails.

knot catches at the dispatch boundary, records the error in the `pipeline_runs` row, surfaces via API.

## Multi-backend handling

**One binding per protocol, per deployment.** The binding can fan out internally (e.g., a multi-engine impl that picks Spark vs Trino per query based on dialect or workload). knot dispatches to the bound impl; routing is internal to it.

DJ's HTTP-mediated DJQS exists for Netflix-internal scheduler integration. **knot is single-team; the executor binds in-process** and reaches Spark/Trino/DuckDB directly. No HTTP boundary, no separate microservice.

## Identity / credentials

Out of band. The binding is constructed with whatever auth it needs (env vars, config files, secret manager) at deployment time. **knot does not propagate end-user identity** into the backend; the executor runs as its own principal. Audit trail is via knot's `pipeline_runs` + `compiled_workflows` rows + the backend's own logs.

## What's NOT in scope here

- **DataContext fulfillment.** The DataContext mechanic (per `di-input-contract.md`) uses Materializer + ViewManager to expose materialized views to bound stage impls. The executor protocols are the lower layer; DataContext is the higher-level abstraction over them.
- **Materialization to non-lake targets.** Publishing to Neo4j, vector stores, etc. is handled by free-form materialization impls (per `pipeline-stages.md` § "Materialization" and `di-input-contract.md`). Those impls might use Materializer / ViewManager internally to stage data lake-side before pushing to the target, but the publishing logic itself is bound impl code, not part of QueryExecutor.
- **Source-side ingestion.** Out of scope per `source-layer-contract.md`.
- **Streaming / long-poll execution.** Reads are request-response; long-running materializations use the handle + status pattern. True streaming SQL is not part of this protocol set.

## DJ patterns we adopted vs avoided

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

## Cross-references

- `seam-contract-pattern.md` — the protocol+context shape this extends.
- `di-input-contract.md` — DataContext mechanism that uses Materializer + ViewManager at fulfillment.
- `pipeline-stages.md` — materialization stage uses Materializer + ViewManager for lake-side staging; free-form bound impls handle non-lake publish targets.
- `dq-design.md` — built-in DQ + custom DqRunner both use QueryReader.
- `legacy/datajunction-di-survey.md`, `legacy/datajunction-materialization-survey.md` — DJ source patterns informing this design.
