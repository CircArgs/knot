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

from knot.db._naming import (
    USER_CORRECTIONS_SOURCE,
    bindings_table_id as _bindings_id,
    is_stored as _is_stored,
    stored_slot_names as _stored_slot_names,
    table_id as _table_id,
)
from knot.ontology import OntologyClass, Source


__all__ = (
    "USER_CORRECTIONS_SOURCE",
    "insert_rows",
    "list_rows",
    "query_rows",
    "count_rows",
    "get_canonical_contributions",
    "get_disagreeing_contributions",
    "canonical_id_exists",
    "merge_canonical_ids",
    "upsert_user_correction_row",
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


def insert_rows(
    conn: psycopg.Connection,
    *,
    source: Source,
    spec_revision: int,
    rows: list[dict[str, Any]],
) -> int:
    """Upsert a batch of source rows. For each row:
      1. INSERT/UPDATE the source-row table (preserves _knot_row_id on conflict).
      2. INSERT a binding (canonical_id = identifier value, valid_to NULL,
         change_type 'ingest') iff no current binding exists for that
         knot_row_id.

    Returns the number of source rows written.
    """
    cls = source.entity_class
    id_slot_name = source.identifier_slot.name
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
    with conn.transaction():
        for row in rows:
            id_value = row[id_slot_name]
            values: list[Any] = [
                source.name,
                str(id_value),
                spec_revision,
            ]
            for slot_name in slot_names:
                values.append(row.get(slot_name))
            knot_row_id = conn.execute(upsert_stmt, values).fetchone()[0]
            conn.execute(
                binding_insert_stmt,
                (knot_row_id, str(id_value), spec_revision, knot_row_id),
            )
            count += 1
    return count


# ─── User-correction-row upsert ─────────────────────────────────────────────


def upsert_user_correction_row(
    conn: psycopg.Connection,
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
        USER_CORRECTIONS_SOURCE,
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

    knot_row_id = conn.execute(upsert_stmt, placeholder_values).fetchone()[0]
    conn.execute(
        binding_insert_stmt,
        (knot_row_id, canonical_id, spec_revision, knot_row_id),
    )
    return str(knot_row_id)


# ─── Reads (JOIN source × current bindings) ─────────────────────────────────


def _is_defined_class(cls: OntologyClass) -> bool:
    """True when the class is a defined class (backed by a VIEW, not a TABLE)."""
    return getattr(cls, "definition", None) is not None


def _select_with_binding(cls: OntologyClass) -> sql.Composable:
    """SELECT s.*, b.canonical_id AS _canonical_id FROM source s
    JOIN bindings b ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL.

    For defined classes, the VIEW already embeds the JOIN and exposes
    _canonical_id directly; query it without the extra JOIN.
    """
    if _is_defined_class(cls):
        return sql.SQL(
            "SELECT s.* FROM {view} s"
        ).format(source=_table_id(cls), view=_table_id(cls))
    return sql.SQL(
        "SELECT s.*, b.canonical_id AS _canonical_id "
        "FROM {source} s "
        "JOIN {bindings} b "
        "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
    ).format(source=_table_id(cls), bindings=_bindings_id(cls))


def _derived_column_exprs(
    cls: OntologyClass,
) -> tuple[list[sql.Composable], list[Any]]:
    """Build a list of ``(<subquery>) AS <slot_name>`` fragments for derived slots,
    plus the accumulated positional parameters for those subqueries.

    Returns ``([], [])`` when the class has no derived slots (the common case).
    The outer alias used when building the subquery context is ``"s"`` — the
    same alias emitted by ``_select_with_binding`` for the source-row table.

    Parameters must be prepended to the outer query's parameter list because
    the derived columns appear in the SELECT clause before any WHERE params.
    """
    from knot.db.sql_compiler import CompileContext, compile_value

    derived_cols: list[sql.Composable] = []
    derived_params: list[Any] = []
    for slot in cls.slots:
        if _is_stored(slot):
            continue
        derivation = getattr(slot, "derivation", None)
        if derivation is None:
            continue
        ctx = CompileContext(primary_class=cls, alias="s")
        expr_sql = compile_value(derivation, ctx)
        derived_cols.append(
            sql.SQL("({expr}) AS {col}").format(
                expr=expr_sql,
                col=sql.Identifier(slot.name),
            )
        )
        derived_params.extend(ctx.params)
    return derived_cols, derived_params


def _select_with_derivations(
    cls: OntologyClass,
) -> tuple[sql.Composable, list[Any]]:
    """Same as ``_select_with_binding`` but appends a computed column per
    derived slot, compiled via the SQL compiler.

    Returns ``(sql_composable, derived_params)`` where ``derived_params`` are
    the positional parameters for the derived-column subqueries.  The caller
    must prepend these to the outer query's parameter list (SELECT params come
    before WHERE params in psycopg positional binding).

    When there are no derived slots the result is ``(_select_with_binding(cls), [])``.
    """
    base = _select_with_binding(cls)
    derived_cols, derived_params = _derived_column_exprs(cls)
    if not derived_cols:
        return base, []
    extra = sql.SQL(", ").join(derived_cols)
    if _is_defined_class(cls):
        stmt = sql.SQL(
            "SELECT s.*, {extra} FROM {view} s"
        ).format(extra=extra, view=_table_id(cls))
    else:
        stmt = sql.SQL(
            "SELECT s.*, b.canonical_id AS _canonical_id, {extra} "
            "FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL"
        ).format(
            extra=extra,
            source=_table_id(cls),
            bindings=_bindings_id(cls),
        )
    return stmt, derived_params


def list_rows(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    limit: int = 100,
    offset: int = 0,
    as_of: int | None = None,
) -> list[dict[str, Any]]:
    base = _select_with_binding(cls)
    where = sql.SQL("WHERE s._spec_revision <= %s") if as_of is not None else sql.SQL("")
    stmt = sql.SQL(
        "{base} {where} ORDER BY b.canonical_id, s._source LIMIT %s OFFSET %s"
    ).format(base=base, where=where)
    params: list[Any] = []
    if as_of is not None:
        params.append(as_of)
    params.extend([limit, offset])
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(stmt, params)
    return [_serialize_row(r) for r in cur.fetchall()]


def query_rows(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    predicate_sql: "sql.Composable | None",
    predicate_params: "list[Any]",
    limit: int = 100,
    offset: int = 0,
    as_of: int | None = None,
    order_by_sql: "sql.Composable | None" = None,
) -> list[dict[str, Any]]:
    """List rows with an optional compiled predicate fragment.

    ``predicate_sql`` is a psycopg sql.Composable from the SQL compiler
    (already parameterised); ``predicate_params`` are its positional values.
    When ``predicate_sql`` is None the query is equivalent to list_rows.

    ``order_by_sql`` is an optional ORDER BY clause (without the ORDER BY
    keyword) as a sql.Composable. When None, defaults to
    ``b.canonical_id ASC, s._source ASC`` for deterministic output.

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

    stmt = sql.SQL(
        "{base} {where} {order} LIMIT %s OFFSET %s"
    ).format(base=base, where=where, order=order_clause)
    params.extend([limit, offset])

    cur = conn.cursor(row_factory=dict_row)
    cur.execute(stmt, params)
    return [_serialize_row(r) for r in cur.fetchall()]


def get_canonical_contributions(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    as_of: int | None = None,
) -> list[dict[str, Any]]:
    if _is_defined_class(cls):
        # VIEW exposes _canonical_id directly; filter on it.
        where_extra = sql.SQL(" AND s._spec_revision <= %s") if as_of is not None else sql.SQL("")
        stmt = sql.SQL(
            "SELECT s.* FROM {view} s"
            " WHERE s._canonical_id = %s{where_extra} ORDER BY s._source"
        ).format(view=_table_id(cls), where_extra=where_extra)
        params: list[Any] = [canonical_id]
        if as_of is not None:
            params.append(as_of)
        cur = conn.cursor(row_factory=dict_row)
        cur.execute(stmt, params)
        return [_serialize_row(r) for r in cur.fetchall()]

    base, derived_params = _select_with_derivations(cls)
    where_extra = sql.SQL(" AND s._spec_revision <= %s") if as_of is not None else sql.SQL("")
    stmt = sql.SQL(
        "{base} WHERE b.canonical_id = %s{where_extra} ORDER BY s._source"
    ).format(base=base, where_extra=where_extra)
    # derived_params bind SELECT subqueries; canonical_id + as_of bind WHERE.
    params: list[Any] = list(derived_params) + [canonical_id]
    if as_of is not None:
        params.append(as_of)
    cur = conn.cursor(row_factory=dict_row)
    cur.execute(stmt, params)
    return [_serialize_row(r) for r in cur.fetchall()]


def count_rows(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    as_of: int | None = None,
    predicate_sql: "sql.Composable | None" = None,
    predicate_params: "list[Any] | None" = None,
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
        stmt = sql.SQL(
            "SELECT count(*) FROM {view} s {where}"
        ).format(view=_table_id(cls), where=where)
    else:
        stmt = sql.SQL(
            "SELECT count(*) FROM {source} s "
            "JOIN {bindings} b "
            "  ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL "
            "{where}"
        ).format(source=_table_id(cls), bindings=_bindings_id(cls), where=where)
    return conn.execute(stmt, params).fetchone()[0]


def get_disagreeing_contributions(
    conn: psycopg.Connection,
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
    rows = conn.execute(stmt, (canonical_id, USER_CORRECTIONS_SOURCE)).fetchall()
    return [(r[0], r[1]) for r in rows]


def canonical_id_exists(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
) -> bool:
    """True iff at least one current binding has this canonical_id."""
    stmt = sql.SQL(
        "SELECT 1 FROM {bindings} "
        "WHERE canonical_id = %s AND valid_to IS NULL LIMIT 1"
    ).format(bindings=_bindings_id(cls))
    return conn.execute(stmt, (canonical_id,)).fetchone() is not None


# ─── SCD2 binding mutations (used by Merge / Split / corrections) ───────────


def merge_canonical_ids(
    conn: psycopg.Connection,
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
        rows = conn.execute(
            sql.SQL(
                "SELECT knot_row_id FROM {bindings} "
                "WHERE canonical_id = %s AND valid_to IS NULL "
                "FOR UPDATE"
            ).format(bindings=_bindings_id(cls)),
            (from_cid,),
        ).fetchall()
        for (knot_row_id,) in rows:
            conn.execute(
                sql.SQL(
                    "UPDATE {bindings} SET valid_to = clock_timestamp() "
                    "WHERE knot_row_id = %s AND valid_to IS NULL"
                ).format(bindings=_bindings_id(cls)),
                (knot_row_id,),
            )
            conn.execute(
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


# ─── Lineage event log ──────────────────────────────────────────────────────


def append_lineage_event(
    conn: psycopg.Connection,
    *,
    class_name: str,
    change_type: str,
    from_canonical_ids: list[str],
    to_canonical_ids: list[str],
    applied_revision: int,
    correction_id: int | None = None,
) -> int:
    """Append a row to ``canonical_id_lineage``. Returns the event_id."""
    return conn.execute(
        "INSERT INTO canonical_id_lineage "
        "(class_name, change_type, from_canonical_ids, to_canonical_ids, "
        " applied_revision, correction_id) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING event_id",
        (class_name, change_type, from_canonical_ids, to_canonical_ids,
         applied_revision, correction_id),
    ).fetchone()[0]


def list_lineage(
    conn: psycopg.Connection,
    *,
    class_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if class_name is None:
        rows = conn.execute(
            "SELECT event_id, class_name, change_type, from_canonical_ids, "
            "to_canonical_ids, applied_revision, correction_id, created_at "
            "FROM canonical_id_lineage ORDER BY event_id DESC LIMIT %s",
            (limit,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT event_id, class_name, change_type, from_canonical_ids, "
            "to_canonical_ids, applied_revision, correction_id, created_at "
            "FROM canonical_id_lineage WHERE class_name = %s "
            "ORDER BY event_id DESC LIMIT %s",
            (class_name, limit),
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
