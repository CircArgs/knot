"""User account CRUD for API-key auth.

Each user has a username and a 32-byte random bearer token; the *hash*
of the token is stored, never the raw value. Hash is SHA-256 hex
(64 chars) — fast lookup, no per-request bcrypt. Tokens are returned
exactly once at creation.

Admin-only mutation surface:
  - ``create_user``    issues a new token; returns ``(user, raw_key)``
  - ``rotate_key``     replaces a user's hash; returns the new raw key
  - ``delete_user``    drops the row.

Auth dep just calls ``find_by_key_hash``.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any

import psycopg


@dataclass(frozen=True)
class User:
    username: str
    is_admin: bool


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    """43-char base64 token (32 bytes of entropy)."""
    return secrets.token_urlsafe(32)


def find_by_key_hash(conn: psycopg.Connection, key_hash: str) -> User | None:
    row = conn.execute(
        "SELECT username, is_admin FROM users WHERE api_key_hash = %s",
        (key_hash,),
    ).fetchone()
    if row is None:
        return None
    return User(username=row[0], is_admin=row[1])


def get_user(conn: psycopg.Connection, username: str) -> User | None:
    row = conn.execute(
        "SELECT username, is_admin FROM users WHERE username = %s",
        (username,),
    ).fetchone()
    if row is None:
        return None
    return User(username=row[0], is_admin=row[1])


def list_users(conn: psycopg.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT username, is_admin, created_at FROM users ORDER BY username"
    ).fetchall()
    return [
        {"username": r[0], "is_admin": r[1], "created_at": r[2].isoformat()}
        for r in rows
    ]


def create_user(
    conn: psycopg.Connection,
    *,
    username: str,
    is_admin: bool = False,
) -> tuple[User, str]:
    """Insert a new user; returns (User, raw_api_key). The raw key is shown
    only here — store it client-side."""
    raw = generate_key()
    conn.execute(
        "INSERT INTO users (username, api_key_hash, is_admin) "
        "VALUES (%s, %s, %s)",
        (username, hash_key(raw), is_admin),
    )
    return User(username=username, is_admin=is_admin), raw


def rotate_key(conn: psycopg.Connection, *, username: str) -> str:
    raw = generate_key()
    cur = conn.execute(
        "UPDATE users SET api_key_hash = %s WHERE username = %s",
        (hash_key(raw), username),
    )
    if cur.rowcount == 0:
        raise KeyError(f"User {username!r} not found")
    return raw


def delete_user(conn: psycopg.Connection, *, username: str) -> bool:
    cur = conn.execute("DELETE FROM users WHERE username = %s", (username,))
    return cur.rowcount > 0


def bootstrap_admin_if_empty(
    conn: psycopg.Connection,
    raw_admin_key: str,
    *,
    username: str = "admin",
) -> bool:
    """Seed an admin user if there are no users yet. Idempotent: returns
    True if it inserted, False if a user already existed."""
    existing = conn.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if existing is not None:
        return False
    conn.execute(
        "INSERT INTO users (username, api_key_hash, is_admin) "
        "VALUES (%s, %s, TRUE)",
        (username, hash_key(raw_admin_key)),
    )
    return True
