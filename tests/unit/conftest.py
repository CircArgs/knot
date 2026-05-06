"""Unit-test conftest — override the session autouse fixture that requires postgres.

The session-scoped _apply_control_schema in tests/conftest.py tries to connect
to postgres, which isn't available in unit test runs.  We shadow it here with a
no-op so unit tests run without any docker dependency.
"""

import pytest


@pytest.fixture(scope="session", autouse=True)
def _apply_control_schema():
    """No-op override — unit tests don't need the control schema."""
    return
