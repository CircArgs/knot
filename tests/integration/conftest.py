"""Fixtures for integration tests against the live ``knot-postgres``
compose service.

Each test gets a fresh schema named ``knot_test_<uuid_hex>`` so tests
don't interfere with each other; the schema is dropped CASCADE on
teardown. Connection params default to the compose service:

  - host: localhost
  - port: 5433
  - user/pw/db: knot/knot/knot

Override via ``KNOT_PG_HOST`` / ``KNOT_PG_PORT`` / ``KNOT_PG_USER`` /
``KNOT_PG_PASS`` / ``KNOT_PG_DB``.

Integration tests are skipped (not failed) if postgres can't be
reached, so the unit suite stays green when running pytest without
the compose stack up.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None  # type: ignore


def _conn_kwargs() -> dict[str, Any]:
    return {
        "host": os.environ.get("KNOT_PG_HOST", "localhost"),
        "port": int(os.environ.get("KNOT_PG_PORT", "5433")),
        "user": os.environ.get("KNOT_PG_USER", "knot"),
        "password": os.environ.get("KNOT_PG_PASS", "knot"),
        "dbname": os.environ.get("KNOT_PG_DB", "knot"),
    }


def _is_pg_available() -> bool:
    if psycopg is None:
        return False
    try:
        with psycopg.connect(**_conn_kwargs(), connect_timeout=2) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


_pg_available = _is_pg_available()


pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def _require_pg():
    if not _pg_available:
        pytest.skip(
            "integration tests need a live postgres; bring the compose "
            "service up with `docker compose up -d postgres`"
        )


@pytest.fixture()
def pg(_require_pg) -> Iterator[psycopg.Connection]:  # type: ignore[name-defined]
    """A psycopg connection in autocommit mode, scoped to the test."""
    conn = psycopg.connect(**_conn_kwargs(), autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture()
def schema(pg) -> Iterator[str]:
    """A unique throwaway schema per test, dropped on teardown."""
    name = f"knot_test_{uuid.uuid4().hex[:12]}"
    pg.execute(f"CREATE SCHEMA {name}")
    try:
        yield name
    finally:
        pg.execute(f"DROP SCHEMA IF EXISTS {name} CASCADE")


def exec_many(pg, statements: list[str]) -> None:
    """Run a list of DDL/DML statements in order against ``pg``."""
    with pg.cursor() as cur:
        for sql in statements:
            cur.execute(sql)


def exec_with_params(pg, sql: str, params: dict | list) -> None:
    """Run a single parameterized statement."""
    with pg.cursor() as cur:
        cur.execute(sql, params)


def exec_script(pg, script: str, params: dict | None = None) -> None:
    """Execute a knot multi-statement SQL script.

    psycopg's prepared-statement path rejects multi-statement scripts
    when params are bound, so split on the blank-line separator knot's
    emitters use between statements and call ``execute`` per statement.
    Each statement gets the same ``params`` dict.
    """
    with pg.cursor() as cur:
        for stmt in script.split("\n\n"):
            stmt = stmt.strip()
            if not stmt:
                continue
            cur.execute(stmt, params)
