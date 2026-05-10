"""Unit-test configuration.

Unit tests are pure-Python with no I/O — no postgres, no network, no
filesystem writes. This conftest deliberately defines no fixtures; if a
test under ``tests/unit/`` needs a fixture, that's a signal the test
probably belongs under ``tests/integration/``.
"""

import pytest


def pytest_collection_modifyitems(config, items):
    """Auto-mark every test under tests/unit/ as 'unit'."""
    for item in items:
        if "tests/unit/" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
