"""Toy in-process orchestrator for knot WorkflowSpec.

Per commitment 1 ("compile, never execute"): knot's compiler never runs work.
This module IS the runner — it consumes the WorkflowSpec the compiler emitted.
In production, Maestro / Airflow / Argo replaces this; the toy orchestrator
is the in-process equivalent for testing and reference.

Per commitment 5 (trusted authors, single team): impl source is exec()d into a
fresh namespace without sandboxing.  The team owns every impl byte.

Round 2 scope: normalize:<source> and merge:<class> run end-to-end against
B2 fixtures.  Impl-bound stages (resolve, publish, dq:*) skip loudly when no
impl source is available — next round adds real impl bodies.

Round 3 scope: when a bound_impls row exists in postgres for (stage, class_name),
the orchestrator loads source bytes + Config snapshot from the DB, exec()s the
impl, materializes DataContext views, and calls the protocol method.
"""

from __future__ import annotations

import datetime
import json
import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pyarrow as pa

from knot.compiler import compile_hash
from knot.lake.duckdb_materializer import DuckDBMaterializer, DuckDBMaterializerConfig
from knot.lake.duckdb_reader import DuckDBReader, DuckDBReaderConfig
from knot.metaschema import Source
from knot.workflow_spec import StageSpec, WorkflowSpec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class ToyOrchestratorConfig:
    """Config for the in-process toy orchestrator.

    Attributes:
        lake_dir: Root directory under which fixture parquet/CSV files live.
            normalize reads from lake_dir/sources/<name>.csv (or .parquet).
            normalize writes per_source_facts/<Class>/source=<src>/data.parquet.
            merge reads those and writes resolved_facts/<Class>/data.parquet.
        postgres_dsn: For pipeline_runs status updates.
        sources: List of Source declarations from the spec; used by normalize
            to map source name → entity class.
        impl_source_loader: (impl_name, revision) -> Python source str.
            Called for impl-bound stages (resolve, publish, dq:*).
            Return None to skip the stage with a loud warning.
    """

    lake_dir: Path
    postgres_dsn: str
    sources: list[Source] = field(default_factory=list)
    impl_source_loader: Any = None  # callable(impl_name, revision) -> str | None


# ---------------------------------------------------------------------------
# Internal context passed to per-stage dispatch helpers
# ---------------------------------------------------------------------------

@dataclass
class _RunCtx:
    """Lightweight per-run context threaded through stage dispatch."""

    run_id: int
    workflow: WorkflowSpec
    compile_hash_str: str


# ---------------------------------------------------------------------------
# ToyOrchestrator
# ---------------------------------------------------------------------------

class ToyOrchestrator:
    """Walks a compiled WorkflowSpec, dispatches each stage in order, records
    status to pipeline_runs.

    Per commitment 1, this is NOT knot core; it's a reference runner that
    a real deployment would replace with Maestro / Airflow / Argo.

    Per commitment 5: trusted authors — impl source exec()d into a plain
    namespace, no sandbox, no restricted Python.
    """

    def __init__(self, config: ToyOrchestratorConfig) -> None:
        self._config = config
        self._reader = DuckDBReader(
            DuckDBReaderConfig(lake_dir=config.lake_dir)
        )
        self._materializer = DuckDBMaterializer(
            DuckDBMaterializerConfig(lake_dir=config.lake_dir)
        )
        # Map source name → Source for quick lookup in normalize dispatch.
        self._source_by_name: dict[str, Source] = {
            s.name: s for s in config.sources
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def dispatch(self, workflow: WorkflowSpec, run_id: int) -> WorkflowSpec:
        """Walk stages in order.  Update pipeline_runs.status as we go.

        For each stage:
        - If cache_hit_artifact: skip; reuse prior artifact.
        - Otherwise dispatch per stage.kind.

        On any stage failure: set pipeline_runs.status='failed' with error
        text and re-raise so the caller knows.
        On all-stages-pass: set pipeline_runs.status='succeeded'.

        Returns the WorkflowSpec unchanged (callers may inspect it after).
        """
        ch = compile_hash(workflow)
        ctx = _RunCtx(run_id=run_id, workflow=workflow, compile_hash_str=ch)

        self._set_status(run_id, "running")

        cache_hit_stage_keys: list[str] = []

        try:
            for stage in workflow.stages:
                if stage.cache_hit_artifact:
                    logger.info(
                        "run=%d stage=%s:%s cache_hit artifact=%s — skipping",
                        run_id, stage.kind, stage.class_name,
                        stage.cache_hit_artifact,
                    )
                    cache_hit_stage_keys.append(stage.cache_key)
                    continue

                logger.info(
                    "run=%d stage=%s:%s — dispatching",
                    run_id, stage.kind, stage.class_name,
                )
                self._dispatch_stage(stage, ctx)

        except Exception as exc:
            self._set_status(run_id, "failed", error=str(exc))
            raise

        self._set_succeeded(run_id, cache_hit_stage_keys)
        return workflow

    # ------------------------------------------------------------------
    # Per-stage dispatch
    # ------------------------------------------------------------------

    def _dispatch_stage(self, stage: StageSpec, ctx: _RunCtx) -> None:
        """Route one stage to the correct handler."""
        kind = stage.kind

        if kind == "normalize":
            self._run_normalize(stage)
        elif kind == "merge":
            self._run_merge_passthrough(stage)
        elif kind == "validate":
            # Built-in structural validation via sql_gen — stubbed for Round 2.
            warnings.warn(
                f"stage=validate:{stage.class_name} — structural validation not yet implemented; skipping.",
                stacklevel=2,
            )
        elif kind in ("resolve", "publish", "dq:normalize", "dq:resolve", "dq:merge", "dq:publish"):
            # Round 3: look up bound_impls row.  If present, invoke impl.
            # If absent, keep Round 2 loud-skip behavior.
            if stage.class_name and self._has_bound_impl(stage):
                self._load_and_invoke_impl(stage, ctx)
            else:
                warnings.warn(
                    f"stage={kind}:{stage.class_name} has no bound impl — skipping. "
                    "Bind an impl via PUT /bound_impls to enable this stage.",
                    stacklevel=2,
                )
        else:
            raise ValueError(f"Unknown stage kind: {kind!r}")

    # ------------------------------------------------------------------
    # normalize
    # ------------------------------------------------------------------

    def _run_normalize(self, stage: StageSpec) -> None:
        """Read source CSV from lake_dir/sources/<name>.csv, write per_source_facts.

        Discovers all sources whose entity_class matches stage.class_name.
        For each source, registers a DuckDB view and writes a parquet file at:
            per_source_facts/<ClassName>/source=<source_name>/data.parquet

        The materializer uses a fresh DuckDB connection seeded with the source
        view.  No SQL string concatenation — table name comes from the source
        view registered against the source file path.
        """
        class_name = stage.class_name
        if class_name is None:
            raise ValueError("normalize stage missing class_name")

        # Find all sources for this class.
        class_sources = [
            s for s in self._config.sources
            if s.entity_class.name == class_name
        ]
        if not class_sources:
            logger.warning(
                "normalize:%s — no sources declared; writing empty output",
                class_name,
            )
            return

        sources_dir = self._config.lake_dir / "sources"

        for source in class_sources:
            src_name = source.name
            # Locate source file — prefer parquet, fall back to CSV.
            parquet_path = sources_dir / f"{src_name}.parquet"
            csv_path = sources_dir / f"{src_name}.csv"

            if parquet_path.exists():
                self._materializer.register_parquet_view(src_name, parquet_path)
            elif csv_path.exists():
                self._materializer.register_csv_view(src_name, csv_path)
            else:
                raise FileNotFoundError(
                    f"normalize:{class_name} — source file not found for "
                    f"{src_name!r} (looked in {sources_dir})"
                )

            # Target path relative to lake_dir (materializer joins lake_dir).
            target_rel = Path(
                f"per_source_facts/{class_name}/source={src_name}/data.parquet"
            )

            # Add _knot_source so downstream merge can filter by source without
            # extra joins.  Source names come from trusted Source declarations in
            # the spec — no raw user input in the SQL string.
            select_sql = (
                f"SELECT *, '{src_name}' AS _knot_source FROM {src_name}"
            )

            self._materializer.materialize(
                ctx=None,
                query_sql=select_sql,
                target_path=target_rel,
            )
            logger.info(
                "normalize:%s source=%s → %s",
                class_name, src_name, target_rel,
            )

    # ------------------------------------------------------------------
    # merge
    # ------------------------------------------------------------------

    def _run_merge_passthrough(self, stage: StageSpec) -> None:
        """Round 2 placeholder: UNION ALL of per_source_facts → resolved_facts.

        Reads all per_source_facts/<ClassName>/source=*/data.parquet via a
        wildcard glob and writes resolved_facts/<ClassName>/data.parquet.

        This is a UNION ALL pass-through, not real merge.  Real merge applies
        ER decisions and produces canonical rows; that requires trust-resolution
        logic landing in a later round.
        """
        class_name = stage.class_name
        if class_name is None:
            raise ValueError("merge stage missing class_name")

        psf_glob = (
            self._config.lake_dir
            / f"per_source_facts/{class_name}/source=*/data.parquet"
        )

        # Check at least one parquet exists before attempting the query.
        matches = list(self._config.lake_dir.glob(
            f"per_source_facts/{class_name}/source=*/data.parquet"
        ))
        if not matches:
            logger.warning(
                "merge:%s — no per_source_facts parquet found; skipping",
                class_name,
            )
            return

        target_rel = Path(f"resolved_facts/{class_name}/data.parquet")

        # UNION ALL pass-through: read all per-source parquet files in one
        # read_parquet call.  Trust resolution (ARGMAX_TRUST etc.) is query-time
        # per commitment 7; merge never writes a "winner" column.
        merge_sql = (
            f"SELECT * FROM read_parquet('{psf_glob}', union_by_name=true)"
        )

        self._materializer.materialize(
            ctx=None,
            query_sql=merge_sql,
            target_path=target_rel,
        )
        logger.info(
            "merge:%s → resolved_facts/%s/data.parquet (%d source files merged)",
            class_name, class_name, len(matches),
        )

    # ------------------------------------------------------------------
    # Impl-bound stage dispatch
    # ------------------------------------------------------------------

    def _has_bound_impl(self, stage: StageSpec) -> bool:
        """Return True if a bound_impls row exists for (stage.kind, stage.class_name)."""
        with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM bound_impls WHERE stage = %s AND class_name = %s",
                (stage.kind, stage.class_name),
            ).fetchone()
        return row is not None

    def _materialize_datacontexts(
        self,
        impl_class: type,
        stage: StageSpec,
    ) -> dict[str, pa.Table]:
        """For each DataContext class attribute on impl_class, run SQL and return Arrow Tables.

        Lake path selection by stage kind:
        - resolve / dq:normalize → per_source_facts/<ClassName>/source=*/data.parquet
          (DISAGREEMENT_AWARE: each source bag is visible separately)
        - publish / dq:merge / dq:publish → resolved_facts/<ClassName>/data.parquet
          (RESOLVED: post-merge, trust-resolution CTE applies query-time)
        - dq:resolve → entity_bindings not yet written; DataContexts return empty Tables.

        SQL generation: for each DataContext whose primary is an OntologyClass,
        register a parquet view and SELECT * [WHERE <dc.where>] from it.
        The where clause is translated via sql_gen.to_sqlglot into a DuckDB predicate.
        """
        from knot.protocols import DataContext
        from knot.sql_gen import to_sqlglot
        import sqlglot.expressions as _exp

        kind = stage.kind
        class_name = stage.class_name or ""

        # Choose the lake path pattern for this stage.
        if kind in ("resolve", "dq:normalize"):
            parquet_glob = str(
                self._config.lake_dir
                / f"per_source_facts/{class_name}/source=*/data.parquet"
            )
            view_base = f"psf_{class_name.lower()}"
        elif kind in ("publish", "dq:merge", "dq:publish", "merge"):
            parquet_glob = str(
                self._config.lake_dir
                / f"resolved_facts/{class_name}/data.parquet"
            )
            view_base = f"rf_{class_name.lower()}"
        else:
            # dq:resolve and anything else: no standard lake path yet.
            parquet_glob = None
            view_base = None

        results: dict[str, pa.Table] = {}

        for attr_name in dir(impl_class):
            try:
                val = getattr(impl_class, attr_name)
            except Exception:
                continue
            if not isinstance(val, DataContext):
                continue

            if parquet_glob is None:
                # Stage kind has no standard lake path; return empty table.
                results[attr_name] = pa.table({})
                continue

            # Register a fresh parquet view for this DataContext.
            # Each DataContext gets its own view name to avoid collisions.
            view_name = f"{view_base}_{attr_name}"
            self._reader.register_parquet_view(view_name, Path(parquet_glob))

            # Build SELECT * FROM <view_name>.
            base_select = _exp.select(_exp.Star()).from_(view_name)

            # Attach WHERE clause if the DataContext declares one.
            if val.where is not None:
                try:
                    where_expr = to_sqlglot(val.where)
                    base_select = base_select.where(where_expr)
                except Exception as exc:
                    logger.warning(
                        "stage=%s:%s DataContext=%s — could not translate where clause: %s; "
                        "falling back to unfiltered read",
                        kind, class_name, attr_name, exc,
                    )

            sql = base_select.sql(dialect="duckdb")

            try:
                table = self._reader.read(ctx=None, sql=sql)
            except Exception as exc:
                logger.warning(
                    "stage=%s:%s DataContext=%s — read failed (%s); returning empty table",
                    kind, class_name, attr_name, exc,
                )
                table = pa.table({})

            results[attr_name] = table
            logger.debug(
                "stage=%s:%s DataContext=%s → %d rows",
                kind, class_name, attr_name, table.num_rows,
            )

        return results

    def _load_and_invoke_impl(
        self,
        stage: StageSpec,
        ctx: _RunCtx,
    ) -> Any:
        """End-to-end impl invocation from bound_impls DB row.

        Per commitment 5: trusted-author impl source; plain exec into a fresh
        namespace; no sandbox; no restricted Python.  The team owns every byte.

        Steps:
        1. SELECT from bound_impls → impl_name + current_revision + current_config_revision
        2. SELECT from impl_revision → source_bytes
        3. SELECT from impl_config → config_snapshot (if config_revision is set)
        4. exec source_bytes into a fresh namespace; find impl class
        5. Instantiate; bind Config snapshot if the class declares a Config
        6. Materialize DataContext views → {attr_name: pa.Table}
        7. Call protocol method (score / materialize / check)
        8. Return typed Result
        """
        kind = stage.kind
        class_name = stage.class_name

        with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
            bi_row = conn.execute(
                """
                SELECT impl_name, current_revision, current_config_revision
                FROM bound_impls WHERE stage = %s AND class_name = %s
                """,
                (kind, class_name),
            ).fetchone()

        if bi_row is None:
            # Should not happen — _has_bound_impl already checked, but guard anyway.
            raise RuntimeError(
                f"bound_impls row disappeared for stage={kind!r} class={class_name!r}"
            )

        impl_name, impl_revision, config_revision = bi_row

        # Load source bytes.
        with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
            src_row = conn.execute(
                "SELECT source_bytes FROM impl_revision WHERE name = %s AND revision = %s",
                (impl_name, impl_revision),
            ).fetchone()

        if src_row is None:
            raise RuntimeError(
                f"impl_revision row missing for impl={impl_name!r} revision={impl_revision}"
            )

        raw_bytes = src_row[0]
        if isinstance(raw_bytes, memoryview):
            source_str = bytes(raw_bytes).decode()
        elif isinstance(raw_bytes, bytes):
            source_str = raw_bytes.decode()
        else:
            source_str = str(raw_bytes)

        # Load Config snapshot (optional).
        config_snapshot: dict = {}
        if config_revision is not None:
            with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
                cfg_row = conn.execute(
                    "SELECT config_snapshot FROM impl_config WHERE impl_name = %s AND revision = %s",
                    (impl_name, config_revision),
                ).fetchone()
            if cfg_row is not None:
                raw_cfg = cfg_row[0]
                config_snapshot = raw_cfg if isinstance(raw_cfg, dict) else json.loads(raw_cfg)

        # exec into a fresh namespace — trusted author, no sandbox.
        ns: dict[str, Any] = {}
        exec(source_str, ns)  # noqa: S102

        # Find the impl class: prefer exact name match, then first Protocol subclass.
        from knot.protocols import Protocol  # local import avoids circular at module level

        impl_cls = ns.get(impl_name)
        if impl_cls is None:
            for obj in ns.values():
                if (
                    isinstance(obj, type)
                    and issubclass(obj, Protocol)
                    and obj is not Protocol
                ):
                    impl_cls = obj
                    break

        if impl_cls is None:
            raise RuntimeError(
                f"impl_source for {impl_name!r} does not define a class named "
                f"{impl_name!r} or any Protocol subclass."
            )

        # Protocol subclasses are Pydantic models.  Forward refs (e.g. DataContext)
        # in annotations may not be resolvable in the exec'd namespace until
        # model_rebuild() is called with the surrounding module's globals.
        if hasattr(impl_cls, "model_rebuild"):
            try:
                import knot.protocols as _kp
                impl_cls.model_rebuild(_types_namespace=vars(_kp))
            except Exception:
                pass  # rebuild is best-effort; instantiation may still succeed

        instance = impl_cls()

        # Bind Config snapshot if the impl declares a Config class.
        # ctx passed to protocol methods carries the config for this run.
        run_ctx: Any = None
        config_cls = getattr(impl_cls, "Config", None)
        if config_cls is not None and config_snapshot:
            try:
                run_ctx = config_cls(**{
                    k: v for k, v in config_snapshot.items()
                    if not k.startswith("_")
                })
            except Exception as exc:
                logger.warning(
                    "stage=%s:%s impl=%s — Config hydration failed (%s); ctx=None",
                    kind, class_name, impl_name, exc,
                )

        # Materialize DataContext views.
        datacontext_views = self._materialize_datacontexts(impl_cls, stage)

        # Dispatch to the protocol method for this stage kind.
        if kind == "resolve":
            result = instance.score(ctx=run_ctx, **datacontext_views)
        elif kind == "publish":
            result = instance.materialize(ctx=run_ctx, **datacontext_views)
        elif kind in ("dq:normalize", "dq:resolve", "dq:merge", "dq:publish"):
            result = instance.check(ctx=run_ctx, **datacontext_views)
        else:
            raise ValueError(
                f"_load_and_invoke_impl called for unhandled kind={kind!r}"
            )

        logger.info(
            "stage=%s:%s impl=%s revision=%s → %r",
            kind, class_name, impl_name, impl_revision, result,
        )
        return result

    # ------------------------------------------------------------------
    # pipeline_runs helpers
    # ------------------------------------------------------------------

    def _set_status(
        self,
        run_id: int,
        status: str,
        error: str | None = None,
    ) -> None:
        """Update pipeline_runs.status (and optionally error + completed_at)."""
        with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
            if status in ("succeeded", "failed"):
                conn.execute(
                    """
                    UPDATE pipeline_runs
                       SET status = %s,
                           error = %s,
                           completed_at = %s
                     WHERE id = %s
                    """,
                    (status, error, datetime.datetime.now(datetime.timezone.utc), run_id),
                )
            else:
                conn.execute(
                    "UPDATE pipeline_runs SET status = %s WHERE id = %s",
                    (status, run_id),
                )

    def _set_succeeded(
        self,
        run_id: int,
        cache_hit_stage_keys: list[str],
    ) -> None:
        """Mark run succeeded; write cache_hit_stages array."""
        with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
            conn.execute(
                """
                UPDATE pipeline_runs
                   SET status = 'succeeded',
                       completed_at = %s,
                       cache_hit_stages = %s
                 WHERE id = %s
                """,
                (
                    datetime.datetime.now(datetime.timezone.utc),
                    cache_hit_stage_keys,
                    run_id,
                ),
            )

    # ------------------------------------------------------------------
    # Control-DB bootstrap helpers
    # ------------------------------------------------------------------

    def insert_run(
        self,
        workflow: WorkflowSpec,
        scope: str,
    ) -> int:
        """Insert a pipeline_runs row (status=pending) and return the new id.

        Also upserts compiled_workflows if the hash is not yet present.
        Callers use the returned id as run_id for dispatch().
        """
        from knot.canonical import canonical_dump

        ch = compile_hash(workflow)
        spec_json = json.loads(canonical_dump(workflow))

        with psycopg.connect(self._config.postgres_dsn, autocommit=True) as conn:
            conn.execute(
                """
                INSERT INTO compiled_workflows (hash, spec)
                VALUES (%s, %s)
                ON CONFLICT (hash) DO NOTHING
                """,
                (ch, json.dumps(spec_json)),
            )
            row = conn.execute(
                """
                INSERT INTO pipeline_runs
                    (compile_hash, scope, started_at, status)
                VALUES (%s, %s, %s, 'pending')
                RETURNING id
                """,
                (ch, scope, datetime.datetime.now(datetime.timezone.utc)),
            ).fetchone()

        return row[0]
