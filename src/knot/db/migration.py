"""Migration emitter — spec → postgres DDL.

Per-class storage is **two tables**, per the SCD2 binding design
(``design/staging/er-and-storage.md``):

  - ``knot_data.<class>``           source rows (immutable per ingest);
                                    columns mirror stored slots; stable
                                    ``_knot_row_id`` UUID anchor.
  - ``knot_data.<class>_bindings``  SCD2 bindings:
                                    ``(knot_row_id, canonical_id,
                                       valid_from, valid_to,
                                       change_type, applied_revision)``
                                    ``valid_to IS NULL`` marks current.

Reads JOIN the two; merges/splits/corrections close current bindings
(``valid_to = now()``) and open new ones (``valid_from = now()``).
History is preserved.

Mapping (locked):
  - schema:           ``knot_data``
  - class → table:    lowercase class name (``Movie`` → ``knot_data.movie``)
  - bindings table:   same name + ``_bindings`` suffix
  - system columns:   underscore-prefixed
  - slot → column:    range-based postgres type (whitelist); multivalued → ``T[]``
  - derived slots:    skipped (query-time projections)

Identifiers go through ``psycopg.sql.Identifier``. Postgres types come
from a fixed whitelist; type names from the spec are never spliced raw.

Diffing:
  - ``diff_specs(prev, candidate) → list[Change]`` — typed dataclasses.
  - ``emit_ddl(change, conn)`` — single-dispatch over Change subtypes.
  - ``apply_migration(conn, prev, candidate)`` — runs diff + applies.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import singledispatch

import psycopg
from psycopg import sql

from knot.db._naming import (
    SCHEMA as _SCHEMA,
    bindings_table_id as _bindings_table_id,
    effective_slots as _effective_slots,
    is_stored as _is_stored,
    slot_pg_type as _slot_pg_type,
    table_id as _table_id,
)
from knot.ontology import OntologyClass, Slot, Spec


def _is_defined(cls: OntologyClass) -> bool:
    """True when the class is a defined class (has a definition — becomes a VIEW)."""
    return getattr(cls, "definition", None) is not None


def _bindings_index_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(f"{cls.name.lower()}_bindings_current")


# Source-row system columns (per-ingest, immutable except by re-push).
# ``_knot_row_id`` is the stable anchor referenced by the bindings table —
# preserved across upserts via ON CONFLICT (default keeps existing UUID).
_SYSTEM_COLUMNS_SQL = sql.SQL(
    "_knot_row_id UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE, "
    "_source TEXT NOT NULL, "
    "_source_row_id TEXT NOT NULL, "
    "_ingest_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "_spec_revision INTEGER NOT NULL REFERENCES public.spec_revisions(revision), "
    "PRIMARY KEY (_source, _source_row_id)"
)


# SCD2 bindings table per class. ``valid_to IS NULL`` is the sole "current"
# row for any given knot_row_id; all queries against the data plane filter
# by it. Closing a binding = ``UPDATE SET valid_to = now()``; opening =
# ``INSERT`` with ``valid_to NULL``.
def _bindings_create_sql(cls: OntologyClass) -> sql.Composable:
    return sql.SQL(
        "CREATE TABLE IF NOT EXISTS {table} ("
        "knot_row_id UUID NOT NULL, "
        "canonical_id TEXT NOT NULL, "
        "valid_from TIMESTAMPTZ NOT NULL DEFAULT now(), "
        "valid_to TIMESTAMPTZ, "
        "change_type TEXT NOT NULL, "
        "applied_revision INTEGER NOT NULL REFERENCES public.spec_revisions(revision), "
        "correction_id INTEGER REFERENCES public._user_corrections(id), "
        "PRIMARY KEY (knot_row_id, valid_from)"
        ")"
    ).format(table=_bindings_table_id(cls))


def _bindings_index_sql(cls: OntologyClass) -> sql.Composable:
    # Partial index for the "current binding by canonical_id" lookup —
    # the hottest read path for the data plane.
    return sql.SQL(
        "CREATE INDEX IF NOT EXISTS {idx} ON {table} (canonical_id) "
        "WHERE valid_to IS NULL"
    ).format(idx=_bindings_index_id(cls), table=_bindings_table_id(cls))


def _bindings_unique_current_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(f"{cls.name.lower()}_bindings_one_current_per_row")


def _bindings_unique_current_sql(cls: OntologyClass) -> sql.Composable:
    # Partial UNIQUE index enforcing exactly one current binding per
    # knot_row_id. Catches any race in merge / split / correction that
    # escapes FOR UPDATE locking.
    return sql.SQL(
        "CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {table} (knot_row_id) "
        "WHERE valid_to IS NULL"
    ).format(idx=_bindings_unique_current_id(cls), table=_bindings_table_id(cls))


def _create_source_table_sql(cls: OntologyClass) -> sql.Composable:
    user_cols: list[sql.Composable] = []
    for slot in _effective_slots(cls):
        if not _is_stored(slot):
            continue
        user_cols.append(
            sql.SQL("{name} {pgtype} NULL").format(
                name=sql.Identifier(slot.name),
                pgtype=sql.SQL(_slot_pg_type(slot)),
            )
        )
    body = sql.SQL(", ").join([_SYSTEM_COLUMNS_SQL, *user_cols])
    return sql.SQL("CREATE TABLE IF NOT EXISTS {table} ({body})").format(
        table=_table_id(cls), body=body,
    )


# ─── Change events ──────────────────────────────────────────────────────────


@dataclass
class Change:
    pass


@dataclass
class AddClass(Change):
    cls: OntologyClass


@dataclass
class DropClass(Change):
    class_name: str
    is_view: bool = False  # True when dropping a defined-class VIEW


@dataclass
class AddDefinedClass(Change):
    """Create a VIEW for a defined class (equivalentClass / OWL DL defined)."""
    cls: OntologyClass


@dataclass
class DropDefinedClass(Change):
    """Drop the VIEW for a defined class."""
    class_name: str


@dataclass
class AddSlot(Change):
    cls: OntologyClass
    slot: Slot


@dataclass
class DropSlot(Change):
    cls: OntologyClass
    slot_name: str


@dataclass
class ChangeSlotType(Change):
    cls: OntologyClass
    slot: Slot
    prev_pg_type: str
    new_pg_type: str


@dataclass
class ChangeSlotRequired(Change):
    """No-op DDL today — slot.required is API-enforced (user-correction
    rows are partial). Kept on the change-event surface for completeness."""
    cls: OntologyClass
    slot_name: str
    new_required: bool


# ─── Diff visitor ───────────────────────────────────────────────────────────


def _stored_slots_by_name(cls: OntologyClass) -> dict[str, Slot]:
    # Walks own + mixin slots so adding/removing a mixin shows up as
    # AddSlot/DropSlot in the diff.
    return {s.name: s for s in _effective_slots(cls) if _is_stored(s)}


def diff_specs(prev: Spec | None, candidate: Spec) -> list[Change]:
    changes: list[Change] = []
    prev_classes = {c.name: c for c in (prev.classes if prev else [])}
    cand_classes = {c.name: c for c in candidate.classes}

    for name in cand_classes.keys() - prev_classes.keys():
        c = cand_classes[name]
        if not c.abstract:
            if _is_defined(c):
                changes.append(AddDefinedClass(cls=c))
            else:
                changes.append(AddClass(cls=c))

    for name in prev_classes.keys() - cand_classes.keys():
        c = prev_classes[name]
        if not c.abstract:
            if _is_defined(c):
                changes.append(DropDefinedClass(class_name=name))
            else:
                changes.append(DropClass(class_name=name))

    for name in cand_classes.keys() & prev_classes.keys():
        prev_cls, cand_cls = prev_classes[name], cand_classes[name]
        if cand_cls.abstract or prev_cls.abstract:
            continue

        prev_defined = _is_defined(prev_cls)
        cand_defined = _is_defined(cand_cls)

        # Concrete ↔ defined transition is always destructive: the storage
        # type changes (table ↔ view).  Emit as drop+add to force the
        # destructive gate.
        if prev_defined != cand_defined:
            if prev_defined:
                # Was a view, now concrete: drop view + add table.
                changes.append(DropDefinedClass(class_name=name))
                changes.append(AddClass(cls=cand_cls))
            else:
                # Was concrete, now defined: drop table + add view.
                changes.append(DropClass(class_name=name))
                changes.append(AddDefinedClass(cls=cand_cls))
            continue

        if cand_defined:
            # Both defined: re-create view if definition changed (simplest
            # approach; view DDL is idempotent via CREATE OR REPLACE).
            changes.append(AddDefinedClass(cls=cand_cls))
            continue

        # Both concrete — diff slots.
        prev_slots = _stored_slots_by_name(prev_cls)
        cand_slots = _stored_slots_by_name(cand_cls)

        for s_name in cand_slots.keys() - prev_slots.keys():
            changes.append(AddSlot(cls=cand_cls, slot=cand_slots[s_name]))
        for s_name in prev_slots.keys() - cand_slots.keys():
            changes.append(DropSlot(cls=cand_cls, slot_name=s_name))
        for s_name in cand_slots.keys() & prev_slots.keys():
            ps, cs = prev_slots[s_name], cand_slots[s_name]
            prev_t = _slot_pg_type(ps)
            new_t = _slot_pg_type(cs)
            if prev_t != new_t:
                changes.append(
                    ChangeSlotType(cls=cand_cls, slot=cs, prev_pg_type=prev_t, new_pg_type=new_t)
                )
            if ps.required != cs.required:
                changes.append(
                    ChangeSlotRequired(cls=cand_cls, slot_name=s_name, new_required=cs.required)
                )
    return changes


# ─── DDL emission (single-dispatch) ─────────────────────────────────────────


@singledispatch
def emit_ddl(change: Change, conn: psycopg.Connection) -> None:
    raise TypeError(f"No DDL emitter registered for {type(change).__name__}")


@emit_ddl.register
def _(change: AddClass, conn: psycopg.Connection) -> None:
    # Source-row table + bindings table + bindings indexes.
    conn.execute(_create_source_table_sql(change.cls))
    conn.execute(_bindings_create_sql(change.cls))
    conn.execute(_bindings_index_sql(change.cls))
    conn.execute(_bindings_unique_current_sql(change.cls))


@emit_ddl.register
def _(change: DropClass, conn: psycopg.Connection) -> None:
    # Drop bindings first to avoid FK-constraint-of-our-own-making.
    cls_lower = change.class_name.lower()
    if change.is_view:
        conn.execute(
            sql.SQL("DROP VIEW IF EXISTS {t} CASCADE").format(
                t=sql.Identifier(_SCHEMA, cls_lower),
            )
        )
        return
    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {t} CASCADE").format(
            t=sql.Identifier(_SCHEMA, f"{cls_lower}_bindings"),
        )
    )
    conn.execute(
        sql.SQL("DROP TABLE IF EXISTS {t} CASCADE").format(
            t=sql.Identifier(_SCHEMA, cls_lower),
        )
    )


@emit_ddl.register
def _(change: AddDefinedClass, conn: psycopg.Connection) -> None:
    """Create (or replace) a VIEW for the defined class.

    The VIEW selects all rows from the parent class (is_a) that satisfy the
    compiled definition predicate.  It JOINs source × bindings just like
    concrete-class reads, so resolvers can query it identically.

    Schema:
        CREATE OR REPLACE VIEW knot_data.<cls> AS
        SELECT s.*, b.canonical_id AS _canonical_id
        FROM knot_data.<parent> s
        JOIN knot_data.<parent>_bindings b
          ON b.knot_row_id = s._knot_row_id AND b.valid_to IS NULL
        WHERE (<compiled definition>)
    """
    from knot.db.sql_compiler import CompileContext, compile_predicate

    cls = change.cls
    if cls.is_a is None:
        raise ValueError(
            f"Defined class {cls.name!r} must have is_a set to a parent class."
        )
    parent = cls.is_a

    ctx = CompileContext(primary_class=parent, alias="s")
    where_sql = compile_predicate(cls.definition, ctx)

    # Join fragments from multi-slot SlotPath traversal (rare in definitions
    # but supported). Prepend to FROM clause.
    if ctx.joins:
        joins_sql = sql.SQL(" ") + sql.SQL(" ").join(ctx.joins)
    else:
        joins_sql = sql.SQL("")

    view_stmt = sql.SQL(
        "CREATE OR REPLACE VIEW {view} AS "
        "SELECT s.*, {bind_alias}.canonical_id AS _canonical_id "
        "FROM {parent_tbl} s "
        "JOIN {parent_btbl} {bind_alias} "
        "  ON {bind_alias}.knot_row_id = s._knot_row_id "
        " AND {bind_alias}.valid_to IS NULL"
        "{joins} "
        "WHERE ({where})"
    ).format(
        view=_table_id(cls),
        bind_alias=sql.Identifier("b"),
        parent_tbl=_table_id(parent),
        parent_btbl=_bindings_table_id(parent),
        joins=joins_sql,
        where=where_sql,
    )
    # CREATE VIEW DDL cannot use server-side parameters ($1, $2...) because
    # PostgreSQL can't infer their types in a view body.  Use a ClientCursor
    # to mogrify the statement (parameter values inlined as SQL literals by
    # the psycopg client) and execute the fully-rendered DDL string.
    from psycopg import ClientCursor
    ccur = ClientCursor(conn)
    rendered = ccur.mogrify(view_stmt, ctx.params)
    conn.execute(rendered)


@emit_ddl.register
def _(change: DropDefinedClass, conn: psycopg.Connection) -> None:
    conn.execute(
        sql.SQL("DROP VIEW IF EXISTS {t} CASCADE").format(
            t=sql.Identifier(_SCHEMA, change.class_name.lower()),
        )
    )


@emit_ddl.register
def _(change: AddSlot, conn: psycopg.Connection) -> None:
    stmt = sql.SQL("ALTER TABLE {table} ADD COLUMN {col} {pgtype} NULL").format(
        table=_table_id(change.cls),
        col=sql.Identifier(change.slot.name),
        pgtype=sql.SQL(_slot_pg_type(change.slot)),
    )
    conn.execute(stmt)


@emit_ddl.register
def _(change: DropSlot, conn: psycopg.Connection) -> None:
    stmt = sql.SQL("ALTER TABLE {table} DROP COLUMN {col}").format(
        table=_table_id(change.cls),
        col=sql.Identifier(change.slot_name),
    )
    conn.execute(stmt)


@emit_ddl.register
def _(change: ChangeSlotType, conn: psycopg.Connection) -> None:
    stmt = sql.SQL("ALTER TABLE {table} ALTER COLUMN {col} TYPE {pgtype}").format(
        table=_table_id(change.cls),
        col=sql.Identifier(change.slot.name),
        pgtype=sql.SQL(change.new_pg_type),
    )
    conn.execute(stmt)


@emit_ddl.register
def _(change: ChangeSlotRequired, conn: psycopg.Connection) -> None:
    return  # API-enforced; no DDL


# ─── Apply ──────────────────────────────────────────────────────────────────


# ─── Destructive-change classification ──────────────────────────────────────

# Changes that destroy or rewrite stored data without a backfill path.
# DropSlot loses a column's data; DropClass loses an entire table;
# ChangeSlotType issues a raw ALTER COLUMN TYPE and may reject existing
# data. The publish gate refuses these unless allow_destructive is
# explicitly set.
_DESTRUCTIVE_CHANGE_TYPES: tuple[type[Change], ...] = (
    DropClass,
    DropSlot,
    ChangeSlotType,
    DropDefinedClass,
)


def is_destructive(change: Change) -> bool:
    return isinstance(change, _DESTRUCTIVE_CHANGE_TYPES)


def apply_changes(conn: psycopg.Connection, changes: list[Change]) -> None:
    """Apply a precomputed list of changes (used after diff + safety check).

    Order:
      1. Drops (DropClass, DropSlot, DropDefinedClass) — before adds so that a
         class renamed via drop+add with the same lowercase name doesn't try to
         CREATE TABLE before the DROP runs.
      2. Concrete class adds (AddClass, AddSlot, etc.) — tables must exist before
         the VIEW DDL for defined classes references them.
      3. Defined class adds (AddDefinedClass) — CREATE OR REPLACE VIEW runs after
         all parent tables are in place.
    """
    drops = [c for c in changes if isinstance(c, (DropClass, DropSlot, DropDefinedClass))]
    concrete_adds = [c for c in changes if not isinstance(c, (DropClass, DropSlot, DropDefinedClass, AddDefinedClass))]
    defined_adds = [c for c in changes if isinstance(c, AddDefinedClass)]
    for change in drops + concrete_adds + defined_adds:
        emit_ddl(change, conn)


def apply_migration(
    conn: psycopg.Connection,
    prev: Spec | None,
    candidate: Spec,
) -> list[Change]:
    """Diff the two specs and apply the resulting DDL. Returns the changes run.

    Caller is responsible for any destructive-change gating; see
    ``is_destructive`` and ``apply_changes`` for the split-control variant.
    """
    changes = diff_specs(prev, candidate)
    apply_changes(conn, changes)
    return changes
