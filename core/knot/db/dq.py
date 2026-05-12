"""Data-quality observations — per-(source, class, slot) stats.

Two write paths feed the same ``public.dq_observations`` table:

  - **incremental** — every ``/graph/ingest/{source}`` batch and every
    row-creating / row-mutating correction emits one observation row per
    affected stored slot. Cheap; runs in the request thread; tagged with
    the originating batch/correction id.
  - **full_scan** — ``POST /dq/scan`` runs an aggregate query against
    every per-class table for the currently-published spec and emits one
    observation row per (source, class, slot, current state). Heavier;
    intended for periodic snapshots.

Reads are plain SQL — see ``query_observations`` and ``summarize`` for
the convenience wrappers used by the management REST router.

Centralization rule: this module is the only place SQL is written for
the dq_observations table. Same rule as everywhere else in ``db/``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

import psycopg
from psycopg import sql

from knot.spec import OntologyClass, Spec
from knot.spec import effective_slots as _effective_slots
from knot.spec import is_stored as _is_stored
from knot.spec.compile.postgres._naming import (
    schema,
    user_corrections_source,
)

# ---------------------------------------------------------------------------
# Stats computation — pure functions, no DB access
# ---------------------------------------------------------------------------


def _slot_stats(values: list[Any]) -> dict[str, Any]:
    """Compute (row_count, null_count, distinct_count, min, max) for a list.

    All min/max comparisons are tolerant: heterogeneous values fall back to
    string comparison so the stats path never raises on weird mixes.
    """
    row_count = len(values)
    non_null = [v for v in values if v is not None]
    null_count = row_count - len(non_null)
    distinct_count = len({_hash_safe(v) for v in non_null}) if non_null else 0

    if not non_null:
        return {
            "row_count": row_count,
            "null_count": null_count,
            "distinct_count": 0,
            "min_value": None,
            "max_value": None,
        }

    try:
        mn = min(non_null)
        mx = max(non_null)
    except TypeError:
        # Heterogeneous — fall back to string ordering.
        s = sorted(str(v) for v in non_null)
        mn, mx = s[0], s[-1]

    return {
        "row_count": row_count,
        "null_count": null_count,
        "distinct_count": distinct_count,
        "min_value": _stringify(mn),
        "max_value": _stringify(mx),
    }


def _hash_safe(v: Any) -> Any:
    """Return a hashable form of v: lists become tuples; rest passes through."""
    if isinstance(v, list):
        return tuple(v)
    if isinstance(v, dict):
        return tuple(sorted(v.items()))
    return v


def _stringify(v: Any) -> str:
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


# ---------------------------------------------------------------------------
# Incremental write path — called from ingest + corrections
# ---------------------------------------------------------------------------


async def record_incremental(
    conn: psycopg.AsyncConnection,
    *,
    source_name: str,
    cls: OntologyClass,
    batch_id: str | None,
    rows: list[dict[str, Any]],
    only_slots: Iterable[str] | None = None,
) -> int:
    """Compute one observation row per stored slot in the batch and insert.

    Returns the number of observation rows inserted. A zero-row batch still
    emits observations with ``row_count = 0`` — useful for "we tried but
    the batch was empty."

    If ``only_slots`` is supplied, observations are recorded only for those
    slot names — used by single-slot corrections (PropertyCorrection) where
    the dict carries one slot and the rest would falsely look 100% null.
    """
    if not rows and not _effective_slots(cls):
        return 0

    only = set(only_slots) if only_slots is not None else None

    inserted = 0
    for slot in _effective_slots(cls):
        if not _is_stored(slot):
            continue
        if only is not None and slot.name not in only:
            continue
        values = [r.get(slot.name) for r in rows]
        stats = _slot_stats(values)
        await conn.execute(
            "INSERT INTO dq_observations "
            "(source_name, class_name, slot_name, kind, batch_id, "
            " row_count, null_count, distinct_count, min_value, max_value) "
            "VALUES (%s, %s, %s, 'incremental', %s, %s, %s, %s, %s, %s)",
            (
                source_name,
                cls.name,
                slot.name,
                batch_id,
                stats["row_count"],
                stats["null_count"],
                stats["distinct_count"],
                stats["min_value"],
                stats["max_value"],
            ),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Full-scan write path — called from POST /dq/scan
# ---------------------------------------------------------------------------


async def full_scan(
    conn: psycopg.AsyncConnection,
    spec: Spec,
    *,
    source_filter: str | None = None,
    class_filter: str | None = None,
) -> int:
    """Snapshot per-(source, class, slot) stats from the current data plane.

    For each Source on ``spec``, runs one aggregate query per stored slot
    against the source's per-class table (filtered to rows where
    ``_source = source_name``) and writes a ``full_scan`` observation.

    Defined classes (no table) and abstract classes are skipped. Rows
    where ``_source = '_user_corrections'`` are also rolled up under the
    synthetic ``user_corrections_source()`` source name — gives ops a
    "what did corrections do" view alongside per-source ingest stats.

    Returns the count of observations inserted.
    """
    inserted = 0
    bindings = [
        b for b in spec.source_bindings if source_filter is None or b.source.name == source_filter
    ]
    seen_class_source: set[tuple[str, str]] = set()

    for binding in bindings:
        cls = binding.class_
        if class_filter is not None and cls.name != class_filter:
            continue
        if getattr(cls, "definition", None) is not None:
            continue  # defined-class views — skip
        if cls.abstract:
            continue
        inserted += await _full_scan_for(conn, source_name=binding.source.name, cls=cls)
        seen_class_source.add((cls.name, binding.source.name))

    # Also scan the synthetic _user_corrections source against any class
    # that has user-correction rows.
    if source_filter is None or source_filter == user_corrections_source():
        for cls in spec.classes:
            if class_filter is not None and cls.name != class_filter:
                continue
            if getattr(cls, "definition", None) is not None or cls.abstract:
                continue
            if (cls.name, user_corrections_source()) in seen_class_source:
                continue
            inserted += await _full_scan_for(
                conn,
                source_name=user_corrections_source(),
                cls=cls,
            )

    return inserted


async def _full_scan_for(
    conn: psycopg.AsyncConnection,
    *,
    source_name: str,
    cls: OntologyClass,
) -> int:
    """Aggregate stats for one (source, class) pair across every stored slot."""
    inserted = 0
    table = sql.Identifier(schema(), cls.name.lower())

    for slot in _effective_slots(cls):
        if not _is_stored(slot):
            continue
        col = sql.Identifier(slot.name)
        # COUNT(*), COUNT(*) FILTER (WHERE col IS NULL), COUNT(DISTINCT col),
        # MIN(col)::text, MAX(col)::text — single round-trip per slot.
        stmt = sql.SQL(
            "SELECT count(*) AS row_count, "
            "       count(*) FILTER (WHERE {col} IS NULL) AS null_count, "
            "       count(DISTINCT {col}) AS distinct_count, "
            "       min({col})::text AS min_value, "
            "       max({col})::text AS max_value "
            "FROM {tbl} WHERE _source = %s"
        ).format(col=col, tbl=table)
        try:
            row = await (await conn.execute(stmt, (source_name,))).fetchone()
        except psycopg.errors.UndefinedTable:
            return inserted  # class table not yet migrated
        except psycopg.errors.UndefinedColumn:
            continue  # slot column not yet added — skip silently
        if row is None:
            continue
        row_count, null_count, distinct_count, min_value, max_value = row
        if row_count == 0:
            continue  # nothing of this source/class in the table
        await conn.execute(
            "INSERT INTO dq_observations "
            "(source_name, class_name, slot_name, kind, batch_id, "
            " row_count, null_count, distinct_count, min_value, max_value) "
            "VALUES (%s, %s, %s, 'full_scan', NULL, %s, %s, %s, %s, %s)",
            (
                source_name,
                cls.name,
                slot.name,
                row_count,
                null_count,
                distinct_count,
                min_value,
                max_value,
            ),
        )
        inserted += 1
    return inserted


# ---------------------------------------------------------------------------
# Read paths — called from the management REST router
# ---------------------------------------------------------------------------


async def query_observations(
    conn: psycopg.AsyncConnection,
    *,
    source: str | None = None,
    class_name: str | None = None,
    slot: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    """Time-series read of observations with optional filters."""
    clauses: list[sql.Composable] = []
    params: list[Any] = []
    if source is not None:
        clauses.append(sql.SQL("source_name = %s"))
        params.append(source)
    if class_name is not None:
        clauses.append(sql.SQL("class_name = %s"))
        params.append(class_name)
    if slot is not None:
        clauses.append(sql.SQL("slot_name = %s"))
        params.append(slot)
    if since is not None:
        clauses.append(sql.SQL("observed_at >= %s"))
        params.append(since)
    if until is not None:
        clauses.append(sql.SQL("observed_at <= %s"))
        params.append(until)
    if kind is not None:
        clauses.append(sql.SQL("kind = %s"))
        params.append(kind)

    where = sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("TRUE")
    stmt = sql.SQL(
        "SELECT observed_at, source_name, class_name, slot_name, kind, "
        "       batch_id, row_count, null_count, distinct_count, "
        "       min_value, max_value, extra "
        "FROM dq_observations WHERE {where} "
        "ORDER BY observed_at DESC LIMIT %s"
    ).format(where=where)
    params.append(limit)

    rows = await (await conn.execute(stmt, params)).fetchall()
    return [
        {
            "observed_at": r[0].isoformat(),
            "source": r[1],
            "class": r[2],
            "slot": r[3],
            "kind": r[4],
            "batch_id": r[5],
            "row_count": r[6],
            "null_count": r[7],
            "distinct_count": r[8],
            "min_value": r[9],
            "max_value": r[10],
            "extra": r[11],
        }
        for r in rows
    ]


async def summarize(
    conn: psycopg.AsyncConnection,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    """Per-(source, class, slot) roll-up over a time window: total rows,
    total nulls, null_rate (= null_count / row_count when row_count > 0).
    """
    clauses: list[sql.Composable] = []
    params: list[Any] = []
    if since is not None:
        clauses.append(sql.SQL("observed_at >= %s"))
        params.append(since)
    if until is not None:
        clauses.append(sql.SQL("observed_at <= %s"))
        params.append(until)
    where = sql.SQL(" AND ").join(clauses) if clauses else sql.SQL("TRUE")

    stmt = sql.SQL(
        "SELECT source_name, class_name, slot_name, "
        "       sum(row_count) AS total_rows, "
        "       sum(null_count) AS total_nulls, "
        "       max(observed_at) AS last_seen "
        "FROM dq_observations WHERE {where} "
        "GROUP BY source_name, class_name, slot_name "
        "ORDER BY total_nulls DESC NULLS LAST"
    ).format(where=where)

    rows = await (await conn.execute(stmt, params)).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        total_rows = int(r[3] or 0)
        total_nulls = int(r[4] or 0)
        null_rate = (total_nulls / total_rows) if total_rows > 0 else None
        out.append(
            {
                "source": r[0],
                "class": r[1],
                "slot": r[2],
                "total_rows": total_rows,
                "total_nulls": total_nulls,
                "null_rate": null_rate,
                "last_seen": r[5].isoformat() if r[5] else None,
            }
        )
    return out
