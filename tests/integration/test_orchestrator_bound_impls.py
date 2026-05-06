"""Integration tests for ToyOrchestrator bound-impl invocation (Round 3).

Verifies that when a bound_impls row exists in postgres, the orchestrator:
- Loads source bytes + Config snapshot from DB
- exec()s the impl (trusted-author per commitment 5)
- Materializes DataContext views
- Calls the correct protocol method
- Returns and logs the typed Result

Round 2 skip behavior (no bound row → warnings.warn) is also covered.

Requires the docker-compose stack (postgres) to be running.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import warnings
from pathlib import Path

import psycopg
import pytest

from knot.compiler import compile as knot_compile
from knot.impact import BoundImpl
from knot.orchestrator import ToyOrchestrator, ToyOrchestratorConfig
from knot.workflow_spec import StageSpec, WorkflowSpec
from tests.fixtures.B2.spec import (
    spec as b2_spec,
    imdb_movies, tmdb_movies, wikidata_movies,
    imdb_persons, tmdb_persons,
    imdb_credits, tmdb_credits, wikidata_credits,
)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

B2_SOURCES_DIR = Path(__file__).parent.parent / "fixtures" / "B2" / "sources"

ALL_B2_SOURCES = [
    imdb_movies, tmdb_movies, wikidata_movies,
    imdb_persons, tmdb_persons,
    imdb_credits, tmdb_credits, wikidata_credits,
]

ALL_B2_WATERMARKS = {
    "imdb_movies": "wm1",
    "tmdb_movies": "wm2",
    "wikidata_movies": "wm3",
    "imdb_persons": "wm4",
    "tmdb_persons": "wm5",
    "imdb_credits": "wm6",
    "tmdb_credits": "wm7",
    "wikidata_credits": "wm8",
}

# ---------------------------------------------------------------------------
# Inline impl source strings — intentionally minimal; resemble real impls.
# Each accepts ctx + **datacontexts kwargs, returns a typed Result.
# DataContext class attributes are omitted from these minimal impls so that
# Pydantic forward-ref resolution is a non-issue; the protocol method shape
# (ctx + **datacontexts) is what matters for dispatch.
# ---------------------------------------------------------------------------

ER_IMPL_SOURCE = '''\
from knot.protocols import ERProtocol, ERResult, ScoreColumnMap

class TinyERImpl(ERProtocol):
    """Minimal ER impl for integration testing."""

    def score(self, ctx, **datacontexts):
        return ERResult(
            output_uri="/tmp/knot_test_er_pairs.parquet",
            column_map=ScoreColumnMap(
                a_canonical="a_id",
                b_canonical="b_id",
                score="similarity",
            ),
        )
'''

PUBLISHER_SOURCE = '''\
from knot.protocols import MaterializerProtocol, MaterializeResult

class TinyPublisher(MaterializerProtocol):
    """Minimal Materializer impl for integration testing."""

    def materialize(self, ctx, **datacontexts):
        return MaterializeResult(
            target="s3://test-bucket/movies/",
            status="succeeded",
            watermark="2024-01-01T00:00:00Z",
        )
'''

DQ_RUNNER_SOURCE = '''\
from knot.protocols import DqMergeRunner, DqResult, DqColumnMap

class TinyDqRunner(DqMergeRunner):
    """Minimal DQ merge runner impl for integration testing."""

    def check(self, ctx, **datacontexts):
        return DqResult(
            offenders_uri=None,
            column_map=DqColumnMap(
                rule_id="rule_id",
                class_name="class_name",
                slot_name="slot_name",
                offending_pk="offending_pk",
                severity="severity",
                detail="detail",
            ),
            passed=True,
            summary={"total": 0, "failed": 0},
        )
'''

CONFIG_IMPL_SOURCE = '''\
from knot.protocols import ERProtocol, ERResult, ScoreColumnMap

class TinyConfigImpl(ERProtocol):
    """ER impl that surfaces its Config values in the result for test assertion."""

    class Config:
        def __init__(self, min_year=1900, threshold=0.5):
            self.min_year = min_year
            self.threshold = threshold

    def score(self, ctx, **datacontexts):
        # ctx is a RunContext; config is at ctx.config per the impl-contract.
        cfg = getattr(ctx, "config", None) if ctx else None
        min_year = getattr(cfg, "min_year", -1) if cfg else -1
        threshold = getattr(cfg, "threshold", -1.0) if cfg else -1.0
        return ERResult(
            output_uri=f"/tmp/knot_config_test_{min_year}_{threshold}.parquet",
            column_map=ScoreColumnMap(
                a_canonical="a",
                b_canonical="b",
                score="score",
            ),
        )
'''

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_lake(tmp_path: Path) -> Path:
    lake = tmp_path / "lake"
    lake.mkdir()
    sources = lake / "sources"
    sources.mkdir()
    for csv in B2_SOURCES_DIR.glob("*.csv"):
        shutil.copy(csv, sources / csv.name)
    return lake


def _make_orchestrator(lake: Path, postgres_dsn: str) -> ToyOrchestrator:
    return ToyOrchestrator(
        ToyOrchestratorConfig(
            lake_dir=lake,
            postgres_dsn=postgres_dsn,
            sources=ALL_B2_SOURCES,
        )
    )


def _cache_key(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _register_impl(conn: psycopg.Connection, impl_name: str, source: str) -> int:
    """Insert an impl_revision row; return the revision number."""
    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM impl_revision WHERE name = %s",
        (impl_name,),
    ).fetchone()
    rev = (row[0] if row else 0) + 1
    conn.execute(
        """
        INSERT INTO impl_revision (name, revision, source_bytes, content_hash, pinned_spec_hash)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (impl_name, rev, source.encode(), f"hash_{impl_name}_{rev}", "spec_hash_test"),
    )
    return rev


def _register_config(
    conn: psycopg.Connection, impl_name: str, config: dict
) -> int:
    """Insert an impl_config row; return the revision number."""
    row = conn.execute(
        "SELECT COALESCE(MAX(revision), 0) FROM impl_config WHERE impl_name = %s",
        (impl_name,),
    ).fetchone()
    rev = (row[0] if row else 0) + 1
    conn.execute(
        """
        INSERT INTO impl_config (impl_name, revision, config_snapshot, content_hash)
        VALUES (%s, %s, %s, %s)
        """,
        (impl_name, rev, json.dumps(config), f"cfg_hash_{impl_name}_{rev}"),
    )
    return rev


def _bind_impl(
    conn: psycopg.Connection,
    stage: str,
    class_name: str,
    impl_name: str,
    impl_rev: int,
    config_rev: int | None = None,
) -> None:
    """Upsert a bound_impls row."""
    conn.execute(
        """
        INSERT INTO bound_impls
            (stage, class_name, impl_name, current_revision, current_config_revision)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (stage, class_name) DO UPDATE SET
            impl_name = EXCLUDED.impl_name,
            current_revision = EXCLUDED.current_revision,
            current_config_revision = EXCLUDED.current_config_revision,
            updated_at = now()
        """,
        (stage, class_name, impl_name, impl_rev, config_rev),
    )


def _cleanup_bound_impl(
    conn: psycopg.Connection, stage: str, class_name: str
) -> None:
    """Remove a bound_impls row (test isolation)."""
    conn.execute(
        "DELETE FROM bound_impls WHERE stage = %s AND class_name = %s",
        (stage, class_name),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dispatch_invokes_bound_er_impl(tmp_path, postgres_dsn):
    """Register a tiny ER impl in DB; orchestrator loads + invokes it; ERResult returned."""
    from knot.protocols import ERResult

    lake = _make_lake(tmp_path)

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        impl_rev = _register_impl(conn, "TinyERImpl", ER_IMPL_SOURCE)
        _bind_impl(conn, "resolve", "Movie", "TinyERImpl", impl_rev)

    try:
        binding = BoundImpl(
            impl_class=object,
            impl_name="TinyERImpl",
            workflow="Movie",
        )
        workflow = knot_compile(
            spec=b2_spec,
            bound_impls=[binding],
            impl_configs={"TinyERImpl": {"_impl_revision": impl_rev, "_revision": None}},
            source_watermarks=ALL_B2_WATERMARKS,
            scope="Movie",
        )

        orch = _make_orchestrator(lake, postgres_dsn)

        # Normalize + merge so per_source_facts / resolved_facts exist.
        norm_merge = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=[s for s in workflow.stages if s.kind in ("normalize", "merge")],
        )
        run_id = orch.insert_run(norm_merge, scope="Movie")
        orch.dispatch(norm_merge, run_id)

        # Compile resolve stage — impl_name=TinyERImpl should appear.
        resolve_stages = [
            s for s in workflow.stages
            if s.kind == "resolve" and s.class_name == "Movie"
        ]
        assert resolve_stages, "Expected a resolve:Movie stage in the compiled workflow"
        assert resolve_stages[0].impl_name == "TinyERImpl"

        results: list = []
        orig = orch._load_and_invoke_impl

        def capture(stage, ctx):
            r = orig(stage, ctx)
            results.append(r)
            return r

        orch._load_and_invoke_impl = capture

        resolve_wf = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=resolve_stages,
        )
        run_id2 = orch.insert_run(resolve_wf, scope="Movie:resolve")
        orch.dispatch(resolve_wf, run_id2)

        assert len(results) == 1
        assert isinstance(results[0], ERResult)
        assert results[0].output_uri == "/tmp/knot_test_er_pairs.parquet"
        assert results[0].column_map.a_canonical == "a_id"

    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _cleanup_bound_impl(conn, "resolve", "Movie")


def test_dispatch_invokes_bound_publisher(tmp_path, postgres_dsn):
    """Register a tiny Materializer impl; orchestrator dispatches; MaterializeResult captured."""
    from knot.protocols import MaterializeResult

    lake = _make_lake(tmp_path)

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        impl_rev = _register_impl(conn, "TinyPublisher", PUBLISHER_SOURCE)
        _bind_impl(conn, "publish", "Movie", "TinyPublisher", impl_rev)

    try:
        binding = BoundImpl(
            impl_class=object,
            impl_name="TinyPublisher",
            workflow="Movie",
        )
        workflow = knot_compile(
            spec=b2_spec,
            bound_impls=[binding],
            impl_configs={"TinyPublisher": {"_impl_revision": impl_rev, "_revision": None}},
            source_watermarks=ALL_B2_WATERMARKS,
            scope="Movie",
        )

        orch = _make_orchestrator(lake, postgres_dsn)

        # Normalize + merge so resolved_facts exist.
        norm_merge = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=[s for s in workflow.stages if s.kind in ("normalize", "merge")],
        )
        run_id = orch.insert_run(norm_merge, scope="Movie")
        orch.dispatch(norm_merge, run_id)

        # Build a publish stage manually with impl_name set.
        # The compiler only injects impl_name on resolve; for publish we wire
        # the stage directly to test the dispatch path.
        publish_stage = StageSpec(
            kind="publish",
            class_name="Movie",
            impl_name="TinyPublisher",
            impl_revision=impl_rev,
            cache_key=_cache_key("publish:Movie:TinyPublisher"),
        )

        results: list = []
        orig = orch._load_and_invoke_impl

        def capture(stage, ctx):
            r = orig(stage, ctx)
            results.append(r)
            return r

        orch._load_and_invoke_impl = capture

        pub_wf = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=[publish_stage],
        )
        run_id2 = orch.insert_run(pub_wf, scope="Movie:publish")
        orch.dispatch(pub_wf, run_id2)

        assert len(results) == 1
        assert isinstance(results[0], MaterializeResult)
        assert results[0].status == "succeeded"
        assert results[0].target == "s3://test-bucket/movies/"

    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _cleanup_bound_impl(conn, "publish", "Movie")


def test_dispatch_invokes_bound_dq_runner(tmp_path, postgres_dsn):
    """Register a tiny DqMergeRunner; dispatch a manual dq:merge stage; DqResult captured."""
    from knot.protocols import DqResult

    lake = _make_lake(tmp_path)

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        impl_rev = _register_impl(conn, "TinyDqRunner", DQ_RUNNER_SOURCE)
        _bind_impl(conn, "dq:merge", "Movie", "TinyDqRunner", impl_rev)

    try:
        orch = _make_orchestrator(lake, postgres_dsn)

        # Build a dq:merge stage manually — the current compiler does not emit
        # dq:* stages; this tests the dispatch arm directly.
        dq_stage = StageSpec(
            kind="dq:merge",
            class_name="Movie",
            impl_name="TinyDqRunner",
            impl_revision=impl_rev,
            cache_key=_cache_key("dq:merge:Movie:TinyDqRunner"),
        )

        # Need a compiled_workflows row to insert a run; fabricate a minimal workflow.
        workflow = knot_compile(
            spec=b2_spec,
            bound_impls=[],
            impl_configs={},
            source_watermarks=ALL_B2_WATERMARKS,
            scope="Movie",
        )
        # Reuse the compile hash but swap in our custom stage.
        dq_wf = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=[dq_stage],
        )

        results: list = []
        orig = orch._load_and_invoke_impl

        def capture(stage, ctx):
            r = orig(stage, ctx)
            results.append(r)
            return r

        orch._load_and_invoke_impl = capture

        run_id = orch.insert_run(dq_wf, scope="Movie:dq:merge")
        orch.dispatch(dq_wf, run_id)

        assert len(results) == 1
        assert isinstance(results[0], DqResult)
        assert results[0].passed is True
        assert results[0].summary["total"] == 0

    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _cleanup_bound_impl(conn, "dq:merge", "Movie")


def test_dispatch_skips_when_no_impl_bound(tmp_path, postgres_dsn):
    """Round 2 behavior preserved: no bound_impls row → warnings.warn fires, no crash."""
    lake = _make_lake(tmp_path)

    # Ensure no bound row for resolve:Movie.
    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        _cleanup_bound_impl(conn, "resolve", "Movie")

    workflow = knot_compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=ALL_B2_WATERMARKS,
        scope="Movie",
    )

    orch = _make_orchestrator(lake, postgres_dsn)

    resolve_stages = [
        s for s in workflow.stages
        if s.kind == "resolve" and s.class_name == "Movie"
    ]
    assert resolve_stages, "Expected a resolve:Movie stage"

    resolve_wf = WorkflowSpec(
        spec_revision_ids=workflow.spec_revision_ids,
        stages=resolve_stages,
    )
    run_id = orch.insert_run(resolve_wf, scope="Movie:resolve")

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        orch.dispatch(resolve_wf, run_id)

    # Exactly one warning about skipping.
    skip_warnings = [
        w for w in captured
        if "skipping" in str(w.message).lower() and "bound" in str(w.message).lower()
    ]
    assert len(skip_warnings) >= 1, (
        f"Expected a skip warning; got: {[str(w.message) for w in captured]}"
    )

    # Run still succeeded (skip is not a failure).
    with psycopg.connect(postgres_dsn) as conn:
        row = conn.execute(
            "SELECT status FROM pipeline_runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert row[0] == "succeeded"


def test_dispatch_loads_config_snapshot(tmp_path, postgres_dsn):
    """Bind impl with a Config snapshot; verify Config values reach the impl via ctx."""
    from knot.protocols import ERResult

    lake = _make_lake(tmp_path)

    config_data = {"min_year": 1985, "threshold": 0.77}

    with psycopg.connect(postgres_dsn, autocommit=True) as conn:
        impl_rev = _register_impl(conn, "TinyConfigImpl", CONFIG_IMPL_SOURCE)
        cfg_rev = _register_config(conn, "TinyConfigImpl", config_data)
        _bind_impl(conn, "resolve", "Movie", "TinyConfigImpl", impl_rev, cfg_rev)

    try:
        binding = BoundImpl(
            impl_class=object,
            impl_name="TinyConfigImpl",
            workflow="Movie",
        )
        workflow = knot_compile(
            spec=b2_spec,
            bound_impls=[binding],
            impl_configs={
                "TinyConfigImpl": {
                    "_impl_revision": impl_rev,
                    "_revision": cfg_rev,
                    **config_data,
                }
            },
            source_watermarks=ALL_B2_WATERMARKS,
            scope="Movie",
        )

        orch = _make_orchestrator(lake, postgres_dsn)

        # Normalize + merge so per_source_facts exist.
        norm_merge = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=[s for s in workflow.stages if s.kind in ("normalize", "merge")],
        )
        run_id = orch.insert_run(norm_merge, scope="Movie")
        orch.dispatch(norm_merge, run_id)

        resolve_stages = [
            s for s in workflow.stages
            if s.kind == "resolve" and s.class_name == "Movie"
        ]
        assert resolve_stages

        results: list = []
        orig = orch._load_and_invoke_impl

        def capture(stage, ctx):
            r = orig(stage, ctx)
            results.append(r)
            return r

        orch._load_and_invoke_impl = capture

        resolve_wf = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=resolve_stages,
        )
        run_id2 = orch.insert_run(resolve_wf, scope="Movie:resolve:config")
        orch.dispatch(resolve_wf, run_id2)

        assert len(results) == 1
        assert isinstance(results[0], ERResult)
        # The impl encodes config values into the output_uri.
        output_uri = results[0].output_uri
        assert "1985" in output_uri, f"Expected min_year=1985 in uri, got: {output_uri}"
        assert "0.77" in output_uri, f"Expected threshold=0.77 in uri, got: {output_uri}"

    finally:
        with psycopg.connect(postgres_dsn, autocommit=True) as conn:
            _cleanup_bound_impl(conn, "resolve", "Movie")
