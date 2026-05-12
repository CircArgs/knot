"""Integration-test fixtures: real postgres via docker-compose."""

import psycopg
import pytest
import pytest_asyncio

from knot import db
from knot.config.config import get_dsn


@pytest.fixture(scope="session")
def postgres_dsn():
    return get_dsn()


@pytest.fixture(scope="session", autouse=True)
def _apply_control_schema(postgres_dsn):
    """Ensure the control-plane schema exists before any test touches postgres.

    Mirrors the API lifespan handler in ``knot.api.main``. Individual reset
    fixtures that truncate ``spec_revisions`` start with a clean slate.
    """
    import asyncio

    async def _setup() -> None:
        await db.apply_schema()

    asyncio.run(_setup())


@pytest_asyncio.fixture
async def pg_conn(postgres_dsn):
    conn = await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True)
    yield conn
    await conn.close()


def pytest_collection_modifyitems(config, items):
    """Auto-mark every test under tests/integration/ as 'integration'."""
    for item in items:
        if "tests/integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
