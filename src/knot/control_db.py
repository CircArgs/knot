from pathlib import Path

import psycopg

SCHEMA_SQL = (Path(__file__).parent / "control_schema.sql").read_text()


def apply_schema(dsn: str) -> None:
    """Apply the control-plane schema. Idempotent (CREATE TABLE IF NOT EXISTS)."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(SCHEMA_SQL)
