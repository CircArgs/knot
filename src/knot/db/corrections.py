"""Audit-log CRUD for ``_user_corrections``.

The persistence boundary for the corrections audit log. Orchestration of
"audit row + data-plane mutation + bandit feedback" lives in
``knot.graph.corrections``; this module is pure SQL.
"""

from __future__ import annotations

import json
from typing import Any

import psycopg


def record_audit_entry(
    conn: psycopg.Connection,
    *,
    correction_type: str,
    payload: dict[str, Any],
    applied_by: str | None,
    applied_revision: int,
) -> int:
    """Insert one row into ``_user_corrections``. Returns the new row id."""
    return conn.execute(
        "INSERT INTO _user_corrections "
        "(correction_type, payload, applied_by, applied_revision) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (correction_type, json.dumps(payload), applied_by, applied_revision),
    ).fetchone()[0]


def list_audit_log(conn: psycopg.Connection, *, limit: int = 100) -> list[dict[str, Any]]:
    """Newest-first slice of the audit log."""
    rows = conn.execute(
        "SELECT id, correction_type, payload, applied_by, applied_revision, created_at "
        "FROM _user_corrections ORDER BY id DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "correction_type": r[1],
            "payload": r[2] if isinstance(r[2], dict) else json.loads(r[2]),
            "applied_by": r[3],
            "applied_revision": r[4],
            "created_at": r[5].isoformat(),
        }
        for r in rows
    ]
