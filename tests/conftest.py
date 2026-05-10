"""Top-level test configuration.

Sets ``KNOT_DEV_MODE=1`` before ``knot.config`` is imported so the
fail-closed DSN guard falls back to the local docker-compose default.
Both unit and integration layers need this — unit tests import knot
modules that touch ``knot.config`` at import time.

Postgres setup (``pg_conn``, schema bootstrap) lives in
``tests/integration/conftest.py``; unit tests must not trigger it.
"""

import os

os.environ.setdefault("KNOT_DEV_MODE", "1")
