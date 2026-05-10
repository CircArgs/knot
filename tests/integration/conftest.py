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
    """Ensure the control-plane schema exists and the base spec is bootstrapped
    before any test touches postgres.

    Mirrors the API lifespan handler in ``knot.api.main`` so test runs see the
    same baseline as production startup. Individual reset fixtures that
    truncate ``spec_revisions`` will wipe the base spec; tests that need it
    can re-bootstrap explicitly.
    """
    import asyncio

    from knot.graph import spec as graph_spec

    async def _bootstrap() -> None:
        await db.apply_schema()
        async with db.connect() as conn:
            await graph_spec.bootstrap_base_spec(conn)

    asyncio.run(_bootstrap())


@pytest_asyncio.fixture
async def pg_conn(postgres_dsn):
    conn = await psycopg.AsyncConnection.connect(postgres_dsn, autocommit=True)
    yield conn
    await conn.close()
