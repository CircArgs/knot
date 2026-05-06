"""Integration tests for Neo4jPublisher.materialize().

Requires a live Neo4j instance at bolt://localhost:7687 (neo4j/knottest).
Uses the neo4j_driver session-scoped fixture from tests/conftest.py.

Each test cleans up the neo4j database before running so tests are
independent regardless of execution order.

Note: nodes/edges are passed as lists of (cls/slot, table) pairs because
OntologyClass is a Pydantic model without __hash__ and cannot be a dict key.
DerivedSlot (= Slot) defines __hash__ = id(self) so it IS hashable, but we
use the same list-of-pairs shape for consistency.
"""

from __future__ import annotations

import pyarrow as pa

from tests.fixtures.B2.impls.neo4j_publisher import Neo4jConfig, Neo4jPublisher
from tests.fixtures.B2.spec import Movie, Person, movie_director
from knot.protocols import MaterializeResult


class _TestConfig(Neo4jConfig):
    """Use the default 'neo4j' database — Community Edition has only one user db."""

    database: str = "neo4j"  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean_db(neo4j_driver, database: str) -> None:
    """Wipe all nodes/rels in the target database before each test."""
    with neo4j_driver.session(database=database) as session:
        session.run("MATCH (n) DETACH DELETE n")


def _make_publisher() -> tuple[Neo4jPublisher, _TestConfig]:
    return Neo4jPublisher(), _TestConfig()


def _movie_table(n: int = 5) -> pa.Table:
    return pa.table({
        "canonical_id": [f"movie_{i}" for i in range(n)],
        "title": [f"Movie {i}" for i in range(n)],
        "year": [2000 + i for i in range(n)],
    })


def _person_table(n: int = 3) -> pa.Table:
    return pa.table({
        "canonical_id": [f"person_{i}" for i in range(n)],
        "name": [f"Person {i}" for i in range(n)],
    })


def _director_edge_table() -> pa.Table:
    """movie_0 → person_0, movie_1 → person_1."""
    return pa.table({
        "src_id": ["movie_0", "movie_1"],
        "dst_id": ["person_0", "person_1"],
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_publish_movie_nodes(neo4j_driver):
    """Five Movie rows → five :Movie nodes in Neo4j."""
    pub, ctx = _make_publisher()
    _clean_db(neo4j_driver, ctx.database)

    nodes = [(Movie, _movie_table(5))]
    edges = []

    pub.materialize(ctx, nodes, edges)

    with neo4j_driver.session(database=ctx.database) as session:
        count = session.run("MATCH (m:Movie) RETURN count(m) AS cnt").single()["cnt"]

    assert count == 5


def test_publish_with_edges(neo4j_driver):
    """Node tables + edge table → expected relationships exist in Neo4j."""
    pub, ctx = _make_publisher()
    _clean_db(neo4j_driver, ctx.database)

    nodes = [(Movie, _movie_table(3)), (Person, _person_table(3))]
    edges = [(movie_director, _director_edge_table())]

    pub.materialize(ctx, nodes, edges)

    with neo4j_driver.session(database=ctx.database) as session:
        count = session.run(
            "MATCH (m:Movie)-[:DIRECTOR]->(p:Person) RETURN count(*) AS cnt"
        ).single()["cnt"]

    assert count == 2


def test_publish_idempotent(neo4j_driver):
    """Calling materialize twice with same data does not duplicate nodes (MERGE)."""
    pub, ctx = _make_publisher()
    _clean_db(neo4j_driver, ctx.database)

    nodes = [(Movie, _movie_table(5))]
    edges = []

    pub.materialize(ctx, nodes, edges)
    pub.materialize(ctx, nodes, edges)

    with neo4j_driver.session(database=ctx.database) as session:
        count = session.run("MATCH (m:Movie) RETURN count(m) AS cnt").single()["cnt"]

    assert count == 5


def test_publish_returns_typed_result(neo4j_driver):
    """materialize() returns a typed MaterializeResult, not an ad-hoc dict."""
    pub, ctx = _make_publisher()
    _clean_db(neo4j_driver, ctx.database)

    result = pub.materialize(ctx, [(Movie, _movie_table(1))], [])

    assert isinstance(result, MaterializeResult)
    assert result.target == ctx.database
    assert result.status == "succeeded"
    assert result.watermark is None


def test_publish_writes_knot_run_node(neo4j_driver):
    """materialize() writes a :KnotRun audit node on each call."""
    pub, ctx = _make_publisher()
    _clean_db(neo4j_driver, ctx.database)

    pub.materialize(ctx, [(Movie, _movie_table(2))], [])

    with neo4j_driver.session(database=ctx.database) as session:
        count = session.run(
            "MATCH (r:KnotRun) RETURN count(r) AS cnt"
        ).single()["cnt"]

    assert count >= 1
