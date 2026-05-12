"""Migration emitter — spec → postgres DDL.

Per-class storage is **two tables**, per the SCD2 binding design:

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
  - slot → column:    TypeExpression-based postgres type (whitelist)
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
   ``ChangeSlotMaximum``, ``ChangeConstraintBody``
   live in this bucket. They produce no DDL.

C. **Spec-only / runtime-behavior.** No DDL, or DDL that doesn't lose
   data. ``ChangeSlotResolutionPolicy``, ``ChangeConstraintSeverity``,
   ``ChangeSlotDerivation`` (body change), ``ChangeClassDefinition``
   (body change for an already-defined class — ``CREATE OR REPLACE
   VIEW`` is idempotent), ``ChangeClassMixins`` (audit-only — the
   actual column adds/drops ride on ``AddSlot``/``DropSlot`` records),
   ``ChangeSlotIdentifier`` (the storage PK is ``(_source,
   _source_row_id)``; the ``identifier`` flag is ER/SCD2 advisory and
   doesn't drive DDL), ``AddSource`` (new pathway, doesn't lose data),
   ``ChangeSourceTrustScore`` (runtime trust, no DDL),
   ``ChangeSourceSlotPrior`` (runtime prior, no DDL).
   ``ChangeClassIsA`` is here for concrete classes (no DDL — own table,
   own slots) and for defined-class body changes (``CREATE OR REPLACE
   VIEW``); the destructive transitions (concrete↔defined) are caught
   instead by the ``Drop*`` records the diff emits.

   **Note on DropSource (Bucket A).** ``DropSource`` is *destructive*:
   rows in ``knot_data.<class>`` that belonged to the removed source
   become orphaned — their ``_source`` value references a source that
   no longer exists in the spec.  No column or table is dropped (hence
   the no-op DDL), but data integrity is silently compromised.
   ``DropSource`` therefore lives in ``_DESTRUCTIVE_CHANGE_TYPES`` and
   requires ``allow_destructive=True`` at publish.  It is NOT Bucket C.

RUNTIME fields (``_RUNTIME_FIELDS`` in ``knot.spec.canonical``) are
excluded from the content hash and therefore never reach this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg import sql

from knot.spec import OntologyClass, Slot, SourceBinding, Spec
from knot.spec import effective_slots as _effective_slots
from knot.spec import is_stored as _is_stored
from knot.spec.compile.postgres._dispatch import CompilerError
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


def _required_check_name(cls: OntologyClass, slot: Slot) -> str:
    """Constraint name for the required-slot CHECK.

    ``<class>_<slot>_required_chk`` — lowercase, safe within postgres's
    63-byte identifier limit (class names are capped at 62 chars by the API,
    slot names likewise).
    """
    return f"{cls.name.lower()}_{slot.name}_required_chk"


def _required_check_sql(cls: OntologyClass, slot: Slot) -> sql.Composable:
    """Idempotent ``ADD CONSTRAINT … CHECK`` for a required slot.

    The user-corrections source is exempted so partial correction rows
    (which only supply changed fields) can always be written.

    Uses a ``DO $$`` block that skips the ALTER when the constraint already
    exists — postgres 16 has no ``ADD CONSTRAINT IF NOT EXISTS`` for checks.
    """
    from knot.spec.compile.postgres._naming import user_corrections_source

    chk_name = _required_check_name(cls, slot)
    tbl_schema = schema()
    tbl_name = cls.name.lower()
    col_name = slot.name
    uc_src = user_corrections_source()

    # Build the raw SQL string for the DO block (identifiers already safe:
    # class + slot names pass the API regex ^[A-Za-z_][A-Za-z0-9_]{0,62}$).
    do_body = (
        f"BEGIN "
        f"  IF NOT EXISTS ("
        f"    SELECT 1 FROM pg_constraint c "
        f"    JOIN pg_class r ON r.oid = c.conrelid "
        f"    JOIN pg_namespace n ON n.oid = r.relnamespace "
        f"    WHERE c.conname = '{chk_name}' "
        f"    AND n.nspname = '{tbl_schema}' "
        f"    AND r.relname = '{tbl_name}'"
        f"  ) THEN "
        f"    ALTER TABLE {tbl_schema}.{tbl_name} ADD CONSTRAINT {chk_name} "
        f"    CHECK (_source = '{uc_src}' OR {col_name} IS NOT NULL); "
        f"  END IF; "
        f"END"
    )
    return sql.SQL("DO $$ {body} $$").format(body=sql.SQL(do_body))


def _required_check_drop_sql(cls: OntologyClass, slot: Slot) -> sql.Composable:
    """``DROP CONSTRAINT IF EXISTS`` for a required-slot CHECK."""
    return sql.SQL(
        "ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {chk}"
    ).format(
        tbl=_table_id(cls),
        chk=sql.Identifier(_required_check_name(cls, slot)),
    )


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
class RenameSlot(Change):
    """Bucket C — rename a stored column without touching data.

    Emits ``ALTER TABLE … RENAME COLUMN old_name TO new_name``.
    Also renames the required-slot CHECK constraint (if one was emitted)
    by dropping the old name and re-adding it under the new name — postgres
    has no RENAME CONSTRAINT, so the workaround is DROP + ADD via DO $$.

    This change is produced by ``diff_specs`` only when the caller passes
    a ``renames`` hint mapping ``{class_name: {old_slot_name: new_slot_name}}``.
    Without that hint, a slot name change appears as ``DropSlot + AddSlot``
    (destructive).
    """

    cls: OntologyClass
    slot: Slot          # candidate-side slot object (carries new name + required flag)
    old_name: str
    new_name: str


@dataclass
class ChangeSlotTypeExpression(Change):
    """Bucket A — the slot's TypeExpression changed, which means the postgres
    column type changed. Carries prev and new pg types for the ALTER COLUMN."""

    cls: OntologyClass
    slot: Slot
    prev_pg_type: str
    new_pg_type: str


@dataclass
class ChangeSlotRequired(Change):
    """Bucket B (false→true) / Bucket C (true→false).

    false → true: adds a CHECK constraint exempting ``_user_corrections`` rows.
      The preflight gate verifies no NULL violations exist before emitting
      the ALTER TABLE.
    true → false: drops the CHECK constraint (no data loss).

    The slot object on the candidate side is needed by the emitter for
    the constraint name and column identifier.
    """

    cls: OntologyClass
    slot: Slot
    slot_name: str
    new_required: bool


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
class ChangeSlotIdentifier(Change):
    """Bucket C — the actual PK on ``knot_data.<class>`` is
    ``(_source, _source_row_id)``, NOT the slot marked ``identifier=True``.
    The ``identifier`` flag is advisory at the storage layer — it drives ER
    bindings and SCD2 semantics but emits no DDL. Audit-only record."""

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
    """Bucket A — flipping abstract toggles whether the class has a table.

    ``cls`` is the candidate-side class object so the emitter can rebuild
    the source table when going abstract → concrete (it needs slots, not
    just the name)."""

    cls: OntologyClass
    class_name: str
    old_value: bool
    new_value: bool


@dataclass
class ChangeClassIsA(Change):
    """Mostly bucket C.

    For a **concrete** class, ``is_a`` is structural-inheritance metadata
    consumed by the GraphQL surface; ``effective_slots`` walks ``mixins``
    but NOT ``is_a``, so the child's table is unaffected — no DDL.

    For a **defined** class with a body change, ``is_a`` drives the
    VIEW's source class; we re-emit ``CREATE OR REPLACE VIEW``.

    The destructive transitions (concrete↔defined, parent gone from spec
    entirely) are handled by the ``Add/DropDefinedClass`` /
    ``Add/DropClass`` records the diff emits alongside this one. So the
    record itself is not destructive.

    ``cls`` is the candidate-side class object so the emitter can dispatch
    on its definition status (concrete vs defined)."""

    cls: OntologyClass
    class_name: str
    old_parent: str | None
    new_parent: str | None


@dataclass
class ChangeClassMixins(Change):
    """Bucket C — mixin set changes ``effective_slots``. The actual column
    add/drop rides on ``AddSlot`` / ``DropSlot`` records emitted alongside
    this; this record carries the mixin-list metadata so the migration
    log is auditable but emits no DDL of its own. The slot-level
    ``DropSlot`` records gate ``allow_destructive`` for a mixin removal."""

    class_name: str
    old_mixins: list[str]
    new_mixins: list[str]


@dataclass
class ChangeClassDefinition(Change):
    """Bucket C — defined-class VIEW body change. ``CREATE OR REPLACE
    VIEW`` is idempotent — no data lost.

    For an already-defined class, a definition body change re-emits the
    VIEW body. For a concrete↔defined transition, the diff emits
    ``DropClass``+``AddDefinedClass`` (or inverse) instead of this record.
    So this record only fires when both prev and cand are defined classes
    and the body changed.

    Body is opaque (ExprNode by object id); we surface only booleans.

    ``cls`` is the candidate-side class object so the emitter can re-emit
    the VIEW with the new compiled body."""

    cls: OntologyClass
    class_name: str
    had_definition_before: bool
    has_definition_now: bool
    definition_changed: bool


# ─── Source-level changes ───────────────────────────────────────────────────


@dataclass
class AddSource(Change):
    """Bucket C — a new named source (thin). No DDL; no data loss."""

    source_name: str


@dataclass
class DropSource(Change):
    """Bucket A — rows from this source become orphaned when source removed.

    Requires allow_destructive=True at publish.
    """

    source_name: str


# ─── SourceBinding-level changes ────────────────────────────────────────────


@dataclass
class AddSourceBinding(Change):
    """Bucket C — new (source, class) binding. No DDL; no data loss."""

    source_name: str
    class_name: str
    identifier_slot: str


@dataclass
class DropSourceBinding(Change):
    """Bucket A — rows from this source+class combination become orphaned.

    Requires allow_destructive=True at publish.
    """

    source_name: str
    class_name: str


@dataclass
class ChangeSourceBindingIdentifierSlot(Change):
    """Bucket A — the binding's identifier slot changed.  Rows must be rekeyed
    (``_source_row_id`` UPDATE). Destructive.

    ``cls`` is the binding's class on the candidate side; the emitter
    rekeys ``_source_row_id`` against this table."""

    cls: OntologyClass
    source_name: str
    class_name: str
    old_slot: str
    new_slot: str


@dataclass
class ChangeSourceBindingTrust(Change):
    """Bucket C — trust_prior is RUNTIME; no DDL."""

    source_name: str
    class_name: str
    old_prior: tuple[float, float]
    new_prior: tuple[float, float]


@dataclass
class ChangeSourceBindingMapping(Change):
    """Bucket C — mapping shape changed (source_field, default, null_semantics).
    Runtime / spec-only; no DDL."""

    source_name: str
    class_name: str
    slot_name: str


@dataclass
class ChangeSourceBindingRequired(Change):
    """Bucket C — required_slots list changed. No DDL (enforced at ingest time)."""

    source_name: str
    class_name: str


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


def _slot_constraints_pattern(slot: Slot) -> str | None:
    if slot.constraints is not None:
        return slot.constraints.pattern
    return None


def _slot_constraints_min(slot: Slot) -> float | None:
    if slot.constraints is not None:
        return slot.constraints.min_value
    return None


def _slot_constraints_max(slot: Slot) -> float | None:
    if slot.constraints is not None:
        return slot.constraints.max_value
    return None


def _slot_constraints_pv(slot: Slot) -> list[str] | None:
    if slot.constraints is not None and slot.constraints.permissible_values is not None:
        return list(slot.constraints.permissible_values)
    return None


def _diff_slot_fields(cls: OntologyClass, prev_slot: Slot, cand_slot: Slot) -> list[Change]:
    """Compare every CANONICAL field on two same-named slots in the context
    of ``cls``."""
    out: list[Change] = []
    name = cand_slot.name

    prev_pattern = _slot_constraints_pattern(prev_slot)
    cand_pattern = _slot_constraints_pattern(cand_slot)
    if prev_pattern != cand_pattern:
        out.append(
            ChangeSlotPattern(
                slot_name=name,
                old_pattern=prev_pattern,
                new_pattern=cand_pattern,
            )
        )

    prev_pv = _slot_constraints_pv(prev_slot)
    cand_pv = _slot_constraints_pv(cand_slot)
    if prev_pv != cand_pv:
        out.append(
            ChangeSlotPermissibleValues(slot_name=name, old_values=prev_pv, new_values=cand_pv)
        )

    prev_min = _slot_constraints_min(prev_slot)
    cand_min = _slot_constraints_min(cand_slot)
    if prev_min != cand_min:
        out.append(
            ChangeSlotMinimum(
                slot_name=name,
                old_value=prev_min,
                new_value=cand_min,
            )
        )

    prev_max = _slot_constraints_max(prev_slot)
    cand_max = _slot_constraints_max(cand_slot)
    if prev_max != cand_max:
        out.append(
            ChangeSlotMaximum(
                slot_name=name,
                old_value=prev_max,
                new_value=cand_max,
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
    """Diff thin Source entities (name + description only).

    Source is now just an identity node; binding-level changes live in
    ``_diff_source_bindings``.  DropSource is Bucket A (rows become orphaned).
    """
    changes: list[Change] = []
    prev_sources = {s.name: s for s in (prev.sources if prev else [])}
    cand_sources = {s.name: s for s in candidate.sources}

    for name in cand_sources.keys() - prev_sources.keys():
        changes.append(AddSource(source_name=name))
    for name in prev_sources.keys() - cand_sources.keys():
        changes.append(DropSource(source_name=name))
    # description is RUNTIME — not in the canonical hash, no Change needed.
    return changes


def _diff_source_bindings(prev: Spec | None, candidate: Spec) -> list[Change]:
    """Diff SourceBinding entities keyed by (source.name, class_.name).

    Change taxonomy:
      - AddSourceBinding       Bucket C — new pathway, no data loss
      - DropSourceBinding      Bucket A — orphans rows from that (source, class)
      - ChangeSourceBindingIdentifierSlot  Bucket A — row rekey required
      - ChangeSourceBindingTrust           Bucket C — RUNTIME
      - ChangeSourceBindingMapping         Bucket C — RUNTIME
      - ChangeSourceBindingRequired        Bucket C — enforced at ingest time
    """
    changes: list[Change] = []

    def _key(b: SourceBinding) -> tuple[str, str]:
        return (b.source.name, b.class_.name)

    prev_bindings: dict[tuple[str, str], SourceBinding] = {
        _key(b): b for b in (prev.source_bindings if prev else [])
    }
    cand_bindings: dict[tuple[str, str], SourceBinding] = {
        _key(b): b for b in candidate.source_bindings
    }

    for key in cand_bindings.keys() - prev_bindings.keys():
        b = cand_bindings[key]
        changes.append(
            AddSourceBinding(
                source_name=key[0],
                class_name=key[1],
                identifier_slot=b.identifier_slot.name,
            )
        )

    for key in prev_bindings.keys() - cand_bindings.keys():
        changes.append(DropSourceBinding(source_name=key[0], class_name=key[1]))

    for key in cand_bindings.keys() & prev_bindings.keys():
        pb, cb = prev_bindings[key], cand_bindings[key]
        source_name, class_name = key

        if pb.identifier_slot.name != cb.identifier_slot.name:
            changes.append(
                ChangeSourceBindingIdentifierSlot(
                    cls=cb.class_,
                    source_name=source_name,
                    class_name=class_name,
                    old_slot=pb.identifier_slot.name,
                    new_slot=cb.identifier_slot.name,
                )
            )

        # trust_prior is RUNTIME — excluded from canonical hash, but we emit a
        # Change record for auditability. No DDL.
        if pb.trust_prior != cb.trust_prior:
            changes.append(
                ChangeSourceBindingTrust(
                    source_name=source_name,
                    class_name=class_name,
                    old_prior=tuple(pb.trust_prior),  # type: ignore[arg-type]
                    new_prior=tuple(cb.trust_prior),  # type: ignore[arg-type]
                )
            )

        # Mapping diffs — compare by slot name.
        prev_map = {m.slot.name: m for m in pb.mappings}
        cand_map = {m.slot.name: m for m in cb.mappings}
        for slot_name in prev_map.keys() | cand_map.keys():
            pm = prev_map.get(slot_name)
            cm = cand_map.get(slot_name)
            if pm is None or cm is None:
                changed = True
            else:
                changed = (
                    pm.source_field != cm.source_field
                    or pm.default != cm.default
                    or pm.null_semantics != cm.null_semantics
                )
            if changed:
                changes.append(
                    ChangeSourceBindingMapping(
                        source_name=source_name,
                        class_name=class_name,
                        slot_name=slot_name,
                    )
                )

        # required_slots diff
        prev_req = {s.name for s in pb.required_slots}
        cand_req = {s.name for s in cb.required_slots}
        if prev_req != cand_req:
            changes.append(
                ChangeSourceBindingRequired(source_name=source_name, class_name=class_name)
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


def diff_specs(
    prev: Spec | None,
    candidate: Spec,
    *,
    renames: dict[str, dict[str, str]] | None = None,
) -> list[Change]:
    """Diff two specs into a list of typed Change records.

    ``renames`` is an optional hint map: ``{class_name: {old_slot_name: new_slot_name}}``.
    When provided, a slot whose name changed according to the hint emits a
    ``RenameSlot`` instead of the default ``DropSlot + AddSlot`` pair (which
    would require ``allow_destructive``).
    """
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

        # Class-level field changes (abstract / is_a / mixins) — emitted
        # regardless of the abstract / defined status switch. ``abstract``
        # toggling between true/false is itself a category-A change.
        if prev_cls.abstract != cand_cls.abstract:
            changes.append(
                ChangeClassAbstract(
                    cls=cand_cls,
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
                    cls=cand_cls,
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
                        cls=cand_cls,
                        class_name=name,
                        had_definition_before=True,
                        has_definition_now=True,
                        definition_changed=True,
                    )
                )
            continue

        # Both concrete — diff slots.
        # ``stored_slots`` drives AddSlot / DropSlot / ChangeSlotTypeExpression (the
        # DDL-relevant subset). ``all_slots`` (incl. derived) drives the
        # per-field diff so a derivation-body or runtime-policy edit on a
        # derived slot still produces a Change record.
        prev_stored = _stored_slots_by_name(prev_cls)
        cand_stored = _stored_slots_by_name(cand_cls)
        prev_all = {s.name: s for s in _effective_slots(prev_cls)}
        cand_all = {s.name: s for s in _effective_slots(cand_cls)}

        # Build rename-hint lookup for this class: {old_name -> new_name}.
        # Names that appear in the hint but aren't actually missing from prev
        # or absent from cand are ignored (stale hint, defensive).
        class_renames: dict[str, str] = {}  # old_name → new_name
        if renames:
            class_renames = renames.get(name, {})
        # Invert: new_name → old_name (for cand-side lookup).
        new_to_old: dict[str, str] = {v: k for k, v in class_renames.items()}

        # Slots that are truly added (in cand but not in prev, excluding those
        # that are the new name of a rename).
        added_slot_names = cand_stored.keys() - prev_stored.keys()
        # Slots that are truly dropped (in prev but not in cand, excluding
        # those that are the old name of a rename).
        dropped_slot_names = prev_stored.keys() - cand_stored.keys()

        # Emit RenameSlot for confirmed renames, remove them from add/drop sets.
        emitted_renames: set[str] = set()  # old names consumed by a RenameSlot
        for new_name_r, old_name_r in new_to_old.items():
            if old_name_r in dropped_slot_names and new_name_r in added_slot_names:
                cand_slot = cand_stored[new_name_r]
                changes.append(
                    RenameSlot(
                        cls=cand_cls,
                        slot=cand_slot,
                        old_name=old_name_r,
                        new_name=new_name_r,
                    )
                )
                emitted_renames.add(old_name_r)
                added_slot_names = added_slot_names - {new_name_r}
                dropped_slot_names = dropped_slot_names - {old_name_r}

        for s_name in added_slot_names:
            changes.append(AddSlot(cls=cand_cls, slot=cand_stored[s_name]))
        for s_name in dropped_slot_names:
            changes.append(DropSlot(cls=cand_cls, slot_name=s_name))
        for s_name in cand_stored.keys() & prev_stored.keys():
            ps, cs = prev_stored[s_name], cand_stored[s_name]
            prev_t = _slot_pg_type(ps)
            new_t = _slot_pg_type(cs)
            if prev_t != new_t:
                changes.append(
                    ChangeSlotTypeExpression(
                        cls=cand_cls, slot=cs, prev_pg_type=prev_t, new_pg_type=new_t
                    )
                )
            if ps.required != cs.required:
                changes.append(
                    ChangeSlotRequired(
                        cls=cand_cls, slot=cs, slot_name=s_name, new_required=cs.required
                    )
                )

        # Per-field diff over the *full* effective-slot intersection — covers
        # derived slots whose pattern / resolution_policy / derivation body
        # changed without storage shifting.
        for s_name in cand_all.keys() & prev_all.keys():
            ps, cs = prev_all[s_name], cand_all[s_name]
            changes.extend(_diff_slot_fields(cand_cls, ps, cs))

    changes.extend(_diff_sources(prev, candidate))
    changes.extend(_diff_source_bindings(prev, candidate))
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
        # Emit CHECK constraints for every required stored slot.
        for slot in _effective_slots(change.cls):
            if _is_stored(slot) and slot.required:
                await conn.execute(_required_check_sql(change.cls, slot))

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
        """Create (or replace) a VIEW for the defined class."""
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
        if change.slot.required:
            await conn.execute(_required_check_sql(change.cls, change.slot))

    elif isinstance(change, DropSlot):
        stmt = sql.SQL("ALTER TABLE {table} DROP COLUMN {col}").format(
            table=_table_id(change.cls),
            col=sql.Identifier(change.slot_name),
        )
        await conn.execute(stmt)

    elif isinstance(change, ChangeSlotTypeExpression):
        # USING <col>::<newtype> handles cast-compatible base changes
        # (e.g. TEXT→INTEGER for digit-only strings). If the cast fails on
        # a row, postgres raises and the whole migration aborts (atomic).
        # Special-case the scalar↔array transitions:
        #   - scalar→array: wrap each value in a 1-element array
        #     (postgres can't cast 'foo'::TEXT[] — needs ARRAY['foo']).
        #   - array→scalar: refused (multiple values would silently collapse).
        prev_is_array = change.prev_pg_type.endswith("[]")
        new_is_array = change.new_pg_type.endswith("[]")
        if prev_is_array and not new_is_array:
            raise CompilerError(
                f"lossy: cannot demote array column "
                f"{change.cls.name}.{change.slot.name} "
                f"from {change.prev_pg_type} to {change.new_pg_type} — "
                "multiple values would be lost. Drop and re-add the slot, or "
                "introduce a new slot and migrate manually."
            )
        col_ident = sql.Identifier(change.slot.name)
        pgtype_sql = sql.SQL(change.new_pg_type)
        if not prev_is_array and new_is_array:
            using = sql.SQL("ARRAY[{col}]::{pgtype}").format(
                col=col_ident, pgtype=pgtype_sql
            )
        else:
            using = sql.SQL("{col}::{pgtype}").format(
                col=col_ident, pgtype=pgtype_sql
            )
        stmt = sql.SQL(
            "ALTER TABLE {table} ALTER COLUMN {col} TYPE {pgtype} USING {using}"
        ).format(
            table=_table_id(change.cls),
            col=col_ident,
            pgtype=pgtype_sql,
            using=using,
        )
        await conn.execute(stmt)

    elif isinstance(change, ChangeSlotRequired):
        if change.new_required:
            # false → true: add CHECK constraint.
            # The preflight gate already verified no NULLs exist for real
            # sources, so this ALTER is safe to run.
            await conn.execute(_required_check_sql(change.cls, change.slot))
        else:
            # true → false: drop CHECK constraint (no data loss).
            await conn.execute(_required_check_drop_sql(change.cls, change.slot))

    elif isinstance(change, RenameSlot):
        # Step 1: rename the column.
        await conn.execute(
            sql.SQL(
                "ALTER TABLE {table} RENAME COLUMN {old_col} TO {new_col}"
            ).format(
                table=_table_id(change.cls),
                old_col=sql.Identifier(change.old_name),
                new_col=sql.Identifier(change.new_name),
            )
        )
        # Step 2: rename the required-slot CHECK constraint if one exists.
        # Postgres has no RENAME CONSTRAINT DDL. Drop the old name and
        # re-add under the new name via the idempotent DO $$ helper.
        # We create a synthetic "old slot" just to compute the old check name.
        old_slot_for_name = Slot(name=change.old_name, type=change.slot.type)
        old_chk = _required_check_name(change.cls, old_slot_for_name)
        new_chk = _required_check_name(change.cls, change.slot)
        if old_chk != new_chk and change.slot.required:
            # Drop old name (may not exist if required was false before rename).
            await conn.execute(
                sql.SQL("ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {chk}").format(
                    tbl=_table_id(change.cls),
                    chk=sql.Identifier(old_chk),
                )
            )
            # Re-add under new name.
            await conn.execute(_required_check_sql(change.cls, change.slot))

    elif isinstance(change, ChangeClassAbstract):
        # abstract toggles whether the class has a table.
        #   True → False : create the source + bindings tables (mirror AddClass).
        #   False → True : drop the source + bindings tables, IF EMPTY. Refuse
        #                  on rows present.
        cls = change.cls
        if change.old_value is True and change.new_value is False:
            await conn.execute(_create_source_table_sql(cls))
            await conn.execute(_bindings_create_sql(cls))
            await conn.execute(_bindings_index_sql(cls))
            await conn.execute(_bindings_unique_current_sql(cls))
            return
        # concrete → abstract. Refuse if non-empty.
        cls_lower = change.class_name.lower()
        existed = await (
            await conn.execute(
                sql.SQL("SELECT EXISTS(SELECT 1 FROM {t})").format(
                    t=sql.Identifier(schema(), cls_lower),
                )
            )
        ).fetchone()
        if existed and existed[0]:
            raise CompilerError(
                f"ChangeClassAbstract on non-empty class {change.class_name!r}: data "
                "must be removed before flipping abstract=True (use a custom SQL "
                "migration)."
            )
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

    elif isinstance(change, ChangeClassDefinition):
        # Defined-class body change. CREATE OR REPLACE VIEW with the new body
        # (idempotent — no data lost). Delegate to AddDefinedClass.
        await emit_ddl(AddDefinedClass(cls=change.cls), conn)

    elif isinstance(change, ChangeClassIsA):
        # Concrete classes: is_a is structural-only at the DDL layer (own
        # table, own slots; effective_slots doesn't walk is_a). No-op.
        # Defined classes: re-emit the VIEW with the new parent as FROM.
        if _is_defined(change.cls):
            await emit_ddl(AddDefinedClass(cls=change.cls), conn)
        # else: no-op for concrete

    elif isinstance(change, ChangeSourceBindingIdentifierSlot):
        # Rekey existing rows: _source_row_id ← <new_slot>::TEXT. Postgres
        # surfaces NOT-NULL violations and PK collisions naturally if the new
        # slot has NULLs or duplicates per source.
        stmt = sql.SQL("UPDATE {table} SET _source_row_id = {col}::TEXT WHERE _source = %s").format(
            table=_table_id(change.cls),
            col=sql.Identifier(change.new_slot),
        )
        await conn.execute(stmt, (change.source_name,))

    # Spec-only / runtime-behavior changes — no DDL. These records exist for
    # auditability and to drive the destructive gate / constraint revalidation
    # at publish time.
    elif isinstance(
        change,
        (
            ChangeSlotPattern,
            ChangeSlotPermissibleValues,
            ChangeSlotMinimum,
            ChangeSlotMaximum,
            ChangeSlotIdentifier,
            ChangeSlotResolutionPolicy,
            ChangeSlotDerivation,
            AddSource,
            DropSource,
            AddSourceBinding,
            DropSourceBinding,
            ChangeSourceBindingTrust,
            ChangeSourceBindingMapping,
            ChangeSourceBindingRequired,
            AddConstraint,
            DropConstraint,
            ChangeConstraintPrimary,
            ChangeConstraintBody,
            ChangeConstraintSeverity,
            ChangeClassMixins,
        ),
    ):
        return

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
#   - **Storage-shape rewrites**: ChangeSlotTypeExpression (column type),
#     ChangeClassAbstract (table appears/disappears).
#   - **Source rekey**: ChangeSourceEntityClass (rows now belong to a
#     different class — refused, manual migration required) and
#     ChangeSourceIdentifierSlot (rows are now keyed by a different slot
#     — UPDATE rekeys ``_source_row_id``).
#
# NOT enumerated here:
#
#   - Bucket B (data-revalidation: ChangeSlotPattern, ChangeSlotPermissible-
#     Values, min/max, ChangeConstraintBody). The publish gate already runs
#     every NEW or CHANGED constraint over current data; tightenings on those
#     fields surface as violations through that pass.
#   - ChangeClassMixins — slot-level ``DropSlot`` / ``AddSlot`` records do
#     the destructive gating; this record is audit-only.
#   - ChangeSlotIdentifier — the storage PK is ``(_source, _source_row_id)``;
#     the ``identifier`` flag is ER/SCD2 advisory and emits no DDL.
#   - ChangeClassIsA — concrete-class is_a doesn't drive DDL (own table,
#     own slots); defined-class is_a body changes are CREATE OR REPLACE
#     VIEW; concrete↔defined transitions surface as ``Drop*`` / ``Add*``.
#   - ChangeClassDefinition — defined-class body change is CREATE OR
#     REPLACE VIEW (idempotent, no data loss).
#   - ChangeSourceTrustScore / ChangeSourceSlotPrior — runtime config, no DDL.
_DESTRUCTIVE_CHANGE_TYPES: tuple[type[Change], ...] = (
    DropClass,
    DropSlot,
    DropDefinedClass,
    ChangeSlotTypeExpression,
    ChangeClassAbstract,
    ChangeClassIsA,
    DropSource,
    DropSourceBinding,
    ChangeSourceBindingIdentifierSlot,
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
