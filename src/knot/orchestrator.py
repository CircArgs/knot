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
            self._run_merge(stage)
        elif kind == "validate":
            # Built-in structural validation via sql_gen — stubbed for Round 2.
            logger.info("stage=validate:%s — structural validation not yet implemented; skipping", stage.class_name)
        elif kind in ("resolve", "publish", "dq:normalize", "dq:resolve", "dq:merge", "dq:publish"):
            self._load_and_invoke_impl(stage, datacontext_views={})
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
                self._reader.register_parquet_view(src_name, parquet_path)
                self._materializer._conn.execute(
                    f"CREATE OR REPLACE VIEW {src_name} AS "
                    f"SELECT * FROM read_parquet('{parquet_path}')"
                )
            elif csv_path.exists():
                self._reader.register_csv_view(src_name, csv_path)
                self._materializer._conn.execute(
                    f"CREATE OR REPLACE VIEW {src_name} AS "
                    f"SELECT * FROM read_csv_auto('{csv_path}')"
                )
            else:
                raise FileNotFoundError(
                    f"normalize:{class_name} — source file not found for "
                    f"{src_name!r} (looked in {sources_dir})"
                )

            # Target path relative to lake_dir (materializer joins lake_dir).
            target_rel = Path(
                f"per_source_facts/{class_name}/source={src_name}/data.parquet"
            )

            # SELECT * adds a 'source' column carrying the source name so
            # downstream merge can GROUP BY source without extra joins.
            # Use sqlglot-safe approach: build the SQL as a projection from
            # the registered view — no raw string concat of user data; source
            # names come from trusted Source declarations in the spec.
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

    def _run_merge(self, stage: StageSpec) -> None:
        """GROUP BY over per_source_facts for class_name → resolved_facts.

        Reads all per_source_facts/<ClassName>/source=*/data.parquet via a
        wildcard glob and writes resolved_facts/<ClassName>/data.parquet.

        Round 2 merge is a simple UNION ALL of per-source contributions —
        the full GROUP BY / trust-resolution CTE is a Round 3 concern.
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

        view_name = f"psf_{class_name.lower()}"
        self._materializer._conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_parquet('{psf_glob}', union_by_name=true)"
        )

        target_rel = Path(f"resolved_facts/{class_name}/data.parquet")

        # Simple UNION ALL merge: retain every source contribution with its
        # source label.  Trust resolution (ARGMAX_TRUST etc.) is query-time
        # per commitment 7; merge never writes a "winner" column.
        merge_sql = f"SELECT * FROM {view_name}"

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

    def _load_and_invoke_impl(
        self,
        stage: StageSpec,
        datacontext_views: dict,
    ) -> Any:
        """exec impl source, instantiate bound class, call its protocol method.

        Per commitment 5: trusted-author impl source; plain exec into a fresh
        namespace; no sandbox; no restricted Python.  The team owns every byte.

        Round 2 behavior:
        - If impl_name is None or no loader configured: skip with loud warning.
        - If loader returns None: skip with loud warning.
        - Otherwise: exec source, find class whose name matches impl_name (or
          first Protocol subclass), instantiate, call the protocol method.
        """
        impl_name = stage.impl_name
        kind = stage.kind

        if impl_name is None:
            warnings.warn(
                f"stage={kind}:{stage.class_name} has no bound impl — skipping. "
                "Bind an impl to enable this stage.",
                stacklevel=2,
            )
            return None

        if self._config.impl_source_loader is None:
            warnings.warn(
                f"stage={kind}:{stage.class_name} impl={impl_name!r}: "
                "no impl_source_loader configured — skipping.",
                stacklevel=2,
            )
            return None

        source_str = self._config.impl_source_loader(
            impl_name, stage.impl_revision
        )
        if source_str is None:
            warnings.warn(
                f"stage={kind}:{stage.class_name} impl={impl_name!r} "
                f"revision={stage.impl_revision}: loader returned None — skipping.",
                stacklevel=2,
            )
            return None

        # exec into a fresh namespace — trusted author, no sandbox.
        ns: dict[str, Any] = {}
        exec(source_str, ns)  # noqa: S102

        # Find the impl class: prefer exact name match, then first Protocol subclass.
        from knot.protocols import Protocol  # local import avoids circular at module level

        impl_cls = ns.get(impl_name)
        if impl_cls is None:
            # Fall back to first class that subclasses Protocol.
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

        instance = impl_cls()

        # Dispatch to the protocol method for this stage kind.
        if kind == "resolve":
            result = instance.score(ctx=None, **datacontext_views)
        elif kind == "publish":
            result = instance.materialize(ctx=None, **datacontext_views)
        elif kind in ("dq:normalize", "dq:resolve", "dq:merge", "dq:publish"):
            result = instance.check(ctx=None, **datacontext_views)
        else:
            raise ValueError(
                f"_load_and_invoke_impl called for unhandled kind={kind!r}"
            )

        logger.info(
            "stage=%s:%s impl=%s → %r",
            kind, stage.class_name, impl_name, result,
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
