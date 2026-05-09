"""Shared test fixtures."""

import os

# Set KNOT_DEV_MODE before knot.config is imported so the fail-closed DSN
# guard falls back to the local docker-compose default.
os.environ.setdefault("KNOT_DEV_MODE", "1")

import psycopg
import pytest

from knot import db
from knot.config.config import get_dsn


@pytest.fixture(scope="session")
def postgres_dsn():
    return get_dsn()


@pytest.fixture(scope="session", autouse=True)
def _apply_control_schema(postgres_dsn):
    """Ensure the control-plane schema exists before any test touches postgres."""
    db.apply_schema()


@pytest.fixture
def pg_conn(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, autocommit=True)
    yield conn
    conn.close()
