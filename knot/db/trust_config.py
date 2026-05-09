"""Per-source trust scores stored in postgres-control.

Trust scores are runtime-editable config — not part of the spec graph,
not draft → publish. They drive query-time resolution via
``knot.db.resolve``. Sources without a row are treated as the table
default (0.5 = neutral).
"""

from __future__ import annotations

import psycopg

DEFAULT_TRUST = 0.5


async def get_score(conn: psycopg.AsyncConnection, source_name: str) -> float:
    """Trust score for a source; falls back to ``DEFAULT_TRUST`` if unset."""
    row = await (
        await conn.execute(
            "SELECT trust_score FROM trust_config WHERE source_name = %s",
            (source_name,),
        )
    ).fetchone()
    return row[0] if row else DEFAULT_TRUST


async def set_score(conn: psycopg.AsyncConnection, source_name: str, score: float) -> None:
    """Upsert a per-source trust score."""
    await conn.execute(
        "INSERT INTO trust_config (source_name, trust_score) VALUES (%s, %s) "
        "ON CONFLICT (source_name) DO UPDATE "
        "SET trust_score = EXCLUDED.trust_score, updated_at = now()",
        (source_name, score),
    )


async def list_scores(conn: psycopg.AsyncConnection) -> dict[str, float]:
    """All configured trust scores as ``{source_name: trust_score}``.

    Sources without a row are absent from this dict (caller substitutes
    ``DEFAULT_TRUST``).
    """
    rows = await (
        await conn.execute("SELECT source_name, trust_score FROM trust_config")
    ).fetchall()
    return {r[0]: r[1] for r in rows}


async def delete_score(conn: psycopg.AsyncConnection, source_name: str) -> bool:
    """Drop a per-source trust override (reverts to ``DEFAULT_TRUST``)."""
    cur = await conn.execute(
        "DELETE FROM trust_config WHERE source_name = %s",
        (source_name,),
    )
    return cur.rowcount > 0
