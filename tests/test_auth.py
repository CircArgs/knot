"""Auth tests — user CRUD, bootstrap_admin_if_empty, token lookup.

These tests operate against the running postgres stack.  The `auth_db`
fixture wipes the users table before each test so state is hermetic.

FastAPI-level auth dep tests (require_user / require_admin) use
TestClient and override KNOT_AUTH_DEV_MODE as needed.
"""

import os

import pytest
import psycopg
from fastapi.testclient import TestClient

from knot import db
from knot.db import users
from knot.db.users import (
    PrincipalKind,
    User,
    bootstrap_admin_if_empty,
    create_user,
    delete_user,
    find_by_key_hash,
    get_user,
    hash_key,
    rotate_key,
)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def auth_db(pg_conn):
    """Wipe users table only — leave spec_revisions etc. intact."""
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    yield pg_conn


# ---------------------------------------------------------------------------
# 1. User CRUD
# ---------------------------------------------------------------------------

def test_create_user_returns_user_and_raw_key(auth_db):
    user, raw_key = create_user(auth_db, username="alice")
    assert isinstance(user, User)
    assert user.username == "alice"
    assert isinstance(raw_key, str)
    assert len(raw_key) > 20  # 43 chars urlsafe base64


def test_create_user_raw_key_not_stored_plaintext(auth_db):
    _, raw_key = create_user(auth_db, username="bob")
    row = auth_db.execute(
        "SELECT api_key_hash FROM users WHERE username = 'bob'"
    ).fetchone()
    assert row[0] != raw_key  # hash stored, not raw


def test_create_user_hash_is_sha256_of_raw_key(auth_db):
    _, raw_key = create_user(auth_db, username="carol")
    expected_hash = hash_key(raw_key)
    row = auth_db.execute(
        "SELECT api_key_hash FROM users WHERE username = 'carol'"
    ).fetchone()
    assert row[0] == expected_hash


def test_find_by_key_hash_returns_user(auth_db):
    _, raw_key = create_user(auth_db, username="dave")
    found = find_by_key_hash(auth_db, hash_key(raw_key))
    assert found is not None
    assert found.username == "dave"


def test_find_by_key_hash_returns_none_for_unknown(auth_db):
    result = find_by_key_hash(auth_db, "a" * 64)
    assert result is None


def test_get_user_returns_user(auth_db):
    create_user(auth_db, username="eve")
    found = get_user(auth_db, "eve")
    assert found is not None
    assert found.username == "eve"


def test_get_user_returns_none_for_unknown(auth_db):
    assert get_user(auth_db, "no_such_user") is None


def test_create_user_records_created_by(auth_db):
    create_user(auth_db, username="admin_user", is_admin=True)
    create_user(auth_db, username="regular", created_by="admin_user")
    regular = get_user(auth_db, "regular")
    assert regular.created_by == "admin_user"


def test_create_admin_user_is_admin(auth_db):
    user, _ = create_user(auth_db, username="superuser", is_admin=True)
    assert user.is_admin is True


def test_rotate_key_returns_new_raw_key(auth_db):
    _, old_raw = create_user(auth_db, username="rotate_me")
    new_raw = rotate_key(auth_db, username="rotate_me")
    assert new_raw != old_raw
    # New key works
    found = find_by_key_hash(auth_db, hash_key(new_raw))
    assert found is not None
    # Old key no longer works
    assert find_by_key_hash(auth_db, hash_key(old_raw)) is None


def test_delete_user_removes_row(auth_db):
    create_user(auth_db, username="to_delete")
    deleted = delete_user(auth_db, username="to_delete")
    assert deleted is True
    assert get_user(auth_db, "to_delete") is None


def test_delete_unknown_user_returns_false(auth_db):
    assert delete_user(auth_db, username="ghost") is False


# ---------------------------------------------------------------------------
# 2. bootstrap_admin_if_empty
# ---------------------------------------------------------------------------

def test_bootstrap_admin_if_empty_inserts_admin_when_table_empty(auth_db):
    result = bootstrap_admin_if_empty(auth_db, "secret_key_123")
    assert result is True
    admin = get_user(auth_db, "admin")
    assert admin is not None
    assert admin.is_admin is True


def test_bootstrap_admin_if_empty_is_idempotent(auth_db):
    bootstrap_admin_if_empty(auth_db, "key1")
    # Second call with any key when a user exists should return False
    result = bootstrap_admin_if_empty(auth_db, "key2")
    assert result is False


def test_bootstrap_admin_key_works_for_auth(auth_db):
    raw_key = "test_bootstrap_key_abc123"
    bootstrap_admin_if_empty(auth_db, raw_key)
    found = find_by_key_hash(auth_db, hash_key(raw_key))
    assert found is not None
    assert found.username == "admin"


def test_bootstrap_admin_does_not_insert_when_users_exist(auth_db):
    create_user(auth_db, username="existing")
    inserted = bootstrap_admin_if_empty(auth_db, "any_key")
    assert not inserted
    # No "admin" row was created
    assert get_user(auth_db, "admin") is None


# ---------------------------------------------------------------------------
# 3. FastAPI auth dep — tested via /auth/me which gates on require_user
#
# KNOT_DEV_MODE=1 is set globally in conftest, so the live require_user
# always returns the dev principal without touching the DB.
#
# For tests that must exercise the real auth paths (missing/wrong/correct
# token) we use FastAPI's dependency_overrides to substitute a
# strict_require_user that ignores dev mode.  The override key must be
# the exact same require_user function object that the router imported.
#
# We use /auth/me because it depends directly on require_user as a
# parameter (not a router-level dependency), making the override reliable.
# ---------------------------------------------------------------------------

from knot.security import require_user as _require_user  # module-level for override key
from knot.security import Principal as _Principal


def _make_strict_require_user():
    """Return a require_user replacement that always enforces the Bearer token.

    Uses `str | None` annotation (not Optional[str] via ForwardRef) to avoid
    the Pydantic TypeAdapter ForwardRef resolution error inside local functions.
    """
    from knot.security import _strip_bearer
    from fastapi import HTTPException, Header

    def strict_require_user(
        authorization: str | None = Header(default=None),
    ) -> _Principal:
        token = _strip_bearer(authorization)
        key_hash = users.hash_key(token)
        with db.connect() as conn:
            user = users.find_by_key_hash(conn, key_hash)
        if user is None:
            raise HTTPException(403, "Invalid bearer token")
        return _Principal(username=user.username, is_admin=user.is_admin)

    return strict_require_user


def test_dev_mode_auth_me_returns_dev_principal():
    """When KNOT_AUTH_DEV_MODE=1 is set, /auth/me returns the dev principal."""
    from knot.api.main import app

    # Override require_user to return the dev principal unconditionally,
    # since KNOT_AUTH_DEV_MODE is NOT set in conftest (only KNOT_DEV_MODE is).
    def dev_require_user() -> _Principal:
        return _Principal(username="dev:default", is_admin=False)

    app.dependency_overrides[_require_user] = dev_require_user
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["username"] == "dev:default"
    finally:
        app.dependency_overrides.pop(_require_user, None)


def test_no_token_returns_401_when_auth_enforced():
    """Missing Authorization header → 401."""
    from knot.api.main import app
    app.dependency_overrides[_require_user] = _make_strict_require_user()
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/auth/me")
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.pop(_require_user, None)


def test_wrong_token_returns_403_when_auth_enforced(auth_db):
    """Unrecognised token → 403."""
    from knot.api.main import app
    app.dependency_overrides[_require_user] = _make_strict_require_user()
    try:
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/auth/me", headers={"Authorization": "Bearer totally_wrong_xyz"})
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.pop(_require_user, None)


def test_correct_token_returns_200_when_auth_enforced(auth_db):
    """Valid token → 200 and correct username in body."""
    _, raw_key = create_user(auth_db, username="api_user")
    from knot.api.main import app
    app.dependency_overrides[_require_user] = _make_strict_require_user()
    try:
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.get("/auth/me", headers={"Authorization": f"Bearer {raw_key}"})
        assert resp.status_code == 200
        assert resp.json()["username"] == "api_user"
    finally:
        app.dependency_overrides.pop(_require_user, None)
