"""Graph data-plane CRUD against per-class postgres tables (SCD2 bindings).

Two tables per class:
  - ``knot_data.<class>``          source rows (immutable per ingest);
                                   ``_knot_row_id`` UUID is the anchor.
  - ``knot_data.<class>_bindings`` SCD2 bindings; ``valid_to IS NULL``
                                   marks the current binding for a row.

System columns on the source-row table:
  - ``_knot_row_id``    UUID anchor (stable across re-pushes via ON CONFLICT)
  - ``_source``         source name (FK by name to published spec)
  - ``_source_row_id``  string-coerced identifier-slot value
  - ``_spec_revision``  spec_revisions.revision at ingest time (FK)
  - ``_ingest_at``      ``now()``

Reserved synthetic source name ``_user_corrections`` is the source for
user-correction rows.

Reads JOIN source × bindings WHERE valid_to IS NULL. Merge/split/correct
operations close current bindings and open new ones. ``reassign`` is
gone — replaced by ``merge_canonical_ids`` which is the SCD2-respecting
merge primitive.

All identifiers go through ``psycopg.sql.Identifier``; no f-string
interpolation of names that came from the API.
"""

from __future__ import annotations

from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from knot.spec.compile.postgres._naming import (
    bindings_table_id as _bindings_id,
    table_id as _table_id,
    user_corrections_source,
)
from knot.spec import OntologyClass, Source
from knot.spec import stored_slot_names as _stored_slot_names
from knot.spec.compile.postgres._queries import (
    select_with_binding as _select_with_binding,
)
from knot.spec.compile.postgres._queries import (
    select_with_derivations as _select_with_derivations,
)

__all__ = (
    "user_corrections_source",
    "insert_rows",
    "list_rows",
    "query_rows",
    "count_rows",
    "aggregate_rows",
    "get_canonical_contributions",
    "get_disagreeing_contributions",
    "canonical_id_exists",
    "merge_canonical_ids",
    "upsert_user_correction_row",
    "split_canonical_id",
    "insert_synthetic_row",
    "tombstone_canonical_id",
    "reject_contribution",
    "append_lineage_event",
    "list_lineage",
)


def _serialize_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


# ─── Ingest ─────────────────────────────────────────────────────────────────


async def insert_rows(
    conn: psycopg.AsyncConnection,
    *,
    source: Source,
    spec_revision: int,
    rows: list[dict[str, Any]],
    canonical_ids: list[str],
) -> int:
    """Upsert a batch of source rows. For each row:
      1. INSERT/UPDATE the source-row table (preserves _knot_row_id on conflict).
      2. INSERT a binding (canonical_id from caller, valid_to NULL,
         change_type 'ingest') iff no current binding exists for that
         knot_row_id.

    ``canonical_ids`` must be parallel to ``rows`` (same length, same order).
    Returns the number of source rows written.
    """
    cls = source.entity_class
    slot_names = _stored_slot_names(cls)
    user_cols = ["_source", "_source_row_id", "_spec_revision", *slot_names]
    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in user_cols)
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(user_cols))
    update_set = sql.SQL(", ").join(
        sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c))
        for c in user_cols
        if c not in ("_source", "_source_row_id")
    )

    upsert_stmt = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({ph}) "
        "ON CONFLICT (_source, _source_row_id) DO UPDATE "
        "SET {update_set}, _ingest_at = now() "
        "RETURNING _knot_row_id"
    ).format(
        table=_table_id(cls),
        cols=cols_sql,
        ph=placeholders,
        update_set=update_set,
    )

    binding_insert_stmt = sql.SQL(
        "INSERT INTO {bindings} "
        "(knot_row_id, canonical_id, change_type, applied_revision) "
        "SELECT %s, %s, 'ingest', %s "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM {bindings} "
        "  WHERE knot_row_id = %s AND valid_to IS NULL"
        ")"
    ).format(bindings=_bindings_id(cls))

    count = 0
    async with conn.transaction():
        for row, canonical_id in zip(rows, canonical_ids, strict=False):
            values: list[Any] = [
                source.name,
                canonical_id,
                spec_revision,
            ]
            for slot_name in slot_names:
                values.append(row.get(slot_name))
            knot_row_id = (await (await conn.execute(upsert_stmt, values)).fetchone())[0]
            await conn.execute(
                binding_insert_stmt,
                (knot_row_id, canonical_id, spec_revision, knot_row_id),
            )
            count += 1
    return count


# ─── User-correction-row upsert ─────────────────────────────────────────────


async def upsert_user_correction_row(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    slot_name: str,
    value: Any,
    spec_revision: int,
) -> str:
    """Upsert the user-correction row for a canonical_id, setting only the
    corrected slot. Other slot columns remain NULL on first insert and
    unchanged on subsequent corrections. Also opens a binding to the
    canonical_id if none is current. Returns _knot_row_id (str)."""
    slot_names = _stored_slot_names(cls)
    user_cols = ["_source", "_source_row_id", "_spec_revision", *slot_names]
    placeholder_values: list[Any] = [
        user_corrections_source(),
        canonical_id,  # _source_row_id = canonical_id at correction time
        spec_revision,
    ]
    for sn in slot_names:
        placeholder_values.append(value if sn == slot_name else None)

    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in user_cols)
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(user_cols))
    upsert_set = sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(slot_name))
    upsert_stmt = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({ph}) "
        "ON CONFLICT (_source, _source_row_id) DO UPDATE "
        "SET {upsert_set}, _spec_revision = EXCLUDED._spec_revision, "
        "_ingest_at = now() "
        "RETURNING _knot_row_id"
    ).format(
        table=_table_id(cls),
        cols=cols_sql,
        ph=placeholders,
        upsert_set=upsert_set,
    )

    binding_insert_stmt = sql.SQL(
        "INSERT INTO {bindings} "
        "(knot_row_id, canonical_id, change_type, applied_revision) "
        "SELECT %s, %s, 'correction', %s "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM {bindings} "
        "  WHERE knot_row_id = %s AND valid_to IS NULL"
        ")"
    ).format(bindings=_bindings_id(cls))

    knot_row_id = (await (await conn.execute(upsert_stmt, placeholder_values)).fetchone())[0]
    await conn.execute(
        binding_insert_stmt,
        (knot_row_id, canonical_id, spec_revision, knot_row_id),
    )
    return str(knot_row_id)


# ─── Reads (JOIN source × current bindings) ─────────────────────────────────


def _is_defined_class(cls: OntologyClass) -> bool:
    """True when the class is backed by a VIEW (defined class), not a TABLE."""
    return getattr(cls, "definition", None) is not None


async def list_rows(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    limit: int = 100,
    offset: int = 0,
    as_of: int | None = None,
    include_tombstoned: bool = False,
) -> list[dict[str, Any]]:
    base = _select_with_binding(cls, include_tombstoned=include_tombstoned)
    where = sql.SQL("WHERE s._spec_revision <= %s") if as_of is not None else sql.SQL("")
    stmt = sql.SQL("{base} {where} ORDER BY b.canonical_id, s._source LIMIT %s OFFSET %s").format(
        base=base, where=where
    )
    params: list[Any] = []
    if as_of is not None:
        params.append(as_of)
    params.extend([limit, offset])
    cur = await conn.cursor(row_factory=dict_row).execute(stmt, params)
    return [_serialize_row(r) for r in await cur.fetchall()]


async def query_rows(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    predicate_sql: sql.Composable | None,
    predicate_params: list[Any],
    limit: int = 100,
    offset: int = 0,
    as_of: int | None = None,
    order_by_sql: sql.Composable | None = None,
    order_by_params: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """List rows with an optional compiled predicate fragment.

    ``predicate_sql`` is a psycopg sql.Composable from the SQL compiler
    (already parameterised); ``predicate_params`` are its positional values.
    When ``predicate_sql`` is None the query is equivalent to list_rows.

    ``order_by_sql`` is an optional ORDER BY clause (without the ORDER BY
    keyword) as a sql.Composable. When None, defaults to
    ``b.canonical_id ASC, s._source ASC`` for deterministic output.

    ``order_by_params`` are the positional parameters for any subquery
    expressions in the ORDER BY clause (e.g. derived-slot derivations).
    They are inserted after derived-column SELECT params and before
    WHERE params in the overall positional binding list.

    Reads JOIN source × current bindings (valid_to IS NULL).  Derived slots
    are computed as correlated subqueries appended to the SELECT list.
    """
    base, derived_params = _select_with_derivations(cls)
    clauses: list[sql.Composable] = []
    # derived_params must come first: they bind placeholders in the SELECT list,
    # which appears before any WHERE clause in the emitted SQL.
    params: list[Any] = list(derived_params)

    if as_of is not None:
        clauses.append(sql.SQL("s._spec_revision <= %s"))
        params.append(as_of)

    if predicate_sql is not None:
        clauses.append(predicate_sql)
        params.extend(predicate_params)

    if clauses:
        where = sql.SQL("WHERE ") + sql.SQL(" AND ").join(
            sql.SQL("(") + c + sql.SQL(")") for c in clauses
        )
    else:
        where = sql.SQL("")

    # Defined classes are backed by a VIEW that already embeds _canonical_id.
    # Use s._canonical_id for ORDER BY instead of the bindings alias b.canonical_id.
    if _is_defined_class(cls):
        canonical_order = sql.SQL("s._canonical_id ASC, s._source ASC")
    else:
        canonical_order = sql.SQL("b.canonical_id ASC, s._source ASC")

    if order_by_sql is not None:
        order_clause = sql.SQL("ORDER BY ") + order_by_sql + sql.SQL(", ") + canonical_order
    else:
        order_clause = sql.SQL("ORDER BY ") + canonical_order

    stmt = sql.SQL("{base} {where} {order} LIMIT %s OFFSET %s").format(
        base=base, where=where, order=order_clause
    )

    # ORDER BY params (from derived-slot sort expressions) go after WHERE params.
    if order_by_params:
        params.extend(order_by_params)
    params.extend([limit, offset])

    cur = await conn.cursor(row_factory=dict_row).execute(stmt, params)
    return [_serialize_row(r) for r in await cur.fetchall()]


async def get_canonical_contributions(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
    include_tombstoned: bool = False,
) -> list[dict[str, Any]]:
    if _is_defined_class(cls):
        # VIEW exposes _canonical_id directly; filter on it.
        where_extra = sql.SQL(" AND s._spec_revision <= %s") if as_of is not None else sql.SQL("")
        stmt = sql.SQL(
            "SELECT s.* FROM {view} s WHERE s._canonical_id = %s{where_extra} ORDER BY s._source"
        ).format(view=_table_id(cls), where_extra=where_extra)
        params: list[Any] = [canonical_id]
        if as_of is not None:
            params.append(as_of)
        cur = await conn.cursor(row_factory=dict_row).execute(stmt, params)
        return [_serialize_row(r) for r in await cur.fetchall()]

    base, derived_params = _select_with_derivations(cls, include_tombstoned=include_tombstoned)
    where_extra = sql.SQL(" AND s._spec_revision <= %s") if as_of is not None else sql.SQL("")
    stmt = sql.SQL("{base} WHERE b.canonical_id = %s{where_extra} ORDER BY s._source").format(
        base=base, where_extra=where_extra
    )
    # derived_params bind SELECT subqueries; canonical_id + as_of bind WHERE.
    params: list[Any] = list(derived_params) + [canonical_id]
    if as_of is not None:
        params.append(as_of)
    cur = await conn.cursor(row_factory=dict_row).execute(stmt, params)
    return [_serialize_row(r) for r in await cur.fetchall()]


async def count_rows(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    as_of: int | None = None,
    predicate_sql: sql.Composable | None = None,
    predicate_params: list[Any] | None = None,
    include_tombstoned: bool = False,
) -> int:
    """Count rows matching optional predicate (same filter as query_rows)."""
    clauses: list[sql.Composable] = []
    params: list[Any] = []

    if as_of is not None:
        clauses.append(sql.SQL("s._spec_revision <= %s"))
        params.append(as_of)

    if predicate_sql is not None:
        clauses.append(predicate_sql)
        params.extend(predicate_params or [])

    if clauses:
        where = sql.SQL("WHERE ") + sql.SQL(" AND ").join(
            sql.SQL("(") + c + sql.SQL(")") for c in clauses
        )
    else:
        where = sql.SQL("")

    if _is_defined_class(cls):
        # VIEW already embeds the source×bindings join; query it directly.
        stmt = sql.SQL("SELECT count(*) FROM {view} s {where}").format(
            view=_table_id(cls), where=where
        )
    elif include_tombstoned:
        stmt = sql.SQL(
            "SELECT count(*) FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id "
            "  AND b.valid_from = ("
            "    SELECT max(b2.valid_from) FROM {bindings} b2 "
            "    WHERE b2.knot_row_id = s._knot_row_id "
            "      AND b2.change_type IN ('tombstone', 'ingest', 'correction', "
            "                             'merge', 'split', 'add', 'rejected')"
            "  ) "
            "  AND (b.valid_to IS NULL OR b.change_type = 'tombstone') "
            "{where}"
        ).format(source=_table_id(cls), bindings=_bindings_id(cls), where=where)
    else:
        stmt = sql.SQL(
            "SELECT count(*) FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL "
            "{where}"
        ).format(source=_table_id(cls), bindings=_bindings_id(cls), where=where)
    return (await (await conn.execute(stmt, params)).fetchone())[0]


async def aggregate_rows(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    as_of: int | None = None,
    predicate_sql: sql.Composable | None = None,
    predicate_params: list[Any] | None = None,
    agg_fields: list[tuple[str, str, str]],
) -> dict[str, Any]:
    """Run one SELECT with COUNT(*) plus requested aggregates over filtered rows.

    ``agg_fields`` is a list of ``(agg_func, slot_name, result_key)`` triples:
      - ``agg_func``   — SQL aggregate function name: ``sum``, ``avg``, ``min``, ``max``
      - ``slot_name``  — stored slot column name
      - ``result_key`` — key in the returned dict

    Returns a dict with ``count`` (int) plus one entry per agg_fields element.
    NULL is returned as None for slots with no matching rows.

    Centralisation rule: all SQL lives in db/.  The resolver in
    ``api._graphql_schema`` calls this and maps the dict to the
    ``AggregateResult_<Class>`` strawberry type.
    """
    _ALLOWED_AGG_FUNCS = {"sum", "avg", "min", "max"}
    clauses: list[sql.Composable] = []
    params: list[Any] = []

    if as_of is not None:
        clauses.append(sql.SQL("s._spec_revision <= %s"))
        params.append(as_of)

    if predicate_sql is not None:
        clauses.append(predicate_sql)
        params.extend(predicate_params or [])

    if clauses:
        where = sql.SQL("WHERE ") + sql.SQL(" AND ").join(
            sql.SQL("(") + c + sql.SQL(")") for c in clauses
        )
    else:
        where = sql.SQL("")

    # Build SELECT list: count(*) first, then each requested aggregate.
    select_parts: list[sql.Composable] = [sql.SQL("count(*) AS _count")]
    for agg_func, slot_name, result_key in agg_fields:
        if agg_func.lower() not in _ALLOWED_AGG_FUNCS:
            raise ValueError(
                f"aggregate_rows: unsupported agg_func {agg_func!r}; "
                f"allowed: {sorted(_ALLOWED_AGG_FUNCS)}"
            )
        select_parts.append(
            sql.SQL("{func}(s.{col}) AS {alias}").format(
                func=sql.SQL(agg_func.lower()),
                col=sql.Identifier(slot_name),
                alias=sql.Identifier(result_key),
            )
        )
    select_sql = sql.SQL(", ").join(select_parts)

    if _is_defined_class(cls):
        stmt = sql.SQL("SELECT {select} FROM {view} s {where}").format(
            select=select_sql, view=_table_id(cls), where=where
        )
    else:
        stmt = sql.SQL(
            "SELECT {select} FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL "
            "{where}"
        ).format(
            select=select_sql,
            source=_table_id(cls),
            bindings=_bindings_id(cls),
            where=where,
        )

    row = await (await conn.execute(stmt, params)).fetchone()
    if row is None:
        result: dict[str, Any] = {"count": 0}
        for _, _, result_key in agg_fields:
            result[result_key] = None
        return result

    result = {"count": row[0]}
    for i, (_, _, result_key) in enumerate(agg_fields):
        result[result_key] = row[1 + i]
    return result


async def get_disagreeing_contributions(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    slot_name: str,
) -> list[tuple[str, Any]]:
    """For bandit-feedback emission: per-source non-null values for one
    slot under the current binding for ``canonical_id``, excluding the
    user-corrections source."""
    stmt = sql.SQL(
        "SELECT s._source, s.{col} "
        "FROM {source} s "
        "JOIN {bindings} b "
        "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL "
        "WHERE b.canonical_id = %s "
        "  AND s._source <> %s "
        "  AND s.{col} IS NOT NULL"
    ).format(
        col=sql.Identifier(slot_name),
        source=_table_id(cls),
        bindings=_bindings_id(cls),
    )
    rows = await (await conn.execute(stmt, (canonical_id, user_corrections_source()))).fetchall()
    return [(r[0], r[1]) for r in rows]


async def canonical_id_exists(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
) -> bool:
    """True iff at least one current binding has this canonical_id."""
    stmt = sql.SQL(
        "SELECT 1 FROM {bindings} WHERE canonical_id = %s AND valid_to IS NULL LIMIT 1"
    ).format(bindings=_bindings_id(cls))
    return (await (await conn.execute(stmt, (canonical_id,))).fetchone()) is not None


# ─── SCD2 binding mutations (used by Merge / Split / corrections) ───────────


async def merge_canonical_ids(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    keep_canonical_id: str,
    from_canonical_ids: list[str],
    spec_revision: int,
    correction_id: int | None = None,
    change_type: str = "merge",
) -> int:
    """SCD2 merge: close current bindings whose canonical_id is in
    ``from_canonical_ids``; open new bindings for the same knot_row_ids
    with ``canonical_id = keep_canonical_id``. Returns the count of
    bindings rewritten.

    Concurrency: the SELECT uses ``FOR UPDATE`` to serialise concurrent
    merges on overlapping rows. The close-stamp uses ``clock_timestamp()``
    so the boundary differs from the new binding's ``valid_from``
    (which is also ``clock_timestamp()`` on the next call) — the
    half-open ``[valid_from, valid_to)`` invariant is preserved.
    A partial unique index ``(knot_row_id) WHERE valid_to IS NULL``
    on the bindings table catches any escaped duplicate.
    """
    rewritten = 0
    for from_cid in from_canonical_ids:
        rows = await (
            await conn.execute(
                sql.SQL(
                    "SELECT knot_row_id FROM {bindings} "
                    "WHERE canonical_id = %s AND valid_to IS NULL "
                    "FOR UPDATE"
                ).format(bindings=_bindings_id(cls)),
                (from_cid,),
            )
        ).fetchall()
        for (knot_row_id,) in rows:
            await conn.execute(
                sql.SQL(
                    "UPDATE {bindings} SET valid_to = clock_timestamp() "
                    "WHERE knot_row_id = %s AND valid_to IS NULL"
                ).format(bindings=_bindings_id(cls)),
                (knot_row_id,),
            )
            await conn.execute(
                sql.SQL(
                    "INSERT INTO {bindings} "
                    "(knot_row_id, canonical_id, valid_from, change_type, "
                    " applied_revision, correction_id) "
                    "VALUES (%s, %s, clock_timestamp(), %s, %s, %s)"
                ).format(bindings=_bindings_id(cls)),
                (knot_row_id, keep_canonical_id, change_type, spec_revision, correction_id),
            )
            rewritten += 1
    return rewritten


# ─── Split ──────────────────────────────────────────────────────────────────


async def split_canonical_id(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    source_canonical_id: str,
    partitions: dict[str, list[tuple[str, str]]],
    spec_revision: int,
    correction_id: int | None = None,
) -> int:
    """SCD2 split: close current bindings for ``source_canonical_id`` and
    open new bindings per partition.

    ``partitions`` maps ``new_canonical_id -> [(source, source_row_id), ...]``.
    For each (source, source_row_id) in a partition we look up the knot_row_id
    whose current binding has ``canonical_id = source_canonical_id``, close that
    binding, and open a new binding with the new canonical_id and
    ``change_type='split'``.

    SELECT FOR UPDATE serialises concurrent splits on the same rows.
    Returns the total number of bindings rewritten.
    """
    rewritten = 0
    for new_cid, members in partitions.items():
        for source_name, source_row_id in members:
            row = await (
                await conn.execute(
                    sql.SQL(
                        "SELECT b.knot_row_id FROM {bindings} b "
                        "JOIN {source} s ON s._knot_row_id = b.knot_row_id "
                        "WHERE b.canonical_id = %s AND b.valid_to IS NULL "
                        "  AND s._source = %s AND s._source_row_id = %s "
                        "FOR UPDATE"
                    ).format(
                        bindings=_bindings_id(cls),
                        source=_table_id(cls),
                    ),
                    (source_canonical_id, source_name, source_row_id),
                )
            ).fetchone()
            if row is None:
                continue
            knot_row_id = row[0]
            await conn.execute(
                sql.SQL(
                    "UPDATE {bindings} SET valid_to = clock_timestamp() "
                    "WHERE knot_row_id = %s AND valid_to IS NULL"
                ).format(bindings=_bindings_id(cls)),
                (knot_row_id,),
            )
            await conn.execute(
                sql.SQL(
                    "INSERT INTO {bindings} "
                    "(knot_row_id, canonical_id, valid_from, change_type, "
                    " applied_revision, correction_id) "
                    "VALUES (%s, %s, clock_timestamp(), 'split', %s, %s)"
                ).format(bindings=_bindings_id(cls)),
                (knot_row_id, new_cid, spec_revision, correction_id),
            )
            rewritten += 1
    return rewritten


# ─── Add (synthetic entity) ─────────────────────────────────────────────────


async def insert_synthetic_row(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    new_canonical_id: str,
    values: dict[str, Any],
    spec_revision: int,
    correction_id: int | None = None,
) -> str:
    """Insert a synthetic source row attributed to ``_user_corrections`` and
    open an initial binding for it.

    ``values`` must contain only stored-slot names for the class. Returns
    the new ``_knot_row_id`` (str).
    """
    slot_names = _stored_slot_names(cls)
    user_cols = ["_source", "_source_row_id", "_spec_revision", *slot_names]
    cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in user_cols)
    placeholders = sql.SQL(", ").join(sql.Placeholder() * len(user_cols))
    placeholder_values: list[Any] = [
        user_corrections_source(),
        new_canonical_id,
        spec_revision,
    ]
    for sn in slot_names:
        placeholder_values.append(values.get(sn))

    knot_row_id = (
        await (
            await conn.execute(
                sql.SQL("INSERT INTO {table} ({cols}) VALUES ({ph}) RETURNING _knot_row_id").format(
                    table=_table_id(cls),
                    cols=cols_sql,
                    ph=placeholders,
                ),
                placeholder_values,
            )
        ).fetchone()
    )[0]

    await conn.execute(
        sql.SQL(
            "INSERT INTO {bindings} "
            "(knot_row_id, canonical_id, valid_from, change_type, "
            " applied_revision, correction_id) "
            "VALUES (%s, %s, clock_timestamp(), 'add', %s, %s)"
        ).format(bindings=_bindings_id(cls)),
        (knot_row_id, new_canonical_id, spec_revision, correction_id),
    )
    return str(knot_row_id)


# ─── Tombstone ───────────────────────────────────────────────────────────────


async def tombstone_canonical_id(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    spec_revision: int,
    correction_id: int | None = None,
) -> int:
    """Close all current bindings for ``canonical_id`` with
    ``change_type='tombstone'``. No replacement bindings are opened, so the
    entity naturally disappears from reads (which JOIN ``valid_to IS NULL``).

    SELECT FOR UPDATE serialises concurrent tombstones on the same rows.
    Returns the number of bindings closed.
    """
    rows = await (
        await conn.execute(
            sql.SQL(
                "SELECT knot_row_id FROM {bindings} "
                "WHERE canonical_id = %s AND valid_to IS NULL "
                "FOR UPDATE"
            ).format(bindings=_bindings_id(cls)),
            (canonical_id,),
        )
    ).fetchall()
    closed = 0
    for (knot_row_id,) in rows:
        await conn.execute(
            sql.SQL(
                "UPDATE {bindings} "
                "SET valid_to = clock_timestamp(), "
                "    change_type = 'tombstone', "
                "    applied_revision = %s, "
                "    correction_id = %s "
                "WHERE knot_row_id = %s AND valid_to IS NULL"
            ).format(bindings=_bindings_id(cls)),
            (spec_revision, correction_id, knot_row_id),
        )
        closed += 1
    return closed


# ─── RejectContribution ──────────────────────────────────────────────────────


async def reject_contribution(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    source: str,
    spec_revision: int,
    correction_id: int | None = None,
) -> bool:
    """Close the single current binding for the (canonical_id, source) pair.

    The source row in ``knot_data.<class>`` is preserved for audit. Returns
    True if a binding was found and closed, False if none matched.
    SELECT FOR UPDATE serialises concurrent rejections on the same row.
    """
    row = await (
        await conn.execute(
            sql.SQL(
                "SELECT b.knot_row_id FROM {bindings} b "
                "JOIN {source_table} s ON s._knot_row_id = b.knot_row_id "
                "WHERE b.canonical_id = %s AND b.valid_to IS NULL "
                "  AND s._source = %s "
                "FOR UPDATE"
            ).format(
                bindings=_bindings_id(cls),
                source_table=_table_id(cls),
            ),
            (canonical_id, source),
        )
    ).fetchone()
    if row is None:
        return False
    knot_row_id = row[0]
    await conn.execute(
        sql.SQL(
            "UPDATE {bindings} "
            "SET valid_to = clock_timestamp(), "
            "    change_type = 'rejected', "
            "    applied_revision = %s, "
            "    correction_id = %s "
            "WHERE knot_row_id = %s AND valid_to IS NULL"
        ).format(bindings=_bindings_id(cls)),
        (spec_revision, correction_id, knot_row_id),
    )
    return True


# ─── Lineage event log ──────────────────────────────────────────────────────


async def append_lineage_event(
    conn: psycopg.AsyncConnection,
    *,
    class_name: str,
    change_type: str,
    from_canonical_ids: list[str],
    to_canonical_ids: list[str],
    applied_revision: int,
    correction_id: int | None = None,
) -> int:
    """Append a row to ``canonical_id_lineage``. Returns the event_id."""
    row = await (
        await conn.execute(
            "INSERT INTO canonical_id_lineage "
            "(class_name, change_type, from_canonical_ids, to_canonical_ids, "
            " applied_revision, correction_id) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING event_id",
            (
                class_name,
                change_type,
                from_canonical_ids,
                to_canonical_ids,
                applied_revision,
                correction_id,
            ),
        )
    ).fetchone()
    return row[0]


async def list_lineage(
    conn: psycopg.AsyncConnection,
    *,
    class_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if class_name is None:
        rows = await (
            await conn.execute(
                "SELECT event_id, class_name, change_type, from_canonical_ids, "
                "to_canonical_ids, applied_revision, correction_id, created_at "
                "FROM canonical_id_lineage ORDER BY event_id DESC LIMIT %s",
                (limit,),
            )
        ).fetchall()
    else:
        rows = await (
            await conn.execute(
                "SELECT event_id, class_name, change_type, from_canonical_ids, "
                "to_canonical_ids, applied_revision, correction_id, created_at "
                "FROM canonical_id_lineage WHERE class_name = %s "
                "ORDER BY event_id DESC LIMIT %s",
                (class_name, limit),
            )
        ).fetchall()
    return [
        {
            "event_id": r[0],
            "class_name": r[1],
            "change_type": r[2],
            "from_canonical_ids": list(r[3]),
            "to_canonical_ids": list(r[4]),
            "applied_revision": r[5],
            "correction_id": r[6],
            "created_at": r[7].isoformat(),
        }
        for r in rows
    ]
