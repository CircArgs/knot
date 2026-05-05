# SQL generation

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How knot turns spec entities (derivations, constraints, validation rules, ER signal slots, DataContext bodies, translator queries) into SQL. **knot emits SQL; the bound `QueryReader` / `Materializer` / `ViewManager` impls execute it** (per `query-executor.md`).

---

## Targets

**Spark and Trino only.** knot is not a multi-backend SQL platform.

| Engine | Primary use |
|---|---|
| **Spark** | Batch / Iceberg writes / large-scale processing |
| **Trino** | Interactive querying / federation / fast SQL access |

No other dialects on the roadmap. If a deployment binds a `QueryReader` for DuckDB / Postgres / etc., it's the binding's responsibility to translate (or restrict knot to a compatible subset).

---

## AST tooling

**Default: sqlglot.** Supports both Spark and Trino dialects natively; AST + emitters; used by SQLMesh and Splink. Active development.

knot uses sqlglot's AST and per-dialect emitters directly. It does NOT lean on sqlglot's multi-dialect transpilation feature and makes no claims about correctness across other dialects.

### Fragments IR

sqlglot's AST does not natively cover all constructs required for both targets: `MAP`, `PIVOT`, `COLLECT_LIST`, `MAP_AGG`. These are handled via an IR layer (`knot.sql_gen.fragments`) with a **per-dialect emit dispatch table**.

Rules:

- **No raw SQL strings outside `fragments`.** Any dialect-specific construct that cannot be expressed as a standard sqlglot AST node must go through a `fragments` IR node with a registered emitter per dialect. Raw SQL string assembly anywhere else in `sql_gen/` is forbidden.
- The dispatch table maps `(IRNode type, dialect)` → sqlglot AST subtree. Emitters for IR nodes that map cleanly to sqlglot builtins on both dialects can be trivial pass-throughs; the abstraction layer exists to contain the divergence cases.

---

## Inputs and outputs

**Inputs:**

- A `DerivationExpr` (or any expression tree per `derivation-and-constraints.md`) from the spec.
- A `TableRefMap` (see below) — parametric table references for the `resolved_facts`-shaped tables the rule reads. knot does not hardcode storage layout.
- Target dialect: `"spark"` or `"trino"`.

**Outputs:**

- A sqlglot AST (the primary output; dialect-specific SQL string emitted from it).
- A SQL string (`SELECT …`) for callers that want the final string directly.
- `KnotSQL.trust_cte_names: list[str]` — names of any trust-resolution CTEs emitted, for knot's runtime to rewrite pre-execution (see "Trust-resolution placeholder" below).

---

## TableRefMap

Parametric table reference lookup. Pure data; JSONB-serializable; no callbacks.

```python
class TableRef(BaseModel):
    """Fully-qualified reference to a resolved_facts-shaped table or view."""
    catalog: str | None = None
    schema_: str | None = Field(None, alias="schema")
    table: str

class TableRefMap(BaseModel):
    """Maps OntologyClass references to their backing TableRef at SQL-gen time."""
    table_for: dict[OntologyClass, TableRef]   # keyed by real OntologyClass reference
    default: TableRef | None = None            # used if class not in table_for

    def get(self, cls: OntologyClass) -> TableRef:
        if cls in self.table_for:
            return self.table_for[cls]
        if self.default is not None:
            return self.default
        raise UnknownClassRef(cls)
```

`UnknownClassRef` is raised (not silently substituted) when a class has no registered ref and no default is set. Callers that want a blanket default supply it explicitly.

`OntologyClass` is `Hashable` (Pydantic v2 frozen `BaseModel` semantics; identity is the `name` field — uniqueness enforced by the spec parser). Map serialization at the persistence boundary flattens to `dict[str, TableRef]` keyed by class name; the in-memory shape uses real refs.

---

## Expressivity bound

knot emits **one SQL statement** per rule. The statement can be as rich as Spark / Trino allow: subqueries, joins, aggregations, window functions. "One statement" is not "one basic SELECT".

**Recursive CTEs: REJECTED.** Aligns with `derivation-and-constraints.md`. Rules whose semantics require recursive CTE must be rejected at compile time with `UnsupportedDerivationError`.

Patterns requiring incremental view maintenance (negation under aggregation across runs, DELETE/INSERT propagation) are also out of scope; reject at compile time.

---

## Aggregation lowering

### `.collect()` — `AggCollect`

IR node: `AggCollect(operand, distinct, order_by)`.

| Dialect | Emit |
|---|---|
| Spark | `COLLECT_LIST(expr)` |
| Trino | `ARRAY_AGG(expr FILTER (WHERE expr IS NOT NULL))` |

**NULL asymmetry:** Spark's `COLLECT_LIST` silently drops nulls. Trino's `ARRAY_AGG` retains nulls by default; the `FILTER (WHERE ... IS NOT NULL)` clause is added explicitly to match Spark semantics. Documented behavior, not papered over. Callers that need to preserve nulls in Trino must use a different construct (out of scope).

### `group_by_source` map output — `AggMapBySource`

IR node: `AggMapBySource(source_expr, value_expr)`. Output type is always `MAP<STRING, ARRAY<T>>` — de-dup wrapping is applied at the SDK expression level, not inside the IR node.

| Dialect | Emit |
|---|---|
| Spark | `map_from_entries(collect_list(struct(source_expr, value_expr)))` |
| Trino | `map_agg(source_expr, value_expr)` |

`map_agg` in Trino raises on duplicate keys; upstream de-dup wrapping (handled at the SDK expression layer before lowering) is required.

---

## Pivot operator

**Always lower to explicit CASE-aggregation** on both dialects.

```sql
-- Pattern emitted for each explicit pivot key k:
MAX(CASE WHEN source = 'k' THEN value END) AS value_k
```

- `exp.Pivot` is NOT used. sqlglot's `Pivot` node behavior diverges across dialects in ways that are hard to control; CASE-aggregation is fully portable within our target set.
- **Caller is required to supply an explicit pivot-key list.** knot does not infer or enumerate pivot keys from data. Empty list raises `InvalidPivotError` at expression-build time.

---

## Validation SQL shape

All structural-validation queries (and Constraint-derived queries; and built-in DQ checks per `dq-design.md`) emit a uniform SELECT shape:

```sql
SELECT
    rule_id,          -- VARCHAR: identifies which validation rule fired
    class_name,       -- VARCHAR: ontology class name
    slot_name,        -- VARCHAR: slot name (NULL for class-level rules)
    offending_pk,     -- VARCHAR: primary key of the offending row
    detail            -- VARCHAR: human-readable detail
FROM ...
```

**Empty result = pass.** Callers compose multiple validation queries via `UNION ALL` or run as separate checks. knot does NOT emit `ASSERT` statements or fail-on-violation logic; that is the consumer's enforcement concern (typically the validate stage; see `pipeline-stages.md`).

Three canonical templates knot emits:

| Template | Rule type |
|---|---|
| **Cardinality** | Required-slot nullability, multivalued count bounds |
| **Identifier uniqueness** | `UniqueKey` → `GROUP BY + HAVING COUNT > 1` |
| **Referential** | FK exists check — `LEFT JOIN + WHERE target IS NULL` |

`Constraint` nodes (per `spec-model.md`) and built-in DQ checks (per `dq-design.md`) follow the same emit shape: their bodies compile to a SELECT that returns offender rows in this 5-column shape.

---

## Trust-resolution CTE — per-policy reductions

Under `RESOLVED`-stance protocols (Materializer, Translator, ConstraintEvaluator, DerivationEvaluator), knot attaches a trust-resolution CTE per ontology class touched by the query. The CTE body emits one reduction expression per slot, selected by the slot's declared `ResolutionPolicy`. Under `DISAGREEMENT_AWARE` protocols (ERProtocol, DqRunner), no CTE is attached; bare slot references resolve directly against `per_source_facts/<Class>`.

Per-policy reduction expressions (copied from `staging/multi-valued-semantics.md` — do not re-derive):

| `ResolutionPolicy` | Reduction expression |
|---|---|
| `ARGMAX_TRUST` | `argmax(value, trust)` (or backend-specific `array_agg` + sort) |
| `MODE` | `mode() WITHIN GROUP (ORDER BY value)` (Postgres / Trino) |
| `WEIGHTED_VOTE` | `argmax(value, sum_of_trust_per_value)` over a sub-group-by |
| `MEDIAN_NUMERIC` | `percentile_cont(0.5) WITHIN GROUP (ORDER BY value)` |
| `LATEST_WATERMARK` | `argmax(value, asserted_at)` |
| `UNIQUE_OR_FAIL` | `CASE WHEN COUNT(DISTINCT value) > 1 THEN error('disagreement') ELSE first(value) END` (or equivalent) |

Example CTE body for a class with two slots of different policies:

```sql
WITH movie_resolved AS (
    SELECT
        canonical_id,
        argmax(year_value,  year_trust)  AS year,   -- ARGMAX_TRUST
        mode() WITHIN GROUP (ORDER BY genre_value) AS genre  -- MODE
    FROM resolved_facts_movie
    GROUP BY canonical_id
)
```

For the consumer-facing `Translator` lens, the trust-resolution CTE composes with the correction overlay (see "Lens-correction interaction" in `staging/multi-valued-semantics.md`). Pipeline impls (Materializer, ConstraintEvaluator, DerivationEvaluator) receive the resolved CTE without the overlay — replay stays deterministic at the pinned watermark.

## Trust-resolution placeholder (named-CTE seam)

For queries against multi-valued, trust-resolved slots, knot emits a named CTE:

```sql
WITH __trust_resolved__<ClassName> AS (
    SELECT pk, slot, value, source, asserted_at
    FROM <resolved_facts>
    -- ^ placeholder body; knot's runtime rewrites this before execution
)
SELECT ...
FROM __trust_resolved__<ClassName>
WHERE ...
```

**Contract:**

- CTE name format: `__trust_resolved__<ClassName>` (class name is `OntologyClass.name`).
- Placeholder body is valid SQL runnable as-is (against the unresolved `resolved_facts` view) so unit tests and golden snapshots work without a trust-resolution runtime in scope.
- `KnotSQL.trust_cte_names: list[str]` lists all trust CTE names emitted in a given query. knot's runtime enumerates this list and rewrites each CTE body via sqlglot AST manipulation before dispatching to `QueryReader`.

`from_source(...)` and `.all()` access modes (per `auto-generated-sdk.md` § "Multi-valued canonical access modes") query `resolved_facts` directly and do NOT emit trust CTEs.

---

## Config substitution and SQL emission

Literals substituted into compiled DataContext SQL may originate from Config snapshots. Substitution happens at compile — before SQL is emitted — so the SQL layer sees only concrete literals and resolved spec entity refs, not `ConfigRef` nodes. The SQL generator is transparent to the Config mechanism; it emits the same SQL patterns regardless of whether a literal arrived from a hand-coded constant or a substituted Config field. See `staging/datacontext-config-binding.md` for the substitution step.

## Testing posture

- **Unit tests** (every PR): AST shape for representative SDK expressions; SQL string output for fixed inputs. Fast, deterministic.
- **Golden-query snapshot tests** (every PR): representative SDK expressions → Spark and Trino SQL strings. Snapshots reviewed on PR; semantic equivalence confirmed against sqlglot's parsing where possible. Covers both emit paths for `AggCollect`, `AggMapBySource`, pivot lowering, and the trust-CTE shape.
- **Live integration tests:** deferred until knot is wired up end-to-end with bound `QueryReader` impls for Spark + Trino.

---

## Open

- **`RelationCount` heuristic.** Correlated subquery vs `LEFT JOIN + GROUP BY` — codegen-context-dependent. Defer to implementation; both are correct.
- **Window function emit.** Rank, row_number, dense_rank, etc. surface in the SDK (per `auto-generated-sdk.md`); SQL gen has no formal design yet. Tractable but not specified.
