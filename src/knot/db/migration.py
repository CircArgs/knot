"""Migration emitter — spec → postgres DDL.

Runs at publish time (after the spec-graph gate). The previously-published
spec IS the previously-applied schema state by construction, so the diff
input is two Pydantic typed trees, not "spec vs unknown DB."

Mapping (locked):
  - schema:           ``knot_data`` (data plane, separate from ``public``)
  - class → table:    lowercase class name (``Movie`` → ``knot_data.movie``)
  - system columns:   underscore-prefixed (``_canonical_id``, ``_source``,
                      ``_source_row_id``, ``_ingest_at``, ``_spec_revision``)
  - slot → column:    range-based postgres type; multivalued → ``T[]``
  - derived slots:    skipped (query-time projections, not stored)
  - primary key:      ``(_source, _source_row_id)``

Identifiers are quoted via ``psycopg.sql.Identifier`` (class + slot names
come from the API and must be safe). Postgres types come from a fixed
whitelist; type names from the spec are never spliced raw.

Diffing:
  - ``diff_specs(prev, candidate) → list[Change]`` — typed dataclasses, one
    per change between two specs (or first-publish when prev is None).
  - ``emit_ddl(change, conn)`` — single-dispatch over the typed Change
    tree; the same machinery as impact analysis (no parallel meta-structure).
  - ``apply_migration(conn, prev, candidate)`` — runs the diff + applies.

Open today (gaps that surface as postgres errors at apply time): type
widening with non-trivial cast (USING clause), scalar↔array switch,
required toggle on populated tables, drop-column confirmation gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import singledispatch

import psycopg
from psycopg import sql

from knot.ontology import OntologyClass, Slot, Spec, TypeDefinition


_SCHEMA = "knot_data"


_PG_TYPE_FOR_BASE: dict[str, str] = {
    "str":      "TEXT",
    "string":   "TEXT",
    "int":      "BIGINT",
    "integer":  "BIGINT",
    "float":    "DOUBLE PRECISION",
    "bool":     "BOOLEAN",
    "boolean":  "BOOLEAN",
    "datetime": "TIMESTAMPTZ",
    "date":     "DATE",
}


def _slot_pg_type(slot: Slot) -> str:
    """Postgres column type for a stored slot. Returns a whitelist value
    (never a user-supplied string)."""
    if isinstance(slot.range, OntologyClass):
        base = "TEXT"
    elif isinstance(slot.range, TypeDefinition):
        key = (slot.range.base or "str").lower()
        base = _PG_TYPE_FOR_BASE.get(key, "TEXT")
    else:
        base = "TEXT"
    return f"{base}[]" if slot.multivalued else base


def _table_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(_SCHEMA, cls.name.lower())


def _is_stored(slot: Slot) -> bool:
    return getattr(slot, "derivation", None) is None


# System columns — knot-managed; underscore-prefixed so they can't collide
# with user slot names. ``_spec_revision`` references public.spec_revisions
# so a row's lineage joins straight to the revision that shaped its meaning.
_SYSTEM_COLUMNS_SQL = sql.SQL(
    "_canonical_id TEXT NOT NULL, "
    "_source TEXT NOT NULL, "
    "_source_row_id TEXT NOT NULL, "
    "_ingest_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
    "_spec_revision INTEGER NOT NULL REFERENCES public.spec_revisions(revision), "
    "PRIMARY KEY (_source, _source_row_id)"
)


def _create_table_sql(cls: OntologyClass) -> sql.Composable:
    # Slot columns are always nullable at the DB level; ``required`` is
    # enforced at the API ingest layer (knot.api.graph.ingest), because
    # user-correction rows in the same table are partial — only the
    # corrected slot has a value, the rest are NULL.
    user_cols: list[sql.Composable] = []
    for slot in cls.slots:
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
    """Marker base for typed migration events. Subclasses dispatch via emit_ddl."""


@dataclass
class AddClass(Change):
    cls: OntologyClass


@dataclass
class DropClass(Change):
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
    """Slot's postgres column type changed (range or multivalued flip)."""
    cls: OntologyClass
    slot: Slot
    prev_pg_type: str
    new_pg_type: str


# Slot.required is API-enforced now (not DB-enforced), so changes to
# the required flag don't emit DDL. Kept on the change-event surface
# for impact-analysis completeness, but the emit_ddl handler is a no-op.
@dataclass
class ChangeSlotRequired(Change):
    cls: OntologyClass
    slot_name: str
    new_required: bool


# ─── Diff visitor ───────────────────────────────────────────────────────────


def _stored_slots_by_name(cls: OntologyClass) -> dict[str, Slot]:
    return {s.name: s for s in cls.slots if _is_stored(s)}


def diff_specs(prev: Spec | None, candidate: Spec) -> list[Change]:
    """Walk both typed trees, emit a list of Change events.

    First-publish (``prev is None``) yields one ``AddClass`` per concrete
    class. Subsequent publishes diff class-by-class and slot-by-slot.
    """
    changes: list[Change] = []
    prev_classes = {c.name: c for c in (prev.classes if prev else [])}
    cand_classes = {c.name: c for c in candidate.classes}

    for name in cand_classes.keys() - prev_classes.keys():
        c = cand_classes[name]
        if not c.abstract:
            changes.append(AddClass(cls=c))

    for name in prev_classes.keys() - cand_classes.keys():
        c = prev_classes[name]
        if not c.abstract:
            changes.append(DropClass(class_name=name))

    for name in cand_classes.keys() & prev_classes.keys():
        prev_cls, cand_cls = prev_classes[name], cand_classes[name]
        if cand_cls.abstract or prev_cls.abstract:
            continue  # abstract→concrete or concrete→abstract: skip for now
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
    conn.execute(_create_table_sql(change.cls))


@emit_ddl.register
def _(change: DropClass, conn: psycopg.Connection) -> None:
    stmt = sql.SQL("DROP TABLE IF EXISTS {table} CASCADE").format(
        table=sql.Identifier(_SCHEMA, change.class_name.lower()),
    )
    conn.execute(stmt)


@emit_ddl.register
def _(change: AddSlot, conn: psycopg.Connection) -> None:
    # See _create_table_sql — slot columns are nullable; required-ness
    # is API-enforced because user-correction rows are partial.
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
    # Postgres ALTER COLUMN TYPE attempts an implicit cast; non-trivial
    # casts (text → int) error here, which is correct (loud failure).
    stmt = sql.SQL("ALTER TABLE {table} ALTER COLUMN {col} TYPE {pgtype}").format(
        table=_table_id(change.cls),
        col=sql.Identifier(change.slot.name),
        pgtype=sql.SQL(change.new_pg_type),
    )
    conn.execute(stmt)


@emit_ddl.register
def _(change: ChangeSlotRequired, conn: psycopg.Connection) -> None:
    # No-op: required-ness is API-enforced, not in DB schema.
    return


# ─── Apply ──────────────────────────────────────────────────────────────────


def apply_migration(
    conn: psycopg.Connection,
    prev: Spec | None,
    candidate: Spec,
) -> list[Change]:
    """Diff the two specs and apply the resulting DDL. Returns the changes run."""
    changes = diff_specs(prev, candidate)
    for change in changes:
        emit_ddl(change, conn)
    return changes
