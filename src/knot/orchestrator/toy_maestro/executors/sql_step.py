"""Execute sparksql_step and trino_step steps via DuckDB.

Both sub_types are translated to DuckDB locally. The lake path is
``/tmp/knot_walkthrough_lake/`` when it exists; otherwise an in-memory
DuckDB instance is used and SQL may reference CSV files by absolute path.

Parameter substitution: ``{param_name}`` tokens in the SQL are replaced
with values from ``ctx.run_params`` before execution.

Row output is capped at 1 000 rows to keep memory bounded.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from knot.orchestrator.toy_maestro.workflow_models import (
    RunCtx,
    Step,
    StepStatus,
    StepRunResult,
)

_LAKE_PATH = Path("/tmp/knot_walkthrough_lake")
_MAX_OUTPUT_ROWS = 1000


class SqlStepError(RuntimeError):
    """Raised when a SQL step fails (bad SQL, missing table, etc.)."""


def _resolve_lake_db_path() -> str | None:
    """Return DuckDB lake path string if it exists, else None (in-memory)."""
    if _LAKE_PATH.exists():
        return str(_LAKE_PATH / "duckdb.db")
    return None


def _substitute_params(sql: str, params: dict[str, Any]) -> str:
    """Replace ``{key}`` placeholders in sql with values from params."""
    for key, value in params.items():
        sql = sql.replace(f"{{{key}}}", str(value))
    return sql


def execute_sql_step(step: Step, ctx: RunCtx) -> StepRunResult:
    """Run a sparksql or trino step via DuckDB.

    Returns a StepRunResult with SUCCEEDED or USER_FAILED status.
    Never raises — errors are captured in the result.
    """
    start_dt = datetime.now(timezone.utc)
    start_ts = time.monotonic()

    result = StepRunResult(
        step_id=step.id,
        step_attempt_id=1,
        status=StepStatus.RUNNING,
        start_time=start_dt,
    )

    # Extract SQL from step params
    sql_raw: str | None = step.params.get("sql")
    if not sql_raw:
        end_dt = datetime.now(timezone.utc)
        ms = int((time.monotonic() - start_ts) * 1000)
        result.status = StepStatus.USER_FAILED
        result.error = "Step param 'sql' is required but not provided"
        result.end_time = end_dt
        result.duration_ms = ms
        return result

    # Merge step-level params with run-level params (run params win)
    merged_params = {**step.params, **ctx.run_params}
    sql = _substitute_params(sql_raw, merged_params)

    # Connect to DuckDB
    lake_db = ctx.lake_path if ctx.lake_path else _resolve_lake_db_path()
    try:
        if lake_db:
            conn = duckdb.connect(lake_db, read_only=False)
        else:
            conn = duckdb.connect()

        try:
            rel = conn.execute(sql)
            rows_raw = rel.fetchmany(_MAX_OUTPUT_ROWS)
            col_names = [desc[0] for desc in rel.description]  # type: ignore[union-attr]
            output_rows = [dict(zip(col_names, row)) for row in rows_raw]
            row_count = len(output_rows)
        finally:
            conn.close()

        end_dt = datetime.now(timezone.utc)
        ms = int((time.monotonic() - start_ts) * 1000)
        result.status = StepStatus.SUCCEEDED
        result.end_time = end_dt
        result.duration_ms = ms
        result.row_count = row_count
        result.output_rows = output_rows

    except duckdb.Error as exc:
        end_dt = datetime.now(timezone.utc)
        ms = int((time.monotonic() - start_ts) * 1000)
        result.status = StepStatus.USER_FAILED
        result.error = str(exc)
        result.end_time = end_dt
        result.duration_ms = ms

    return result
