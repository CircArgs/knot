"""Integration tests for ToyOrchestrator.

Round 2 scope: normalize:<source> and merge:<class> running end-to-end
against B2 fixture CSVs.  Impl-bound stages (resolve, publish, dq:*) are
stubbed — the orchestrator skips them with a warning when no impl is bound.

Requires the docker-compose stack (postgres) to be running.  Tests that
need postgres are marked with the 'postgres' fixture; the conftest's
_apply_control_schema override means these CAN run against a live stack.
"""

from __future__ import annotations

import shutil
import warnings
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from knot.compiler import compile as knot_compile
from knot.orchestrator import ToyOrchestrator, ToyOrchestratorConfig
from tests.fixtures.B2.spec import (
    spec as b2_spec,
    imdb_movies, tmdb_movies, wikidata_movies,
    imdb_persons, tmdb_persons,
    imdb_credits, tmdb_credits, wikidata_credits,
)

# ---------------------------------------------------------------------------
# Fixture directory
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
# Helpers
# ---------------------------------------------------------------------------

def _make_lake(tmp_path: Path) -> Path:
    """Create a lake_dir with fixture CSVs copied into sources/."""
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


def _compile_movie_workflow(watermarks: dict | None = None):
    return knot_compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=watermarks or ALL_B2_WATERMARKS,
        scope="Movie",
    )


def _compile_full_workflow(watermarks: dict | None = None):
    return knot_compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=watermarks or ALL_B2_WATERMARKS,
        scope="full",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_dispatch_normalize_stage(tmp_path, postgres_dsn):
    """Orchestrator runs normalize:Movie; per_source_facts parquets exist with rows."""
    lake = _make_lake(tmp_path)
    orch = _make_orchestrator(lake, postgres_dsn)
    workflow = _compile_movie_workflow()

    run_id = orch.insert_run(workflow, scope="Movie")

    # Only run normalize stages to keep the test focused.
    from knot.workflow_spec import WorkflowSpec
    normalize_only = WorkflowSpec(
        spec_revision_ids=workflow.spec_revision_ids,
        stages=[s for s in workflow.stages if s.kind == "normalize"],
    )

    orch.dispatch(normalize_only, run_id)

    # imdb_movies → per_source_facts/Movie/source=imdb_movies/data.parquet
    imdb_out = lake / "per_source_facts" / "Movie" / "source=imdb_movies" / "data.parquet"
    assert imdb_out.exists(), f"Expected {imdb_out} to exist"

    table = pq.read_table(imdb_out)
    assert table.num_rows > 0, "Expected rows in imdb_movies per_source_facts"

    # tmdb_movies parquet also written
    tmdb_out = lake / "per_source_facts" / "Movie" / "source=tmdb_movies" / "data.parquet"
    assert tmdb_out.exists(), f"Expected {tmdb_out} to exist"


def test_dispatch_merge_stage(tmp_path, postgres_dsn):
    """After normalize, merge produces resolved_facts/Movie/data.parquet."""
    lake = _make_lake(tmp_path)
    orch = _make_orchestrator(lake, postgres_dsn)
    workflow = _compile_movie_workflow()

    run_id = orch.insert_run(workflow, scope="Movie")

    from knot.workflow_spec import WorkflowSpec
    norm_merge = WorkflowSpec(
        spec_revision_ids=workflow.spec_revision_ids,
        stages=[
            s for s in workflow.stages
            if s.kind in ("normalize", "merge")
        ],
    )

    orch.dispatch(norm_merge, run_id)

    resolved = lake / "resolved_facts" / "Movie" / "data.parquet"
    assert resolved.exists(), f"Expected {resolved} to exist"

    table = pq.read_table(resolved)
    assert table.num_rows > 0, "resolved_facts should have rows from merged sources"

    # All source contributions retained (multi-valued — no winner written).
    sources_in_output = set(table.column("_knot_source").to_pylist())
    assert "imdb_movies" in sources_in_output
    assert "tmdb_movies" in sources_in_output


def test_dispatch_full_movie_pipeline(tmp_path, postgres_dsn):
    """Run all Movie stages; pipeline_runs.status='succeeded'; resolved_facts on disk."""
    lake = _make_lake(tmp_path)
    orch = _make_orchestrator(lake, postgres_dsn)
    workflow = _compile_movie_workflow()

    run_id = orch.insert_run(workflow, scope="Movie")

    # Suppress warnings from impl-bound stages that have no impl.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        orch.dispatch(workflow, run_id)

    # pipeline_runs.status = 'succeeded'
    import psycopg
    with psycopg.connect(postgres_dsn) as conn:
        row = conn.execute(
            "SELECT status FROM pipeline_runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert row is not None
    assert row[0] == "succeeded"

    resolved = lake / "resolved_facts" / "Movie" / "data.parquet"
    assert resolved.exists()


def test_dispatch_skips_cache_hit_stages(tmp_path, postgres_dsn):
    """Stages with cache_hit_artifact set are skipped; no re-execution."""
    lake = _make_lake(tmp_path)
    orch = _make_orchestrator(lake, postgres_dsn)

    # Compile with a cache-hit for every stage.
    prior_keys = {
        s.cache_key: f"s3://prior/artifact/{s.kind}/{s.class_name}.parquet"
        for s in _compile_movie_workflow().stages
    }
    workflow = knot_compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=ALL_B2_WATERMARKS,
        scope="Movie",
        prior_cache_keys=prior_keys,
    )

    # All stages should have cache_hit_artifact set.
    assert all(s.cache_hit_artifact is not None for s in workflow.stages), (
        "Expected all stages to have cache_hit_artifact when prior_cache_keys covers all keys"
    )

    run_id = orch.insert_run(workflow, scope="Movie")
    orch.dispatch(workflow, run_id)

    # No per_source_facts or resolved_facts written — execution was skipped.
    assert not (lake / "per_source_facts").exists(), (
        "per_source_facts should not be written for cache-hit stages"
    )
    assert not (lake / "resolved_facts").exists(), (
        "resolved_facts should not be written for cache-hit stages"
    )

    # Run status still succeeded.
    import psycopg
    with psycopg.connect(postgres_dsn) as conn:
        row = conn.execute(
            "SELECT status, cache_hit_stages FROM pipeline_runs WHERE id = %s",
            (run_id,),
        ).fetchone()
    assert row[0] == "succeeded"
    # cache_hit_stages contains the stage keys.
    assert len(row[1]) == len(workflow.stages)


def test_dispatch_records_run_status(tmp_path, postgres_dsn):
    """pipeline_runs.status transitions: pending → running → succeeded."""
    import psycopg

    lake = _make_lake(tmp_path)
    orch = _make_orchestrator(lake, postgres_dsn)

    from knot.workflow_spec import WorkflowSpec
    workflow = knot_compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=ALL_B2_WATERMARKS,
        scope="Movie",
    )
    normalize_only = WorkflowSpec(
        spec_revision_ids=workflow.spec_revision_ids,
        stages=[s for s in workflow.stages if s.kind == "normalize"],
    )

    run_id = orch.insert_run(normalize_only, scope="Movie")

    # Before dispatch: pending
    with psycopg.connect(postgres_dsn) as conn:
        row = conn.execute(
            "SELECT status FROM pipeline_runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert row[0] == "pending"

    orch.dispatch(normalize_only, run_id)

    # After dispatch: succeeded
    with psycopg.connect(postgres_dsn) as conn:
        row = conn.execute(
            "SELECT status, completed_at FROM pipeline_runs WHERE id = %s",
            (run_id,),
        ).fetchone()
    assert row[0] == "succeeded"
    assert row[1] is not None  # completed_at set


def test_dispatch_records_failed_status(tmp_path, postgres_dsn):
    """On stage failure, pipeline_runs.status='failed' with error text."""
    import psycopg

    lake = _make_lake(tmp_path)
    # Use a lake_dir with no sources/ dir → normalize will raise FileNotFoundError.
    bad_lake = tmp_path / "bad_lake"
    bad_lake.mkdir()

    orch = _make_orchestrator(bad_lake, postgres_dsn)
    workflow = knot_compile(
        spec=b2_spec,
        bound_impls=[],
        impl_configs={},
        source_watermarks=ALL_B2_WATERMARKS,
        scope="Movie",
    )

    from knot.workflow_spec import WorkflowSpec
    normalize_only = WorkflowSpec(
        spec_revision_ids=workflow.spec_revision_ids,
        stages=[s for s in workflow.stages if s.kind == "normalize"],
    )

    run_id = orch.insert_run(normalize_only, scope="Movie")

    with pytest.raises(FileNotFoundError):
        orch.dispatch(normalize_only, run_id)

    with psycopg.connect(postgres_dsn) as conn:
        row = conn.execute(
            "SELECT status, error FROM pipeline_runs WHERE id = %s", (run_id,)
        ).fetchone()
    assert row[0] == "failed"
    assert row[1] is not None and len(row[1]) > 0


def test_dispatch_invokes_bound_impl(tmp_path, postgres_dsn):
    """bind a tiny test impl that returns a known ERResult; orchestrator calls it.

    Round 3: impl source + binding are registered in postgres; the orchestrator
    loads them from DB, exec()s the source, and calls score().
    """
    import psycopg as _psycopg
    from knot.protocols import ERResult, ScoreColumnMap
    from knot.impact import BoundImpl

    # Minimal impl source that returns a known ERResult.
    impl_source = '''\
from pathlib import Path
from knot.protocols import ERProtocol, ERResult, ScoreColumnMap

class test_er_impl(ERProtocol):
    def score(self, ctx, **datacontexts):
        return ERResult(
            table=Path("/tmp/test_er_pairs.parquet"),
            column_map=ScoreColumnMap(
                a_canonical="a", b_canonical="b", score="score"
            ),
        )
'''

    # Register impl source + bound_impls row in postgres.
    with _psycopg.connect(postgres_dsn, autocommit=True) as conn:
        rev_row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM impl_revision WHERE name = %s",
            ("test_er_impl",),
        ).fetchone()
        impl_rev = (rev_row[0] if rev_row else 0) + 1
        conn.execute(
            """
            INSERT INTO impl_revision (name, revision, source_bytes, content_hash, pinned_spec_hash)
            VALUES (%s, %s, %s, %s, %s)
            """,
            ("test_er_impl", impl_rev, impl_source.encode(), "hash_test_er", "spec_test"),
        )
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
            ("resolve", "Movie", "test_er_impl", impl_rev, None),
        )

    try:
        results_captured: list = []

        lake = _make_lake(tmp_path)

        binding = BoundImpl(
            impl_class=object,  # placeholder — orchestrator exec()s source directly
            impl_name="test_er_impl",
            workflow="Movie",
        )
        workflow = knot_compile(
            spec=b2_spec,
            bound_impls=[binding],
            impl_configs={"test_er_impl": {"_revision": impl_rev, "_impl_revision": impl_rev}},
            source_watermarks=ALL_B2_WATERMARKS,
            scope="Movie",
        )

        orch = ToyOrchestrator(
            ToyOrchestratorConfig(
                lake_dir=lake,
                postgres_dsn=postgres_dsn,
                sources=ALL_B2_SOURCES,
            )
        )

        # Patch _load_and_invoke_impl to capture result.
        original = orch._load_and_invoke_impl

        def capturing_invoke(stage, ctx):
            result = original(stage, ctx)
            if result is not None:
                results_captured.append(result)
            return result

        orch._load_and_invoke_impl = capturing_invoke

        from knot.workflow_spec import WorkflowSpec
        resolve_only = WorkflowSpec(
            spec_revision_ids=workflow.spec_revision_ids,
            stages=[s for s in workflow.stages if s.kind == "resolve" and s.class_name == "Movie"],
        )

        run_id = orch.insert_run(resolve_only, scope="Movie")
        orch.dispatch(resolve_only, run_id)

        assert len(results_captured) == 1
        assert isinstance(results_captured[0], ERResult)
        assert str(results_captured[0].table) == "/tmp/test_er_pairs.parquet"

    finally:
        with _psycopg.connect(postgres_dsn, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM bound_impls WHERE stage = 'resolve' AND class_name = 'Movie'"
            )
