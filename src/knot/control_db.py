"""Postgres bootstrap for the spec router.

`apply_schema(dsn)` runs `control_schema.sql` against the target database.
Idempotent (CREATE … IF NOT EXISTS); safe to call on every boot.
"""

from __future__ import annotations

from pathlib import Path

import psycopg


_SCHEMA_SQL = (Path(__file__).parent / "control_schema.sql").read_text()


def apply_schema(dsn: str) -> None:
    """Apply the control-plane schema.  Idempotent."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(_SCHEMA_SQL)
