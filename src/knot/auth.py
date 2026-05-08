"""API authentication — env-driven bearer-token dependency.

The pivot tagline is "API is the only door." This module is the lock.

Single env knob: ``KNOT_API_BEARER`` (read fresh on every request so it
can be rotated without a restart). When unset, mutation routes are
unauthenticated — explicit single-team / private-network deployment
posture, not a default we'd recommend in production. When set, every
mutation route requires ``Authorization: Bearer <token>`` to match.

``applied_by`` on mutation requests should ideally come from auth
context rather than self-reported in the body — see the open meta-question
on identity attribution. For today: when ``KNOT_API_BEARER`` is set, we
treat the bearer as the principal (returned as ``"bearer:<sha8>"`` to
avoid logging the token verbatim).
"""

from __future__ import annotations

import hashlib
import os
from typing import Optional

from fastapi import Header, HTTPException


def _expected_bearer() -> Optional[str]:
    return os.environ.get("KNOT_API_BEARER")


def _principal_for_token(token: str) -> str:
    short = hashlib.sha256(token.encode("utf-8")).hexdigest()[:8]
    return f"bearer:{short}"


def require_bearer(
    authorization: Optional[str] = Header(default=None),
) -> Optional[str]:
    """FastAPI dependency for mutation routes.

    Returns the principal string ('bearer:<sha8>') when a token is
    configured + matched, ``None`` when unauthenticated mode is
    explicitly enabled (env unset). Raises 401/403 on failure.
    """
    expected = _expected_bearer()
    if expected is None:
        return None  # explicit no-auth mode
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    token = authorization[len("Bearer "):]
    if token != expected:
        raise HTTPException(403, "Invalid bearer token")
    return _principal_for_token(token)
