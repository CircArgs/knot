import pytest
import psycopg
from neo4j import GraphDatabase


@pytest.fixture(scope="session")
def postgres_dsn():
    return "postgresql://knot:knot@localhost:5432/knot_control"


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
