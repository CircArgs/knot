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

Change-type taxonomy
--------------------

``diff_specs`` emits a typed ``Change`` for every CANONICAL field edit on
every entity. Three buckets:

A. **DDL-destructive.** Storage shape changes; an ALTER may fail or
   silently lose data. Listed in ``_DESTRUCTIVE_CHANGE_TYPES`` and
   gated by ``allow_destructive`` at publish.

B. **Data-revalidation.** Constraint tightens; previously-valid rows
   may now violate. The publish gate already runs the constraint check
   over current data on every NEW or CHANGED constraint, so a separate
   ``requires_data_revalidation`` set is not maintained — the constraint
   gate is the canonical revalidation pass. ``ChangeSlotPattern``,
   ``ChangeSlotPermissibleValues``, ``ChangeSlotMinimum``,
   ``ChangeSlotMaximum``, ``ChangeTypePattern``, ``ChangeConstraintBody``
   live in this bucket. They produce no DDL.

C. **Spec-only / runtime-behavior.** No DDL, no revalidation. Just
   changes runtime behavior. ``ChangeSlotResolutionPolicy``,
   ``ChangeConstraintSeverity``, ``ChangeSlotDerivation`` (body change),
   ``ChangeClassDefinition`` (body change for an already-defined class —
   handled by CREATE OR REPLACE VIEW elsewhere).

RUNTIME fields (``_RUNTIME_FIELDS`` in ``knot.spec.canonical``) are
excluded from the content hash and therefore never reach this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from knot.spec import OntologyClass, Slot, Spec
from knot.spec import effective_slots as _effective_slots
from knot.spec import is_stored as _is_stored
from knot.spec.compile.postgres._types import slot_pg_type as _slot_pg_type

from ._naming import (
    bindings_table_id as _bindings_table_id,
)
from ._naming import (
    schema,
)
from ._naming import (
    table_id as _table_id,
)


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
        "CREATE INDEX IF NOT EXISTS {idx} ON {table} (canonical_id) WHERE valid_to IS NULL"
    ).format(idx=_bindings_index_id(cls), table=_bindings_table_id(cls))


def _bindings_unique_current_id(cls: OntologyClass) -> sql.Identifier:
    return sql.Identifier(f"{cls.name.lower()}_bindings_one_current_per_row")


def _bindings_unique_current_sql(cls: OntologyClass) -> sql.Composable:
    # Partial UNIQUE index enforcing exactly one current binding per
    # knot_row_id. Catches any race in merge / split / correction that
    # escapes FOR UPDATE locking.
    return sql.SQL(
        "CREATE UNIQUE INDEX IF NOT EXISTS {idx} ON {table} (knot_row_id) WHERE valid_to IS NULL"
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
        table=_table_id(cls),
        body=body,
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


# ─── Type-level changes (TypeDefinition) ────────────────────────────────────


@dataclass
class ChangeTypeBase(Change):
    """`TypeDefinition.base` changed — every slot using this type sees a column
    type change. Bucket A (DDL-destructive); the actual ALTER COLUMNs ride on
    the per-slot ``ChangeSlotType`` records emitted alongside this."""

    type_name: str
    old_base: str | None
    new_base: str | None


@dataclass
class ChangeTypePattern(Change):
    """`TypeDefinition.pattern` changed — existing rows may now violate.
    Bucket B (revalidation, no DDL)."""

    type_name: str
    old_pattern: str | None
    new_pattern: str | None


# ─── Slot-level changes (canonical fields) ──────────────────────────────────


@dataclass
class ChangeSlotPattern(Change):
    """Bucket B — pattern tightening can invalidate existing rows."""

    slot_name: str
    old_pattern: str | None
    new_pattern: str | None


@dataclass
class ChangeSlotPermissibleValues(Change):
    """Bucket B — narrowing the enum can invalidate existing rows; widening is fine."""

    slot_name: str
    old_values: list[str] | None
    new_values: list[str] | None


@dataclass
class ChangeSlotMinimum(Change):
    """Bucket B — tightening minimum_value can invalidate existing rows."""

    slot_name: str
    old_value: float | None
    new_value: float | None


@dataclass
class ChangeSlotMaximum(Change):
    """Bucket B — tightening maximum_value can invalidate existing rows."""

    slot_name: str
    old_value: float | None
    new_value: float | None


@dataclass
class ChangeSlotMultivalued(Change):
    """Bucket A — column shape T vs T[] is a destructive ALTER."""

    slot_name: str
    old_value: bool
    new_value: bool


@dataclass
class ChangeSlotIdentifier(Change):
    """Bucket A — identifier flag affects PK / source-keying semantics."""

    slot_name: str
    old_value: bool
    new_value: bool


@dataclass
class ChangeSlotResolutionPolicy(Change):
    """Bucket C — runtime resolution policy; no DDL, no revalidation."""

    slot_name: str
    old_value: str
    new_value: str


@dataclass
class ChangeSlotDerivation(Change):
    """Derivation body change.

    ``had_derivation_before`` / ``has_derivation_now`` capture transitions
    between stored and derived (those produce ``AddSlot``/``DropSlot`` via
    the stored-slot diff); ``derivation_changed`` captures a body-only
    change between two derivation expressions. The body itself is opaque
    (an ExprNode by object id), so we surface only the booleans.

    Bucket C — derivation projection runs at query time. No DDL."""

    slot_name: str
    had_derivation_before: bool
    has_derivation_now: bool
    derivation_changed: bool


# ─── Class-level changes ────────────────────────────────────────────────────


@dataclass
class ChangeClassAbstract(Change):
    """Bucket A — flipping abstract toggles whether the class has a table."""

    class_name: str
    old_value: bool
    new_value: bool


@dataclass
class ChangeClassIsA(Change):
    """Bucket A — for defined classes the parent is the VIEW source; for
    concrete subclasses the GraphQL surface treats is_a as inherited
    structure. Either way storage semantics shift."""

    class_name: str
    old_parent: str | None
    new_parent: str | None


@dataclass
class ChangeClassMixins(Change):
    """Bucket A — mixin set changes ``effective_slots``. The actual column
    add/drop rides on ``AddSlot`` / ``DropSlot`` records emitted alongside
    this; this record carries the mixin-list metadata so the migration
    log is auditable.

    Marked destructive even though slot-level DDL also fires: dropping a
    mixin without ``allow_destructive`` should fail loudly, and slot-level
    drop may be subsumed by ``DropClass`` ordering."""

    class_name: str
    old_mixins: list[str]
    new_mixins: list[str]


@dataclass
class ChangeClassDefinition(Change):
    """Bucket C — defined-class VIEW body change.

    For an already-defined class, a definition body change is a
    ``CREATE OR REPLACE VIEW`` (no data lost). For a concrete↔defined
    transition, the diff emits ``DropClass``+``AddDefinedClass`` (or
    inverse) instead of this record. So this record only fires when both
    prev and cand are defined classes and the body changed.

    Body is opaque (ExprNode by object id); we surface only booleans."""

    class_name: str
    had_definition_before: bool
    has_definition_now: bool
    definition_changed: bool


# ─── Source-level changes ───────────────────────────────────────────────────


@dataclass
class AddSource(Change):
    source_name: str
    entity_class: str
    identifier_slot: str


@dataclass
class DropSource(Change):
    """Bucket A — rows from this source become orphaned."""

    source_name: str


@dataclass
class ChangeSourceEntityClass(Change):
    """Bucket A — rows now logically belong to a different table."""

    source_name: str
    old_class: str
    new_class: str


@dataclass
class ChangeSourceIdentifierSlot(Change):
    """Bucket A — rows are now keyed by a different slot."""

    source_name: str
    old_slot: str
    new_slot: str


# ─── Constraint-level changes ───────────────────────────────────────────────


@dataclass
class AddConstraint(Change):
    constraint_name: str
    primary: str


@dataclass
class DropConstraint(Change):
    constraint_name: str


@dataclass
class ChangeConstraintPrimary(Change):
    """Bucket B — constraint applies to a different class; revalidate."""

    constraint_name: str
    old_primary: str
    new_primary: str


@dataclass
class ChangeConstraintBody(Change):
    """Bucket B — body changed; the publish-gate constraint pass revalidates."""

    constraint_name: str


@dataclass
class ChangeConstraintSeverity(Change):
    """Bucket C — severity is purely runtime."""

    constraint_name: str
    old_severity: str
    new_severity: str


# ─── Diff visitor ───────────────────────────────────────────────────────────


def _stored_slots_by_name(cls: OntologyClass) -> dict[str, Slot]:
    # Walks own + mixin slots so adding/removing a mixin shows up as
    # AddSlot/DropSlot in the diff.
    return {s.name: s for s in _effective_slots(cls) if _is_stored(s)}


def _pv_texts(values: list[Any] | None) -> list[str] | None:
    if values is None:
        return None
    return [getattr(v, "text", str(v)) for v in values]


def _enum_value(v: Any) -> str:
    """Render an enum / StrEnum field as its string value."""
    return getattr(v, "value", str(v))


def _diff_types(prev: Spec | None, candidate: Spec) -> list[Change]:
    """Per-TypeDefinition field-level diff.

    Type Adds/Drops aren't on the change-event surface — types only matter
    via the slots that reference them, and slot-side ChangeSlotType already
    fires when the bound pg type shifts. We do emit per-field records for
    type edits so the migration log is complete and the destructive gate
    catches base swaps."""
    changes: list[Change] = []
    prev_types = {t.name: t for t in (prev.types if prev else [])}
    cand_types = {t.name: t for t in candidate.types}
    for name in cand_types.keys() & prev_types.keys():
        pt, ct = prev_types[name], cand_types[name]
        if pt.base != ct.base:
            changes.append(ChangeTypeBase(type_name=name, old_base=pt.base, new_base=ct.base))
        if pt.pattern != ct.pattern:
            changes.append(
                ChangeTypePattern(type_name=name, old_pattern=pt.pattern, new_pattern=ct.pattern)
            )
    return changes


def _diff_slot_fields(prev_slot: Slot, cand_slot: Slot) -> list[Change]:
    """Compare every CANONICAL field on two same-named slots."""
    out: list[Change] = []
    name = cand_slot.name

    if prev_slot.pattern != cand_slot.pattern:
        out.append(
            ChangeSlotPattern(
                slot_name=name,
                old_pattern=prev_slot.pattern,
                new_pattern=cand_slot.pattern,
            )
        )

    prev_pv = _pv_texts(prev_slot.permissible_values)
    cand_pv = _pv_texts(cand_slot.permissible_values)
    if prev_pv != cand_pv:
        out.append(
            ChangeSlotPermissibleValues(slot_name=name, old_values=prev_pv, new_values=cand_pv)
        )

    if prev_slot.minimum_value != cand_slot.minimum_value:
        out.append(
            ChangeSlotMinimum(
                slot_name=name,
                old_value=prev_slot.minimum_value,
                new_value=cand_slot.minimum_value,
            )
        )
    if prev_slot.maximum_value != cand_slot.maximum_value:
        out.append(
            ChangeSlotMaximum(
                slot_name=name,
                old_value=prev_slot.maximum_value,
                new_value=cand_slot.maximum_value,
            )
        )
    if prev_slot.multivalued != cand_slot.multivalued:
        out.append(
            ChangeSlotMultivalued(
                slot_name=name,
                old_value=prev_slot.multivalued,
                new_value=cand_slot.multivalued,
            )
        )
    if prev_slot.identifier != cand_slot.identifier:
        out.append(
            ChangeSlotIdentifier(
                slot_name=name,
                old_value=prev_slot.identifier,
                new_value=cand_slot.identifier,
            )
        )
    if prev_slot.resolution_policy != cand_slot.resolution_policy:
        out.append(
            ChangeSlotResolutionPolicy(
                slot_name=name,
                old_value=_enum_value(prev_slot.resolution_policy),
                new_value=_enum_value(cand_slot.resolution_policy),
            )
        )
    had = prev_slot.derivation is not None
    has = cand_slot.derivation is not None
    if had or has:
        derivation_changed = prev_slot.derivation is not cand_slot.derivation and had and has
        if had != has or derivation_changed:
            out.append(
                ChangeSlotDerivation(
                    slot_name=name,
                    had_derivation_before=had,
                    has_derivation_now=has,
                    derivation_changed=derivation_changed,
                )
            )
    return out


def _diff_sources(prev: Spec | None, candidate: Spec) -> list[Change]:
    changes: list[Change] = []
    prev_sources = {s.name: s for s in (prev.sources if prev else [])}
    cand_sources = {s.name: s for s in candidate.sources}

    for name in cand_sources.keys() - prev_sources.keys():
        s = cand_sources[name]
        changes.append(
            AddSource(
                source_name=name,
                entity_class=s.entity_class.name,
                identifier_slot=s.identifier_slot.name,
            )
        )
    for name in prev_sources.keys() - cand_sources.keys():
        changes.append(DropSource(source_name=name))
    for name in cand_sources.keys() & prev_sources.keys():
        ps, cs = prev_sources[name], cand_sources[name]
        if ps.entity_class.name != cs.entity_class.name:
            changes.append(
                ChangeSourceEntityClass(
                    source_name=name,
                    old_class=ps.entity_class.name,
                    new_class=cs.entity_class.name,
                )
            )
        if ps.identifier_slot.name != cs.identifier_slot.name:
            changes.append(
                ChangeSourceIdentifierSlot(
                    source_name=name,
                    old_slot=ps.identifier_slot.name,
                    new_slot=cs.identifier_slot.name,
                )
            )
    return changes


def _diff_constraints(prev: Spec | None, candidate: Spec) -> list[Change]:
    changes: list[Change] = []
    prev_cons = {c.name: c for c in (prev.constraints if prev else [])}
    cand_cons = {c.name: c for c in candidate.constraints}

    for name in cand_cons.keys() - prev_cons.keys():
        c = cand_cons[name]
        changes.append(AddConstraint(constraint_name=name, primary=c.primary.name))
    for name in prev_cons.keys() - cand_cons.keys():
        changes.append(DropConstraint(constraint_name=name))
    for name in cand_cons.keys() & prev_cons.keys():
        pc, cc = prev_cons[name], cand_cons[name]
        if pc.primary.name != cc.primary.name:
            changes.append(
                ChangeConstraintPrimary(
                    constraint_name=name,
                    old_primary=pc.primary.name,
                    new_primary=cc.primary.name,
                )
            )
        if pc.body is not cc.body:
            # Body is an ExprNode — compare by object identity (same caveat
            # as ChangeSlotDerivation; the publish-time content hash already
            # detects body changes via JCS bytes). When identity differs we
            # emit the record so the destructive/revalidation gate sees it.
            changes.append(ChangeConstraintBody(constraint_name=name))
        if pc.severity != cc.severity:
            changes.append(
                ChangeConstraintSeverity(
                    constraint_name=name,
                    old_severity=_enum_value(pc.severity),
                    new_severity=_enum_value(cc.severity),
                )
            )
    return changes


def diff_specs(prev: Spec | None, candidate: Spec) -> list[Change]:
    changes: list[Change] = []
    prev_classes = {c.name: c for c in (prev.classes if prev else [])}
    cand_classes = {c.name: c for c in candidate.classes}

    # Type-level changes go first; they may shadow per-slot ChangeSlotType
    # at consumption time but emitting both is fine (audit log is verbose,
    # destructive gate hits on either).
    changes.extend(_diff_types(prev, candidate))

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

        # Class-level field changes (abstract / is_a / mixins) — emitted
        # regardless of the abstract / defined status switch. ``abstract``
        # toggling between true/false is itself a category-A change.
        if prev_cls.abstract != cand_cls.abstract:
            changes.append(
                ChangeClassAbstract(
                    class_name=name,
                    old_value=prev_cls.abstract,
                    new_value=cand_cls.abstract,
                )
            )

        prev_parent = prev_cls.is_a.name if prev_cls.is_a is not None else None
        cand_parent = cand_cls.is_a.name if cand_cls.is_a is not None else None
        if prev_parent != cand_parent:
            changes.append(
                ChangeClassIsA(
                    class_name=name,
                    old_parent=prev_parent,
                    new_parent=cand_parent,
                )
            )

        prev_mixins = [m.name for m in prev_cls.mixins]
        cand_mixins = [m.name for m in cand_cls.mixins]
        if prev_mixins != cand_mixins:
            changes.append(
                ChangeClassMixins(
                    class_name=name,
                    old_mixins=prev_mixins,
                    new_mixins=cand_mixins,
                )
            )

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
            if prev_cls.definition is not cand_cls.definition:
                changes.append(
                    ChangeClassDefinition(
                        class_name=name,
                        had_definition_before=True,
                        has_definition_now=True,
                        definition_changed=True,
                    )
                )
            continue

        # Both concrete — diff slots.
        # ``stored_slots`` drives AddSlot / DropSlot / ChangeSlotType (the
        # DDL-relevant subset). ``all_slots`` (incl. derived) drives the
        # per-field diff so a derivation-body or runtime-policy edit on a
        # derived slot still produces a Change record.
        prev_stored = _stored_slots_by_name(prev_cls)
        cand_stored = _stored_slots_by_name(cand_cls)
        prev_all = {s.name: s for s in _effective_slots(prev_cls)}
        cand_all = {s.name: s for s in _effective_slots(cand_cls)}

        for s_name in cand_stored.keys() - prev_stored.keys():
            changes.append(AddSlot(cls=cand_cls, slot=cand_stored[s_name]))
        for s_name in prev_stored.keys() - cand_stored.keys():
            changes.append(DropSlot(cls=cand_cls, slot_name=s_name))
        for s_name in cand_stored.keys() & prev_stored.keys():
            ps, cs = prev_stored[s_name], cand_stored[s_name]
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

        # Per-field diff over the *full* effective-slot intersection — covers
        # derived slots whose pattern / resolution_policy / derivation body
        # changed without storage shifting.
        for s_name in cand_all.keys() & prev_all.keys():
            ps, cs = prev_all[s_name], cand_all[s_name]
            changes.extend(_diff_slot_fields(ps, cs))

    changes.extend(_diff_sources(prev, candidate))
    changes.extend(_diff_constraints(prev, candidate))
    return changes


# ─── DDL emission (async, type-dispatched) ──────────────────────────────────


async def emit_ddl(change: Change, conn: psycopg.AsyncConnection) -> None:
    """Dispatch DDL execution for a single change against the async connection."""
    if isinstance(change, AddClass):
        await conn.execute(_create_source_table_sql(change.cls))
        await conn.execute(_bindings_create_sql(change.cls))
        await conn.execute(_bindings_index_sql(change.cls))
        await conn.execute(_bindings_unique_current_sql(change.cls))

    elif isinstance(change, DropClass):
        cls_lower = change.class_name.lower()
        if change.is_view:
            await conn.execute(
                sql.SQL("DROP VIEW IF EXISTS {t} CASCADE").format(
                    t=sql.Identifier(schema(), cls_lower),
                )
            )
            return
        await conn.execute(
            sql.SQL("DROP TABLE IF EXISTS {t} CASCADE").format(
                t=sql.Identifier(schema(), f"{cls_lower}_bindings"),
            )
        )
        await conn.execute(
            sql.SQL("DROP TABLE IF EXISTS {t} CASCADE").format(
                t=sql.Identifier(schema(), cls_lower),
            )
        )

    elif isinstance(change, AddDefinedClass):
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
        from knot.spec.compile.postgres import CompileContext, compile_predicate

        cls = change.cls
        if cls.is_a is None:
            raise ValueError(f"Defined class {cls.name!r} must have is_a set to a parent class.")
        parent = cls.is_a

        ctx = CompileContext(primary_class=parent, alias="s")
        where_sql = compile_predicate(cls.definition, ctx)

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
        # PostgreSQL can't infer their types in a view body.  Use an
        # AsyncClientCursor to mogrify (parameter values inlined as SQL
        # literals by the psycopg client) and execute the fully-rendered DDL.
        from psycopg import AsyncClientCursor

        ccur = AsyncClientCursor(conn)
        rendered = ccur.mogrify(view_stmt, ctx.params)
        await conn.execute(rendered)

    elif isinstance(change, DropDefinedClass):
        await conn.execute(
            sql.SQL("DROP VIEW IF EXISTS {t} CASCADE").format(
                t=sql.Identifier(schema(), change.class_name.lower()),
            )
        )

    elif isinstance(change, AddSlot):
        stmt = sql.SQL("ALTER TABLE {table} ADD COLUMN {col} {pgtype} NULL").format(
            table=_table_id(change.cls),
            col=sql.Identifier(change.slot.name),
            pgtype=sql.SQL(_slot_pg_type(change.slot)),
        )
        await conn.execute(stmt)

    elif isinstance(change, DropSlot):
        stmt = sql.SQL("ALTER TABLE {table} DROP COLUMN {col}").format(
            table=_table_id(change.cls),
            col=sql.Identifier(change.slot_name),
        )
        await conn.execute(stmt)

    elif isinstance(change, ChangeSlotType):
        stmt = sql.SQL("ALTER TABLE {table} ALTER COLUMN {col} TYPE {pgtype}").format(
            table=_table_id(change.cls),
            col=sql.Identifier(change.slot.name),
            pgtype=sql.SQL(change.new_pg_type),
        )
        await conn.execute(stmt)

    elif isinstance(change, ChangeSlotRequired):
        return  # API-enforced; no DDL

    # Spec-only / runtime-behavior changes — no DDL. These records exist for
    # auditability and to drive the destructive gate / constraint revalidation
    # at publish time.
    elif isinstance(
        change,
        (
            ChangeTypePattern,
            ChangeSlotPattern,
            ChangeSlotPermissibleValues,
            ChangeSlotMinimum,
            ChangeSlotMaximum,
            ChangeSlotResolutionPolicy,
            ChangeSlotDerivation,
            ChangeClassDefinition,
            AddSource,
            DropSource,
            AddConstraint,
            DropConstraint,
            ChangeConstraintPrimary,
            ChangeConstraintBody,
            ChangeConstraintSeverity,
            ChangeClassMixins,
        ),
    ):
        return

    # Bucket-A changes whose DDL emitters aren't wired yet. They MUST gate at
    # the publish layer (``allow_destructive=true``) but the actual table-
    # rewrite DDL is follow-up work; surface a clear NotImplementedError if
    # someone tries to apply them.
    elif isinstance(
        change,
        (
            ChangeTypeBase,
            ChangeSlotMultivalued,
            ChangeSlotIdentifier,
            ChangeClassAbstract,
            ChangeClassIsA,
            ChangeSourceEntityClass,
            ChangeSourceIdentifierSlot,
        ),
    ):
        raise NotImplementedError(
            f"DDL emitter for {type(change).__name__} is not implemented yet; "
            "this change is gated as destructive at publish but the table-rewrite "
            "path is follow-up work."
        )

    else:
        raise TypeError(f"No DDL emitter registered for {type(change).__name__}")


# ─── Apply ──────────────────────────────────────────────────────────────────


# ─── Destructive-change classification ──────────────────────────────────────

# Changes that destroy or rewrite stored data without a backfill path.
# The publish gate refuses these unless allow_destructive is explicitly
# set. Three families:
#
#   - **Drops**: removing a class, slot, defined class, or source forfeits
#     the rows / column / view that hold the data.
#   - **Storage-shape rewrites**: ChangeSlotType (column type), Change-
#     SlotMultivalued (T → T[] or back), ChangeSlotIdentifier (PK / source
#     keying), ChangeClassAbstract (table appears/disappears),
#     ChangeClassIsA / ChangeClassMixins (effective slot set + parent
#     table rewires), ChangeTypeBase (every using slot's column type
#     changes). Source rekey is a logical destructive too — rows now
#     belong to a different class or are keyed by a different slot.
#
# Bucket B (data-revalidation: ChangeSlotPattern, ChangeSlotPermissible-
# Values, min/max, ChangeTypePattern, ChangeConstraintBody) is NOT
# enumerated here. The publish gate already runs every NEW or CHANGED
# constraint over current data; tightenings on those fields surface as
# violations through that pass. Adding a separate revalidation set would
# duplicate the constraint gate's work.
_DESTRUCTIVE_CHANGE_TYPES: tuple[type[Change], ...] = (
    # original drops + type rewrite
    DropClass,
    DropSlot,
    ChangeSlotType,
    DropDefinedClass,
    # type-level rewrites
    ChangeTypeBase,
    # slot-level shape rewrites
    ChangeSlotMultivalued,
    ChangeSlotIdentifier,
    # class-level shape rewrites
    ChangeClassAbstract,
    ChangeClassIsA,
    ChangeClassMixins,
    # source rekey / drop
    DropSource,
    ChangeSourceEntityClass,
    ChangeSourceIdentifierSlot,
)


def is_destructive(change: Change) -> bool:
    return isinstance(change, _DESTRUCTIVE_CHANGE_TYPES)


async def apply_changes(conn: psycopg.AsyncConnection, changes: list[Change]) -> None:
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
    concrete_adds = [
        c
        for c in changes
        if not isinstance(c, (DropClass, DropSlot, DropDefinedClass, AddDefinedClass))
    ]
    defined_adds = [c for c in changes if isinstance(c, AddDefinedClass)]
    for change in drops + concrete_adds + defined_adds:
        await emit_ddl(change, conn)


async def apply_migration(
    conn: psycopg.AsyncConnection,
    prev: Spec | None,
    candidate: Spec,
) -> list[Change]:
    """Diff the two specs and apply the resulting DDL. Returns the changes run.

    Caller is responsible for any destructive-change gating; see
    ``is_destructive`` and ``apply_changes`` for the split-control variant.
    """
    changes = diff_specs(prev, candidate)
    await apply_changes(conn, changes)
    return changes
