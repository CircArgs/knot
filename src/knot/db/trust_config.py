"""Per-source trust scores stored in postgres-control.

Trust scores are runtime-editable config — not part of the spec graph,
not draft → publish. They drive query-time resolution via
``knot.db.resolve``. Sources without a row are treated as the table
default (0.5 = neutral).
"""

from __future__ import annotations

import psycopg


DEFAULT_TRUST = 0.5


def get_score(conn: psycopg.Connection, source_name: str) -> float:
    """Trust score for a source; falls back to ``DEFAULT_TRUST`` if unset."""
    row = conn.execute(
        "SELECT trust_score FROM trust_config WHERE source_name = %s",
        (source_name,),
    ).fetchone()
    return row[0] if row else DEFAULT_TRUST


def set_score(conn: psycopg.Connection, source_name: str, score: float) -> None:
    """Upsert a per-source trust score."""
    conn.execute(
        "INSERT INTO trust_config (source_name, trust_score) VALUES (%s, %s) "
        "ON CONFLICT (source_name) DO UPDATE "
        "SET trust_score = EXCLUDED.trust_score, updated_at = now()",
        (source_name, score),
    )


def list_scores(conn: psycopg.Connection) -> dict[str, float]:
    """All configured trust scores as ``{source_name: trust_score}``.

    Sources without a row are absent from this dict (caller substitutes
    ``DEFAULT_TRUST``).
    """
    rows = conn.execute(
        "SELECT source_name, trust_score FROM trust_config"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def delete_score(conn: psycopg.Connection, source_name: str) -> bool:
    """Drop a per-source trust override (reverts to ``DEFAULT_TRUST``)."""
    cur = conn.execute(
        "DELETE FROM trust_config WHERE source_name = %s",
        (source_name,),
    )
    return cur.rowcount > 0
