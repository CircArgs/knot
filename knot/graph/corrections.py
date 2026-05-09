"""User corrections — orchestration layer.

Composes the persistence primitives in ``knot.db`` to apply a typed
correction in one transaction:

  1. Audit log row in ``_user_corrections`` (db.corrections).
  2. Data-plane mutation in ``knot_data.<class>`` (db.graph_store).
  3. Bandit feedback per disagreeing source where applicable
     (db.trust_posteriors).

Each ``apply_*`` is self-validating: it checks slot membership,
canonical_id existence, and partition shape before touching the data
plane. The route layer dispatches on body type and maps the typed
exceptions raised here to HTTP status codes.

Implemented: PropertyCorrection, Merge, Split, Add, Tombstone,
RejectContribution.
"""

from __future__ import annotations

from typing import Any

import psycopg
from pydantic import ValidationError

from knot.api.row_models import build_row_model_for_class, build_value_model_for_slot
from knot.db import corrections as db_corrections
from knot.db import dq, graph_store, trust_posteriors
from knot.spec.compile.postgres._naming import user_corrections_source
from knot.spec import OntologyClass, Slot

# ---------------------------------------------------------------------------
# Typed errors — every apply_* validates and raises one of these.
# ---------------------------------------------------------------------------


class CanonicalNotFoundError(Exception):
    """A canonical_id has no current contributions for the given class."""

    def __init__(self, class_name: str, canonical_id: str, *, role: str = "canonical_id") -> None:
        self.class_name = class_name
        self.canonical_id = canonical_id
        self.role = role
        super().__init__(
            f"{role} {canonical_id!r} has no current contributions for class {class_name!r}"
        )


class CanonicalAlreadyExistsError(Exception):
    """A canonical_id already has current contributions; can't be reused."""

    def __init__(self, class_name: str, canonical_id: str) -> None:
        self.class_name = class_name
        self.canonical_id = canonical_id
        super().__init__(
            f"new_canonical_id {canonical_id!r} already exists for class {class_name!r}"
        )


class SlotNotOnClassError(Exception):
    """A slot name isn't on the target class."""

    def __init__(self, class_name: str, slot_name: str) -> None:
        self.class_name = class_name
        self.slot_name = slot_name
        super().__init__(f"Slot {slot_name!r} not on class {class_name!r}")


class InvalidPartitionsError(Exception):
    """Partition shape is invalid (too few, overlapping rows, etc.)."""


class CorrectionValueError(Exception):
    """The corrected value failed validation against the slot/class shape.

    ``errors`` carries the FastAPI-shape error list that the route returns.
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} value validation error(s)")


# ---------------------------------------------------------------------------
# Helpers — slot lookup, value validation
# ---------------------------------------------------------------------------


def _resolve_slot(cls: OntologyClass, slot_name: str) -> Slot:
    slot = next((s for s in cls.slots if s.name == slot_name), None)
    if slot is None:
        raise SlotNotOnClassError(cls.name, slot_name)
    return slot


def _validate_property_value(slot: Slot, value: Any) -> Any:
    """Validate the corrected value against the slot's full constraint set
    (type, pattern, min/max, Literal-from-permissible-values, multivalued
    list-shape). Raises ``CorrectionValueError`` on mismatch.

    Same constraint-application logic as ingest's per-source row model.
    """
    one_field = build_value_model_for_slot(slot)
    try:
        return one_field.model_validate({slot.name: value}).model_dump()[slot.name]
    except ValidationError as exc:
        errors = [{**e, "loc": ("body", "value", *e["loc"])} for e in exc.errors()]
        raise CorrectionValueError(errors) from exc


def _validate_synthetic_values(cls: OntologyClass, values: dict[str, Any]) -> dict[str, Any]:
    SyntheticRowModel = build_row_model_for_class(cls)
    try:
        return SyntheticRowModel.model_validate(values).model_dump(exclude_none=True)
    except Exception as exc:
        raise CorrectionValueError([{"msg": str(exc), "loc": ("body", "values")}]) from exc


def _values_match(contributed: Any, corrected: Any) -> bool:
    """Equality used for bandit-feedback signal extraction.

    Multivalued slots come back as Python lists; equality is order-
    insensitive (sources providing the same set in different order
    shouldn't be punished). Scalars use plain equality.
    """
    if isinstance(contributed, list) and isinstance(corrected, list):
        try:
            return frozenset(contributed) == frozenset(corrected)
        except TypeError:
            return sorted(map(repr, contributed)) == sorted(map(repr, corrected))
    return contributed == corrected


# ---------------------------------------------------------------------------
# apply_* — each does validation + audit + data mutation in one transaction.
# ---------------------------------------------------------------------------


async def apply_property_correction(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    slot_name: str,
    value: Any,
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Apply a PropertyCorrection in one transaction. Returns the audit-log id.

    Validates: slot is on class, value matches slot's constraint set.
    Does NOT pre-check canonical_id existence — corrections may be the first
    write under an Add-style flow (the upsert path handles it).
    """
    slot = _resolve_slot(cls, slot_name)
    validated_value = _validate_property_value(slot, value)

    log_payload = payload_for_log or {
        "type": "property",
        "class_name": cls.name,
        "canonical_id": canonical_id,
        "slot": slot_name,
        "value": value,
    }
    async with conn.transaction():
        correction_id = await db_corrections.record_audit_entry(
            conn,
            correction_type="property",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        await graph_store.upsert_user_correction_row(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            slot_name=slot_name,
            value=validated_value,
            spec_revision=spec_revision,
        )
        await dq.record_incremental(
            conn,
            source_name=user_corrections_source(),
            cls=cls,
            batch_id=str(correction_id),
            rows=[{slot_name: validated_value}],
            only_slots=[slot_name],
        )
        for source, contributed in await graph_store.get_disagreeing_contributions(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            slot_name=slot_name,
        ):
            success = _values_match(contributed, validated_value)
            await trust_posteriors.record_feedback(conn, source, slot_name, success)
        return correction_id


async def apply_merge(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    keep_canonical_id: str,
    merge_canonical_ids: list[str],
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Collapse ``merge_canonical_ids`` into ``keep_canonical_id`` via SCD2.

    Validates: at least one merge source, no self-merge, all canonical_ids
    have current contributions (deduplicated). No bandit feedback on merges
    in this slice — slot-level Beta posteriors don't have a natural
    Bernoulli signal here.

    Returns the audit-log id.
    """
    if not merge_canonical_ids:
        raise InvalidPartitionsError("merge_canonical_ids must be non-empty")
    if keep_canonical_id in merge_canonical_ids:
        raise InvalidPartitionsError("keep_canonical_id must not appear in merge_canonical_ids")

    seen: set[str] = set()
    deduped: list[str] = []
    for cid in merge_canonical_ids:
        if cid not in seen:
            seen.add(cid)
            deduped.append(cid)

    if not await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=keep_canonical_id):
        raise CanonicalNotFoundError(cls.name, keep_canonical_id, role="keep_canonical_id")
    for cid in deduped:
        if not await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=cid):
            raise CanonicalNotFoundError(cls.name, cid, role="merge_canonical_id")

    log_payload = payload_for_log or {
        "type": "merge",
        "class_name": cls.name,
        "keep_canonical_id": keep_canonical_id,
        "merge_canonical_ids": list(deduped),
    }
    async with conn.transaction():
        correction_id = await db_corrections.record_audit_entry(
            conn,
            correction_type="merge",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        await graph_store.merge_canonical_ids(
            conn,
            cls=cls,
            keep_canonical_id=keep_canonical_id,
            from_canonical_ids=deduped,
            spec_revision=spec_revision,
            correction_id=correction_id,
            change_type="merge",
        )
        await graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="merge",
            from_canonical_ids=list(deduped),
            to_canonical_ids=[keep_canonical_id],
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


async def apply_split(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    source_canonical_id: str,
    partitions: dict[str, list[tuple[str, str]]],
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Split ``source_canonical_id`` into two or more new canonical IDs.

    Validates: at least 2 partitions, source canonical_id has current
    contributions, no new canonical_id collides with an existing one,
    each (source, source_row_id) appears in exactly one partition.

    ``partitions`` maps new_canonical_id -> [(source, source_row_id), ...].
    Returns the audit-log id.
    """
    if len(partitions) < 2:
        raise InvalidPartitionsError("split requires at least 2 partitions")
    if not await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=source_canonical_id):
        raise CanonicalNotFoundError(cls.name, source_canonical_id, role="source_canonical_id")
    for new_cid in partitions:
        if await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=new_cid):
            raise CanonicalAlreadyExistsError(cls.name, new_cid)
    all_members: list[tuple[str, str]] = []
    for members in partitions.values():
        all_members.extend(members)
    if len(all_members) != len(set(all_members)):
        raise InvalidPartitionsError(
            "each (source, source_row_id) must appear in exactly one partition"
        )

    log_payload = payload_for_log or {
        "type": "split",
        "class_name": cls.name,
        "source_canonical_id": source_canonical_id,
        "partitions": dict(partitions),
    }
    async with conn.transaction():
        correction_id = await db_corrections.record_audit_entry(
            conn,
            correction_type="split",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        await graph_store.split_canonical_id(
            conn,
            cls=cls,
            source_canonical_id=source_canonical_id,
            partitions=partitions,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        await graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="split",
            from_canonical_ids=[source_canonical_id],
            to_canonical_ids=list(partitions.keys()),
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


async def apply_add(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    new_canonical_id: str,
    values: dict[str, Any],
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Create a synthetic entity not present in any source.

    Validates: new_canonical_id doesn't collide with an existing one;
    values match the class's row shape.

    Inserts a source row attributed to ``_user_corrections`` and opens an
    initial binding. Returns the audit-log id.
    """
    if await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=new_canonical_id):
        raise CanonicalAlreadyExistsError(cls.name, new_canonical_id)
    validated_values = _validate_synthetic_values(cls, values)

    # Audit log keeps the human-submitted payload (raw strings/scalars). The
    # validated dict may contain parsed datetimes etc. that aren't JSON-safe
    # for the audit row.
    log_payload = payload_for_log or {
        "type": "add",
        "class_name": cls.name,
        "new_canonical_id": new_canonical_id,
        "values": values,
    }
    async with conn.transaction():
        correction_id = await db_corrections.record_audit_entry(
            conn,
            correction_type="add",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        await graph_store.insert_synthetic_row(
            conn,
            cls=cls,
            new_canonical_id=new_canonical_id,
            values=validated_values,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        await graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="add",
            from_canonical_ids=[],
            to_canonical_ids=[new_canonical_id],
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        await dq.record_incremental(
            conn,
            source_name=user_corrections_source(),
            cls=cls,
            batch_id=str(correction_id),
            rows=[validated_values],
        )
        return correction_id


async def apply_tombstone(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    reason: str | None = None,
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Mark a canonical entity as deleted by closing all current bindings.

    Validates: canonical_id has current contributions.

    Tombstoned entities disappear from reads (``valid_to IS NULL`` joins)
    unless ``include_tombstoned=True`` is passed. Returns the audit-log id.
    """
    if not await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=canonical_id):
        raise CanonicalNotFoundError(cls.name, canonical_id)

    log_payload = payload_for_log or {
        "type": "tombstone",
        "class_name": cls.name,
        "canonical_id": canonical_id,
        "reason": reason,
    }
    async with conn.transaction():
        correction_id = await db_corrections.record_audit_entry(
            conn,
            correction_type="tombstone",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        await graph_store.tombstone_canonical_id(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        await graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="tombstone",
            from_canonical_ids=[canonical_id],
            to_canonical_ids=[],
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


async def apply_reject_contribution(
    conn: psycopg.AsyncConnection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    source: str,
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Drop one source's view of an entity by closing just that binding.

    Validates: canonical_id has current contributions.

    The source row is preserved for audit. No lineage event is emitted
    (this is a single-source change, not an identity event). Returns the
    audit-log id.
    """
    if not await graph_store.canonical_id_exists(conn, cls=cls, canonical_id=canonical_id):
        raise CanonicalNotFoundError(cls.name, canonical_id)

    log_payload = payload_for_log or {
        "type": "reject_contribution",
        "class_name": cls.name,
        "canonical_id": canonical_id,
        "source": source,
    }
    async with conn.transaction():
        correction_id = await db_corrections.record_audit_entry(
            conn,
            correction_type="reject_contribution",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        await graph_store.reject_contribution(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            source=source,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id
