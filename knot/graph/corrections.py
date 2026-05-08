"""User corrections — orchestration layer.

Composes the persistence primitives in ``knot.db`` to apply a typed
correction in one transaction:

  1. Audit log row in ``_user_corrections`` (db.corrections).
  2. Data-plane mutation in ``knot_data.<class>`` (db.graph_store).
  3. Bandit feedback per disagreeing source where applicable
     (db.trust_posteriors).

This module is pure composition: no SQL strings, no psycopg imports —
everything routes through ``knot.db``.

Implemented: PropertyCorrection, Merge, Split, Add, Tombstone,
RejectContribution.
"""

from __future__ import annotations

from typing import Any

import psycopg

from knot.db import corrections as db_corrections
from knot.db import dq, graph_store, trust_posteriors
from knot.db._naming import user_corrections_source
from knot.spec import OntologyClass


def apply_merge(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    keep_canonical_id: str,
    merge_canonical_ids: list[str],
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Collapse ``merge_canonical_ids`` into ``keep_canonical_id`` via SCD2:
      1. Audit row in _user_corrections.
      2. For each merged canonical_id: close current bindings (set
         valid_to=now()) and open new bindings on the same knot_row_ids
         with the kept canonical_id.
      3. Append a canonical_id_lineage event for human-readable audit.

    No bandit feedback on merges in this slice — slot-level Beta
    posteriors don't have a natural Bernoulli signal here. ER-level
    feedback is its own design.

    Returns the audit-log id.
    """
    log_payload = payload_for_log or {
        "type": "merge",
        "class_name": cls.name,
        "keep_canonical_id": keep_canonical_id,
        "merge_canonical_ids": list(merge_canonical_ids),
    }
    with conn.transaction():
        correction_id = db_corrections.record_audit_entry(
            conn,
            correction_type="merge",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        graph_store.merge_canonical_ids(
            conn,
            cls=cls,
            keep_canonical_id=keep_canonical_id,
            from_canonical_ids=merge_canonical_ids,
            spec_revision=spec_revision,
            correction_id=correction_id,
            change_type="merge",
        )
        graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="merge",
            from_canonical_ids=list(merge_canonical_ids),
            to_canonical_ids=[keep_canonical_id],
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


def apply_property_correction(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    slot_name: str,
    value: Any,
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Apply a PropertyCorrection in one transaction. Returns the audit-log id."""
    log_payload = payload_for_log or {
        "type": "property",
        "class_name": cls.name,
        "canonical_id": canonical_id,
        "slot": slot_name,
        "value": value,
    }
    with conn.transaction():
        correction_id = db_corrections.record_audit_entry(
            conn,
            correction_type="property",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        graph_store.upsert_user_correction_row(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            slot_name=slot_name,
            value=value,
            spec_revision=spec_revision,
        )
        dq.record_incremental(
            conn,
            source_name=user_corrections_source(),
            cls=cls,
            batch_id=str(correction_id),
            rows=[{slot_name: value}],
            only_slots=[slot_name],
        )
        for source, contributed in graph_store.get_disagreeing_contributions(
            conn, cls=cls, canonical_id=canonical_id, slot_name=slot_name,
        ):
            success = _values_match(contributed, value)
            trust_posteriors.record_feedback(conn, source, slot_name, success)
        return correction_id


def apply_split(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    source_canonical_id: str,
    partitions: dict[str, list[tuple[str, str]]],
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Split ``source_canonical_id`` into two or more new canonical IDs.

    ``partitions`` maps new_canonical_id -> [(source, source_row_id), ...].
    Each (source, source_row_id) currently bound to source_canonical_id is
    moved to exactly one new canonical_id.  Returns the audit-log id.
    """
    log_payload = payload_for_log or {
        "type": "split",
        "class_name": cls.name,
        "source_canonical_id": source_canonical_id,
        "partitions": {k: v for k, v in partitions.items()},
    }
    with conn.transaction():
        correction_id = db_corrections.record_audit_entry(
            conn,
            correction_type="split",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        graph_store.split_canonical_id(
            conn,
            cls=cls,
            source_canonical_id=source_canonical_id,
            partitions=partitions,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="split",
            from_canonical_ids=[source_canonical_id],
            to_canonical_ids=list(partitions.keys()),
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


def apply_add(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    new_canonical_id: str,
    values: dict[str, Any],
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Create a synthetic entity not present in any source.

    Inserts a source row attributed to ``_user_corrections`` and opens an
    initial binding.  Returns the audit-log id.
    """
    log_payload = payload_for_log or {
        "type": "add",
        "class_name": cls.name,
        "new_canonical_id": new_canonical_id,
        "values": values,
    }
    with conn.transaction():
        correction_id = db_corrections.record_audit_entry(
            conn,
            correction_type="add",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        graph_store.insert_synthetic_row(
            conn,
            cls=cls,
            new_canonical_id=new_canonical_id,
            values=values,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="add",
            from_canonical_ids=[],
            to_canonical_ids=[new_canonical_id],
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        dq.record_incremental(
            conn,
            source_name=user_corrections_source(),
            cls=cls,
            batch_id=str(correction_id),
            rows=[values],
        )
        return correction_id


def apply_tombstone(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    reason: str | None = None,
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Mark a canonical entity as deleted by closing all current bindings.

    Tombstoned entities disappear from reads (``valid_to IS NULL`` joins)
    unless ``include_tombstoned=True`` is passed.  Returns the audit-log id.
    """
    log_payload = payload_for_log or {
        "type": "tombstone",
        "class_name": cls.name,
        "canonical_id": canonical_id,
        "reason": reason,
    }
    with conn.transaction():
        correction_id = db_corrections.record_audit_entry(
            conn,
            correction_type="tombstone",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        graph_store.tombstone_canonical_id(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        graph_store.append_lineage_event(
            conn,
            class_name=cls.name,
            change_type="tombstone",
            from_canonical_ids=[canonical_id],
            to_canonical_ids=[],
            applied_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


def apply_reject_contribution(
    conn: psycopg.Connection,
    *,
    cls: OntologyClass,
    canonical_id: str,
    source: str,
    spec_revision: int,
    applied_by: str | None = None,
    payload_for_log: dict[str, Any] | None = None,
) -> int:
    """Drop one source's view of an entity by closing just that binding.

    The source row is preserved for audit. No lineage event is emitted
    (this is a single-source change, not an identity event). Returns the
    audit-log id.
    """
    log_payload = payload_for_log or {
        "type": "reject_contribution",
        "class_name": cls.name,
        "canonical_id": canonical_id,
        "source": source,
    }
    with conn.transaction():
        correction_id = db_corrections.record_audit_entry(
            conn,
            correction_type="reject_contribution",
            payload=log_payload,
            applied_by=applied_by,
            applied_revision=spec_revision,
        )
        graph_store.reject_contribution(
            conn,
            cls=cls,
            canonical_id=canonical_id,
            source=source,
            spec_revision=spec_revision,
            correction_id=correction_id,
        )
        return correction_id


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
