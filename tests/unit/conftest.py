"""Unit-test configuration.

Unit tests are pure-Python with no I/O — no postgres, no network, no
filesystem writes. This conftest deliberately defines no fixtures; if a
test under ``tests/unit/`` needs a fixture, that's a signal the test
probably belongs under ``tests/integration/``.
"""
