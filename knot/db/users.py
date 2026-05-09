"""User account CRUD for API-key auth.

Each user has a username and a 32-byte random bearer token; the *hash*
of the token is stored, never the raw value. Hash is SHA-256 hex
(64 chars) — fast lookup, no per-request bcrypt. Tokens are returned
exactly once at creation.

Audit fields (DataJunction-shaped, simplified):
  - ``kind``         'user' | 'service_account' — distinguishes humans from bots
  - ``email``        nullable
  - ``display_name`` nullable
  - ``created_by``   nullable FK to users(username)

Admin-only mutation surface:
  - ``create_user``    issues a new token; returns ``(User, raw_key)``
  - ``rotate_key``     replaces a user's hash; returns the new raw key
  - ``delete_user``    drops the row.

Auth dep just calls ``find_by_key_hash``.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import psycopg


class PrincipalKind(StrEnum):
    USER = "user"
    SERVICE_ACCOUNT = "service_account"


@dataclass(frozen=True)
class User:
    username: str
    is_admin: bool
    kind: PrincipalKind = PrincipalKind.USER
    email: str | None = None
    display_name: str | None = None
    created_by: str | None = None


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> str:
    """43-char base64 token (32 bytes of entropy)."""
    return secrets.token_urlsafe(32)


_USER_COLS = "username, is_admin, kind, email, display_name, created_by"


def _row_to_user(row: tuple) -> User:
    return User(
        username=row[0],
        is_admin=row[1],
        kind=PrincipalKind(row[2]),
        email=row[3],
        display_name=row[4],
        created_by=row[5],
    )


async def find_by_key_hash(conn: psycopg.AsyncConnection, key_hash: str) -> User | None:
    row = await (
        await conn.execute(
            f"SELECT {_USER_COLS} FROM users WHERE api_key_hash = %s",
            (key_hash,),
        )
    ).fetchone()
    return _row_to_user(row) if row else None


async def get_user(conn: psycopg.AsyncConnection, username: str) -> User | None:
    row = await (
        await conn.execute(
            f"SELECT {_USER_COLS} FROM users WHERE username = %s",
            (username,),
        )
    ).fetchone()
    return _row_to_user(row) if row else None


async def list_users(conn: psycopg.AsyncConnection) -> list[dict[str, Any]]:
    rows = await (
        await conn.execute(
            "SELECT username, is_admin, kind, email, display_name, "
            "       created_by, created_at "
            "FROM users ORDER BY username"
        )
    ).fetchall()
    return [
        {
            "username": r[0],
            "is_admin": r[1],
            "kind": r[2],
            "email": r[3],
            "display_name": r[4],
            "created_by": r[5],
            "created_at": r[6].isoformat(),
        }
        for r in rows
    ]


async def create_user(
    conn: psycopg.AsyncConnection,
    *,
    username: str,
    is_admin: bool = False,
    kind: PrincipalKind = PrincipalKind.USER,
    email: str | None = None,
    display_name: str | None = None,
    created_by: str | None = None,
) -> tuple[User, str]:
    """Insert a new user; returns (User, raw_api_key). The raw key is shown
    only here — store it client-side."""
    raw = generate_key()
    await conn.execute(
        "INSERT INTO users "
        "(username, api_key_hash, is_admin, kind, email, display_name, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (username, hash_key(raw), is_admin, kind.value, email, display_name, created_by),
    )
    return (
        User(
            username=username,
            is_admin=is_admin,
            kind=kind,
            email=email,
            display_name=display_name,
            created_by=created_by,
        ),
        raw,
    )


async def rotate_key(conn: psycopg.AsyncConnection, *, username: str) -> str:
    raw = generate_key()
    cur = await conn.execute(
        "UPDATE users SET api_key_hash = %s WHERE username = %s",
        (hash_key(raw), username),
    )
    if cur.rowcount == 0:
        raise KeyError(f"User {username!r} not found")
    return raw


async def delete_user(conn: psycopg.AsyncConnection, *, username: str) -> bool:
    cur = await conn.execute("DELETE FROM users WHERE username = %s", (username,))
    return cur.rowcount > 0


async def bootstrap_admin_if_empty(
    conn: psycopg.AsyncConnection,
    raw_admin_key: str,
    *,
    username: str = "admin",
) -> bool:
    """Seed an admin user if there are no users yet. Idempotent: returns
    True if it inserted, False if a user already existed."""
    existing = await (
        await conn.execute("SELECT 1 FROM users LIMIT 1")
    ).fetchone()
    if existing is not None:
        return False
    await conn.execute(
        "INSERT INTO users "
        "(username, api_key_hash, is_admin, kind, display_name) "
        "VALUES (%s, %s, TRUE, 'user', %s)",
        (username, hash_key(raw_admin_key), "Bootstrap admin"),
    )
    return True
