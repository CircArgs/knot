"""Postgres connection helpers.

Sync psycopg3 — knot is sync, activities are sync, simpler is better.
We open a fresh connection per activity invocation (Temporal activities
are short-lived; pooling can come later).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row


@contextmanager
def connect(dsn: str, *, autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Yield a psycopg3 connection. dict_row by default so resolver outputs
    are already {col: value} dicts (matches knot_graphql's contract)."""
    conn = psycopg.connect(dsn, autocommit=autocommit, row_factory=dict_row)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def execute(dsn: str, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
    """Run one SELECT and return the rows. autocommit since pure read."""
    with connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        return list(cur.fetchall())


def execute_many(dsn: str, statements: list[str]) -> None:
    """Run a sequence of DDL/DML statements in one transaction."""
    with connect(dsn) as conn, conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)


def jsonb_param(value: Any) -> str:
    """psycopg3 binds jsonb from a Python object directly, but knot's
    write templates use `%(rows)s::jsonb` which expects a serialized
    string. json.dumps on the host is the contract."""
    return json.dumps(value)
