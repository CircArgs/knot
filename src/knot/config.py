"""Single source of truth for runtime configuration.

Knot's config surface is small (single-team trust posture, commitment 5):
one postgres DSN. Everything else comes from the spec or a bound impl's
``Config`` (when bound impls return).
"""

from __future__ import annotations

import os


_DEFAULT_DSN = "postgresql://knot:knot@localhost:5432/knot_control"


def get_dsn() -> str:
    """Postgres DSN for both control plane (``public``) and data plane (``knot_data``)."""
    return os.environ.get("KNOT_CONTROL_DSN", _DEFAULT_DSN)
