"""Smoke tests for TestEnv.

Three tests work today; one is xfail pending knot core.
"""

import pytest


def test_env_lifecycle(env):
    """Verify env can reset and teardown without a scenario loaded."""
    env.reset()
    # No scenario loaded; basic methods should still work.


def test_cypher_works_now(env):
    """neo4j is reachable via env.cypher; this works today."""
    result = env.cypher("RETURN 1 AS x")
    assert result == [{"x": 1}]


def test_expected_facts_loads(env):
    """env.expected_facts() returns parsed YAML for the loaded scenario."""
    env.set_scenario("A1")
    facts = env.expected_facts()
    assert "movies" in facts


@pytest.mark.xfail(reason="requires knot core", strict=True)
def test_run_full_pipeline_xfail(env):
    """Demonstrates an integration test ready to pass once knot core lands."""
    env.set_scenario("A1")
    env.run_full_pipeline()
