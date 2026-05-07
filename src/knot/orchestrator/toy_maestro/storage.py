"""Postgres persistence for toy Maestro.

Tables:
  maestro_workflows      — registered workflow definitions (JSON)
  maestro_workflow_runs  — run instances (JSON)
  maestro_step_runs      — per-step status rows

All table names are prefixed with ``maestro_`` to avoid conflicts with
knot's existing control-plane tables (spec_revisions, etc.).

Schema is applied idempotently at startup via ``apply_schema(dsn)``.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg

from knot.orchestrator.toy_maestro.workflow_models import (
    RunInstance,
    StepRunResult,
    Workflow,
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS maestro_workflows (
    workflow_id   TEXT        PRIMARY KEY,
    definition    JSONB       NOT NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS maestro_workflow_runs (
    run_id        TEXT        PRIMARY KEY,
    workflow_id   TEXT        NOT NULL REFERENCES maestro_workflows(workflow_id),
    instance      JSONB       NOT NULL,
    status        TEXT        NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS maestro_workflow_runs_workflow_id
    ON maestro_workflow_runs (workflow_id);

CREATE TABLE IF NOT EXISTS maestro_step_runs (
    id            SERIAL      PRIMARY KEY,
    run_id        TEXT        NOT NULL REFERENCES maestro_workflow_runs(run_id),
    step_id       TEXT        NOT NULL,
    step_attempt  INT         NOT NULL DEFAULT 1,
    status        TEXT        NOT NULL,
    start_time    TIMESTAMPTZ,
    end_time      TIMESTAMPTZ,
    duration_ms   INT,
    row_count     INT,
    error         TEXT,
    output_rows   JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (run_id, step_id, step_attempt)
);
"""

_DEFAULT_DSN = "postgresql://knot:knot@localhost:5432/knot_control"


def _dsn() -> str:
    return os.environ.get("KNOT_CONTROL_DSN", _DEFAULT_DSN)


def apply_schema(dsn: str | None = None) -> None:
    """Create toy-maestro tables if they don't exist. Idempotent."""
    with psycopg.connect(dsn or _dsn(), autocommit=True) as conn:
        conn.execute(_SCHEMA_SQL)


# ---------------------------------------------------------------------------
# Workflow registration
# ---------------------------------------------------------------------------


def save_workflow(workflow: Workflow, dsn: str | None = None) -> None:
    sql = """
        INSERT INTO maestro_workflows (workflow_id, definition)
        VALUES (%s, %s)
        ON CONFLICT (workflow_id) DO UPDATE
            SET definition = EXCLUDED.definition,
                registered_at = now()
    """
    with psycopg.connect(dsn or _dsn(), autocommit=True) as conn:
        conn.execute(sql, (workflow.id, workflow.model_dump_json()))


def load_workflow(workflow_id: str, dsn: str | None = None) -> Workflow | None:
    sql = "SELECT definition FROM maestro_workflows WHERE workflow_id = %s"
    with psycopg.connect(dsn or _dsn()) as conn:
        row = conn.execute(sql, (workflow_id,)).fetchone()
    if row is None:
        return None
    # psycopg returns JSONB columns as dicts, not raw JSON strings
    return Workflow.model_validate(row[0])


def list_workflow_ids(dsn: str | None = None) -> list[str]:
    sql = "SELECT workflow_id FROM maestro_workflows ORDER BY registered_at"
    with psycopg.connect(dsn or _dsn()) as conn:
        rows = conn.execute(sql).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Run persistence
# ---------------------------------------------------------------------------


def save_run(run_id: str, instance: RunInstance, dsn: str | None = None) -> None:
    sql = """
        INSERT INTO maestro_workflow_runs (run_id, workflow_id, instance, status)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (run_id) DO UPDATE
            SET instance   = EXCLUDED.instance,
                status     = EXCLUDED.status,
                updated_at = now()
    """
    with psycopg.connect(dsn or _dsn(), autocommit=True) as conn:
        conn.execute(
            sql,
            (
                run_id,
                instance.workflow_id,
                instance.model_dump_json(),
                instance.status.value,
            ),
        )


def load_run(run_id: str, dsn: str | None = None) -> RunInstance | None:
    sql = "SELECT instance FROM maestro_workflow_runs WHERE run_id = %s"
    with psycopg.connect(dsn or _dsn()) as conn:
        row = conn.execute(sql, (run_id,)).fetchone()
    if row is None:
        return None
    # psycopg returns JSONB columns as dicts, not raw JSON strings
    return RunInstance.model_validate(row[0])


# ---------------------------------------------------------------------------
# Step run persistence
# ---------------------------------------------------------------------------


def upsert_step_run(run_id: str, result: StepRunResult, dsn: str | None = None) -> None:
    sql = """
        INSERT INTO maestro_step_runs
            (run_id, step_id, step_attempt, status, start_time, end_time,
             duration_ms, row_count, error, output_rows)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, step_id, step_attempt) DO UPDATE
            SET status      = EXCLUDED.status,
                start_time  = EXCLUDED.start_time,
                end_time    = EXCLUDED.end_time,
                duration_ms = EXCLUDED.duration_ms,
                row_count   = EXCLUDED.row_count,
                error       = EXCLUDED.error,
                output_rows = EXCLUDED.output_rows,
                updated_at  = now()
    """
    output_json: str | None = None
    if result.output_rows is not None:
        output_json = json.dumps(result.output_rows)

    with psycopg.connect(dsn or _dsn(), autocommit=True) as conn:
        conn.execute(
            sql,
            (
                run_id,
                result.step_id,
                result.step_attempt_id,
                result.status.value,
                result.start_time,
                result.end_time,
                result.duration_ms,
                result.row_count,
                result.error,
                output_json,
            ),
        )


def load_step_runs(run_id: str, dsn: str | None = None) -> list[dict[str, Any]]:
    sql = """
        SELECT step_id, step_attempt, status, start_time, end_time,
               duration_ms, row_count, error, output_rows
        FROM maestro_step_runs
        WHERE run_id = %s
        ORDER BY id
    """
    with psycopg.connect(dsn or _dsn()) as conn:
        rows = conn.execute(sql, (run_id,)).fetchall()
    results = []
    for r in rows:
        results.append(
            {
                "step_id": r[0],
                "step_attempt": r[1],
                "status": r[2],
                "start_time": r[3].isoformat() if r[3] else None,
                "end_time": r[4].isoformat() if r[4] else None,
                "duration_ms": r[5],
                "row_count": r[6],
                "error": r[7],
                "output_rows": r[8],
            }
        )
    return results
