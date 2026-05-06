"""Integration-test conftest — override the session autouse fixture that requires postgres.

The session-scoped _apply_control_schema in tests/conftest.py tries to connect
to postgres. Integration tests that don't need a live stack (pure compiler tests)
shadow it here with a no-op so they can run without docker.

Tests that DO require docker (env fixture) will still fail on CI without the stack —
that's expected; they're guarded by xfail or skipped separately.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _apply_control_schema():
    """No-op override — compiler integration tests don't need the control schema."""
    return
