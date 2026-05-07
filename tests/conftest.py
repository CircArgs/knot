import pytest
import psycopg
from neo4j import GraphDatabase

from tests.test_env import TestEnv
from knot import db
from knot.config import get_dsn


@pytest.fixture(scope="session")
def postgres_dsn():
    return get_dsn()


@pytest.fixture(scope="session", autouse=True)
def _apply_control_schema(postgres_dsn):
    """Ensure the control-plane schema exists before any test that touches postgres."""
    db.apply_schema()


@pytest.fixture(scope="session")
def neo4j_uri():
    return "bolt://localhost:7687"


@pytest.fixture
def pg_conn(postgres_dsn):
    conn = psycopg.connect(postgres_dsn, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture(scope="session")
def neo4j_driver(neo4j_uri):
    driver = GraphDatabase.driver(neo4j_uri, auth=("neo4j", "knottest"))
    yield driver
    driver.close()


@pytest.fixture
def lake_dir(tmp_path):
    return tmp_path / "lake"


@pytest.fixture
def env(postgres_dsn, neo4j_uri, lake_dir, request):
    e = TestEnv(postgres_dsn, neo4j_uri, ("neo4j", "knottest"), lake_dir)
    yield e
    e.teardown()
