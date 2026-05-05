# Pipeline run artifacts

**Status:** staging — captured for review, not yet integrated into authoritative docs.

How knot tracks byproducts of pipeline runs that aren't part of the I/O contract but matter across runs.

## The pattern

A pipeline run can produce **artifacts** — byproducts that aren't part of the stage's input or output contract but that the impl wants to associate with the run. Examples:

- A trained ER classifier from run N → reusable in run N+1 (avoid retraining cold).
- Computed entity embeddings → reused for similarity work.
- Calibration data, blocking-key statistics, evaluation metrics.
- Materialization checkpoints, partial outputs, debug snapshots.

**Knot tracks metadata; the impl owns storage.** Knot records:

- `kind` — what kind of artifact (e.g., `er_classifier_model`, `entity_embeddings`, `evaluation_metrics`).
- `name` — a stable identifier within its kind.
- `produced_by_run` — which pipeline run created it.
- `content_hash` — content-addressed; for audit and replay.
- `location` — where the bytes live (S3 URI, MLflow run, parquet path, etc.). Opaque to knot.
- `metadata` — arbitrary structured blob (training params, sample counts, model version, whatever the impl wants).

Knot doesn't read or interpret artifact contents — only metadata.

## The DI surface

Contexts expose artifact registration and retrieval:

```python
# Impl writes:
ctx.register_artifact(
    kind="er_classifier_model",
    name="movie_v3",
    location="s3://...",
    metadata={"trained_on_count": 1_000_000, "auc": 0.96, ...},
)

# Impl reads:
artifact = ctx.get_artifact(
    kind="er_classifier_model",
    name="movie_v3",
    from_run="latest_succeeded",   # or a specific run hash
)
# artifact: { location, metadata, produced_by_run };
# impl loads bytes from `location` itself.
```

Exact signatures live per-context.

## Audit and content-addressing

Artifacts get full provenance for free:

- Every artifact references its producing run; runs are content-addressed (per `compiled-workflow-hashing.md`).
- Reruns of the same compiled spec produce identical artifacts in principle (if the impl is deterministic).
- Audit walk-back from a downstream consumption ("which model did this run use?") resolves through `from_run` to the producing run's hash.

## What this enables

- **Stateful ER impls.** Train on run N's per-source-facts; persist; reuse on run N+1.
- **Embedding caches.** Compute during materialization; reuse for similarity queries via a separate consumer DI.
- **Evaluation pipelines.** Each run records metrics; downstream dashboards / alerts read them.
- **Cross-run analysis.** Compare run N to run M via metadata.

## What this doesn't cover

- The impl's choice of storage backend (S3, MLflow, Weights & Biases, custom). Impl-side.
- Artifact lifecycle on the storage side (retention, GC, access control). Knot can record retention policy in metadata; enforcement is impl-side.
- Schema versioning of artifact contents — impl's concern; `metadata` blob can include a version field.

## Closest analogues

- **MLflow run artifacts** — model registries with run-scoped tracking. Closest match.
- **Airflow XCom** — cross-task data passing; smaller scope, conventionally for short-lived cross-task data.
- **dbt sources / exposures** — cataloging things that participate in a pipeline; different domain but the same "track metadata, don't own storage" pattern.
