"""Integration tests for toy Maestro dispatcher.

These tests use a live Postgres connection (the same docker-compose stack
used by the rest of the knot test suite) and real DuckDB queries against
the B2 fixture CSVs.

Run:
    pytest tests/integration/test_toy_maestro.py -v
"""

from __future__ import annotations

import os
import pytest

from knot.orchestrator.toy_maestro.dispatcher import ToyMaestro, WorkflowNotFound
from knot.orchestrator.toy_maestro.workflow_models import (
    SqlSubType,
    Step,
    StepStatus,
    StepTransition,
    StepType,
    Workflow,
    WorkflowStatus,
)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "..", "data", "B2", "sources")
_IMDB_CSV = os.path.join(_FIXTURES, "imdb_movies.csv")
_TMDB_CSV = os.path.join(_FIXTURES, "tmdb_movies.csv")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def dsn(postgres_dsn):
    return postgres_dsn


@pytest.fixture(scope="module")
def maestro(dsn):
    return ToyMaestro(dsn=dsn)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def sparksql_step(step_id: str, sql: str, successors: list[str] | None = None) -> Step:
    return Step(
        id=step_id,
        type=StepType.TITUS,
        sub_type=SqlSubType.SPARKSQL,
        params={"sql": sql},
        transition=StepTransition(successors=successors or []),
    )


def trino_step(step_id: str, sql: str, successors: list[str] | None = None) -> Step:
    return Step(
        id=step_id,
        type=StepType.TITUS,
        sub_type=SqlSubType.TRINO,
        params={"sql": sql},
        transition=StepTransition(successors=successors or []),
    )


# ---------------------------------------------------------------------------
# Test 1 — sparksql_step: SELECT from B2 imdb_movies CSV
# ---------------------------------------------------------------------------


def test_sparksql_step_succeeds(maestro):
    wf = Workflow(
        id="test_sparksql_basic",
        steps=[
            sparksql_step(
                "read_imdb",
                f"SELECT imdb_id, title, year FROM read_csv_auto('{_IMDB_CSV}') LIMIT 5",
            )
        ],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_sparksql_basic")
    instance = maestro.get_run(run_id)

    assert instance.status == WorkflowStatus.SUCCEEDED
    assert len(instance.step_runs) == 1
    step_result = instance.step_runs[0]
    assert step_result.status == StepStatus.SUCCEEDED
    assert step_result.row_count == 5
    assert step_result.output_rows is not None
    # Verify column names are present
    assert "imdb_id" in step_result.output_rows[0]
    assert "title" in step_result.output_rows[0]


# ---------------------------------------------------------------------------
# Test 2 — trino_step: JOIN across two B2 CSVs
# ---------------------------------------------------------------------------


def test_trino_step_join_succeeds(maestro):
    sql = f"""
        SELECT i.imdb_id, i.title AS imdb_title, t.title AS tmdb_title
        FROM read_csv_auto('{_IMDB_CSV}') i
        JOIN read_csv_auto('{_TMDB_CSV}') t
          ON i.imdb_id = t.imdb_id
        LIMIT 10
    """
    wf = Workflow(
        id="test_trino_join",
        steps=[trino_step("join_movies", sql)],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_trino_join")
    instance = maestro.get_run(run_id)

    assert instance.status == WorkflowStatus.SUCCEEDED
    step_result = instance.step_runs[0]
    assert step_result.status == StepStatus.SUCCEEDED
    assert step_result.row_count is not None
    assert step_result.row_count > 0


# ---------------------------------------------------------------------------
# Test 3 — broken SQL → FAILED with error captured
# ---------------------------------------------------------------------------


def test_broken_sql_step_fails(maestro):
    wf = Workflow(
        id="test_broken_sql",
        steps=[
            sparksql_step(
                "bad_query",
                "SELECT * FROM nonexistent_table_xyz_12345",
            )
        ],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_broken_sql")
    instance = maestro.get_run(run_id)

    assert instance.status == WorkflowStatus.FAILED
    step_result = instance.step_runs[0]
    assert step_result.status == StepStatus.USER_FAILED
    assert step_result.error is not None
    assert len(step_result.error) > 0


# ---------------------------------------------------------------------------
# Test 4 — multi-step workflow: sequential execution ordering
# ---------------------------------------------------------------------------


def test_multi_step_sequential_ordering(maestro):
    """Three steps in sequence; assert all run and output is captured."""
    sql_a = f"SELECT count(*) AS cnt FROM read_csv_auto('{_IMDB_CSV}')"
    sql_b = f"SELECT title FROM read_csv_auto('{_IMDB_CSV}') LIMIT 3"
    sql_c = f"SELECT year FROM read_csv_auto('{_TMDB_CSV}') LIMIT 2"

    wf = Workflow(
        id="test_multi_step_seq",
        steps=[
            sparksql_step("step_a", sql_a, successors=["step_b"]),
            sparksql_step("step_b", sql_b, successors=["step_c"]),
            trino_step("step_c", sql_c),
        ],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_multi_step_seq")
    instance = maestro.get_run(run_id)

    assert instance.status == WorkflowStatus.SUCCEEDED
    assert len(instance.step_runs) == 3
    # Steps must have executed in order a → b → c
    step_ids = [r.step_id for r in instance.step_runs]
    assert step_ids == ["step_a", "step_b", "step_c"]
    for result in instance.step_runs:
        assert result.status == StepStatus.SUCCEEDED


# ---------------------------------------------------------------------------
# Test 5 — multi-step: first step fails, downstream steps are skipped
# ---------------------------------------------------------------------------


def test_multi_step_halts_on_failure(maestro):
    wf = Workflow(
        id="test_halt_on_failure",
        steps=[
            sparksql_step("step_fail", "SELECT * FROM no_such_table", successors=["step_ok"]),
            sparksql_step(
                "step_ok",
                f"SELECT 1 AS x FROM read_csv_auto('{_IMDB_CSV}') LIMIT 1",
            ),
        ],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_halt_on_failure")
    instance = maestro.get_run(run_id)

    assert instance.status == WorkflowStatus.FAILED
    # Only the first step should have run
    assert len(instance.step_runs) == 1
    assert instance.step_runs[0].step_id == "step_fail"
    assert instance.step_runs[0].status == StepStatus.USER_FAILED


# ---------------------------------------------------------------------------
# Test 6 — WorkflowNotFound raises correctly
# ---------------------------------------------------------------------------


def test_start_run_nonexistent_workflow_raises(maestro):
    with pytest.raises(WorkflowNotFound):
        maestro.start_run("workflow_that_does_not_exist_xyz")


# ---------------------------------------------------------------------------
# Test 7 — run_params are substituted into SQL
# ---------------------------------------------------------------------------


def test_run_params_substitution(maestro):
    sql = f"SELECT imdb_id, title FROM read_csv_auto('{_IMDB_CSV}') LIMIT {{limit}}"
    wf = Workflow(
        id="test_param_substitution",
        steps=[sparksql_step("step_with_param", sql)],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_param_substitution", params={"limit": 3})
    instance = maestro.get_run(run_id)

    assert instance.status == WorkflowStatus.SUCCEEDED
    step_result = instance.step_runs[0]
    assert step_result.status == StepStatus.SUCCEEDED
    assert step_result.row_count == 3


# ---------------------------------------------------------------------------
# Test 8 — duration_ms is captured and positive
# ---------------------------------------------------------------------------


def test_duration_ms_captured(maestro):
    wf = Workflow(
        id="test_duration_ms",
        steps=[
            sparksql_step(
                "timed_step",
                f"SELECT count(*) FROM read_csv_auto('{_IMDB_CSV}')",
            )
        ],
    )
    maestro.register_workflow(wf)
    run_id = maestro.start_run("test_duration_ms")
    instance = maestro.get_run(run_id)

    step_result = instance.step_runs[0]
    assert step_result.duration_ms is not None
    assert step_result.duration_ms >= 0
