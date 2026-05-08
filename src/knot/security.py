"""API authentication — postgres-backed user accounts + dev-mode bypass.

The pivot tagline is "API is the only door"; this module is the lock.

Two env knobs:

  - ``KNOT_AUTH_DEV_MODE=1``      — skip auth entirely; principal returned
                                    is ``"dev:default"``. For local dev /
                                    notebooks / CI only.
  - ``KNOT_BOOTSTRAP_ADMIN_KEY``  — at startup, if no users exist, seed one
                                    named ``"admin"`` with this raw key
                                    (sha256 hashed at rest).

Principal flow:
  - ``Authorization: Bearer <api_key>`` is hashed (SHA-256 hex) and looked
    up against ``users.api_key_hash``. Match → username is the principal.
  - Mismatch / missing → 401 / 403.
  - In dev mode the dep skips the lookup and returns ``dev:default``.

Mutation handlers depend on ``require_user`` and use the returned principal
as ``applied_by`` for audit rows; self-reported ``applied_by`` in request
bodies is no longer accepted.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Header

from knot import db
from knot.config import get_settings
from knot.db import users


DEV_PRINCIPAL = "dev:default"


@dataclass(frozen=True)
class Principal:
    username: str
    is_admin: bool

    def __str__(self) -> str:  # for audit-log payloads
        return self.username


def _strip_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer token required")
    return authorization[len("Bearer "):]


def require_user(
    authorization: str | None = Header(default=None),
) -> Principal:
    """FastAPI dependency for any authenticated route.

    Returns the principal (username + is_admin). 401 on missing token,
    403 on no-such-user. In dev mode returns a synthetic non-admin
    principal without touching the DB.
    """
    if get_settings().auth_dev_mode:
        return Principal(username=DEV_PRINCIPAL, is_admin=False)
    token = _strip_bearer(authorization)
    key_hash = users.hash_key(token)
    with db.connect() as conn:
        user = users.find_by_key_hash(conn, key_hash)
    if user is None:
        raise HTTPException(403, "Invalid bearer token")
    return Principal(username=user.username, is_admin=user.is_admin)


def require_admin(principal: Principal = Depends(require_user)) -> Principal:
    """Stricter dep: 403 unless principal.is_admin is True. Dev mode is
    NOT auto-admin — admin endpoints are gated even in dev so the check
    code path runs the same way."""
    if not principal.is_admin:
        raise HTTPException(403, "Admin privileges required")
    return principal


def bootstrap_admin_from_env() -> None:
    """If ``KNOT_BOOTSTRAP_ADMIN_KEY`` is set and the users table is empty,
    seed an ``admin`` user with that key. Called at app startup after
    ``apply_schema``."""
    raw = get_settings().bootstrap_admin_key
    if not raw:
        return
    with db.connect() as conn:
        users.bootstrap_admin_if_empty(conn, raw)
