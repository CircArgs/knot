"""Single source of truth for runtime configuration.

Knot's config surface is small under the API-first pivot:
  - ``KNOT_CONTROL_DSN`` — postgres DSN for control + data plane (required).
  - ``KNOT_API_BEARER``  — bearer token for mutation routes (optional;
                           see ``knot.auth``).

A development default DSN is provided when ``KNOT_DEV_MODE=1``; otherwise
the service refuses to start without the env var, to avoid silently
booting against a known-credential database.
"""

from __future__ import annotations

import os


_DEV_DEFAULT_DSN = "postgresql://knot:knot@localhost:5432/knot_control"


def get_dsn() -> str:
    """Postgres DSN for both control plane (``public``) and data plane (``knot_data``).

    Fails closed when ``KNOT_CONTROL_DSN`` is unset *unless* ``KNOT_DEV_MODE=1``
    is also set (opt-in dev fallback to the local docker-compose creds).
    """
    dsn = os.environ.get("KNOT_CONTROL_DSN")
    if dsn:
        return dsn
    if os.environ.get("KNOT_DEV_MODE") == "1":
        return _DEV_DEFAULT_DSN
    raise RuntimeError(
        "KNOT_CONTROL_DSN is not set. Set the env var, or set "
        "KNOT_DEV_MODE=1 to fall back to the dev docker-compose default."
    )
