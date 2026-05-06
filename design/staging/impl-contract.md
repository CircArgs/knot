# Impl contract — what knot owns vs what impls own

**Status:** decided. Captures the seam contract that took too many rounds to land cleanly. Read this before adding any new impl, protocol, or seam — it answers questions that must NOT be re-litigated.

---

## What knot owns

- **The I/O contract tables at the lake**: `per_source_facts/<Class>/source=<X>`, `resolved_facts/<Class>`, `entity_bindings/<Class>` (SCD2), `canonical_id_lineage`. These are the spec-driven shapes knot's compiler reasons about.
- **The control plane in postgres**: `compiled_workflows`, `pipeline_runs`, `bound_impls`, `impl_revision`, `impl_config`, `_user_corrections`, `_user_er_decisions`. Metadata only.
- **DataContext fulfillment**: when an impl declares a `DataContext`, knot's compiler walks it via single-dispatch over the typed expression tree, generates SQL via `sql_gen`, and hands the impl whatever the bound `QueryReader` returns.
- **Result metadata storage**: typed `ERResult` / `MaterializeResult` / `DqResult` / `TranslateResult` etc. are stored in postgres-control and threaded into `ctx` for downstream stages to reference.
- **The compile hash**: content-addressed identity for replay / audit walk-back.

## What knot does NOT own

- **In-memory data shape**. The bound `QueryReader` decides the handoff format. Arrow Table for DuckDB-on-fs, Iceberg snapshot ref for S3+Trino, view name for Spark — the protocol abstracts the backend. Do not hardcode `pyarrow.Table` in protocol signatures.
- **Filesystem layout for impl outputs**. Impls write through their bound `Materializer`. Where the bytes live, what compression, what partitioning — impl-side. Knot only sees the URI/snapshot-id the impl returns.
- **Python deps / runtime image**. New library = redeploy the runtime. See `impl-dependencies.md`.
- **Auth / sandboxing / multi-tenant defenses**. Single-team posture per `goals.md`. Trusted authors. `exec(impl_source)` into a fresh namespace; no isolation.

## Result objects are metadata only

```python
class ERResult(SpecBase):
    output_uri: str        # opaque — could be parquet path, Iceberg snapshot ref, etc.
    column_map: ScoreColumnMap
    # NO embedded Arrow Table. NO embedded data. Just metadata.
```

Same for `MaterializeResult`, `DqResult`, `TranslateResult`, `ConstraintResult`, `DerivationResult`. Knot stores the metadata; downstream stages reference it via `ctx.upstream_results`.

## ctx contract

`ctx` (the runtime context object the orchestrator passes to bound impls) is a typed Pydantic model. Exposed fields:

- `ctx.config` — the impl's hydrated Config snapshot
- `ctx.run_id` — current pipeline_runs row id
- `ctx.compile_hash` — content-addressed identity of the WorkflowSpec being executed
- `ctx.binding_info` — `(stage, class_name, impl_name, revision)` of the bound impl
- `ctx.upstream_results` — `dict[stage_key, Result]` of prior stage outputs in this run; impls reference upstream parquet/Iceberg via `upstream_results[...].output_uri`
- `ctx.query_reader` — the bound QueryReader instance for ad-hoc reads beyond what DataContexts pre-materialized
- `ctx.materializer` — the bound Materializer for writes

**Not exposed**: `ctx.lake_dir`, raw filesystem paths, postgres connection — impls don't reach through.

## What this means for impl writers

1. Declare `Config: ClassVar[type] = MyConfig` and `DataContext` class attributes. Knot fulfills them.
2. Method body receives `ctx` plus DataContext views (whatever shape the bound QueryReader returns).
3. Write outputs through `ctx.materializer.materialize(query_or_table, target_uri)` — never raw filesystem writes.
4. Return a typed `Result` with `output_uri` + `column_map`. Knot stores the metadata.
5. Reference upstream stages via `ctx.upstream_results` — which Result they produced, where its data is.

## Cross-references

- `core-design.md` § 4 (universal DI seam), § 12 (delegate execution)
- `staging/datacontext-config-binding.md`
- `staging/multi-class-datacontexts.md`
- `staging/multi-valued-semantics.md`
- `staging/protocol-result-shapes.md`
- `staging/query-executor.md`
- `staging/impl-dependencies.md`
