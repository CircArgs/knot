"""User corrections — orchestration layer.

Composes the persistence primitives in ``knot.db`` to apply a typed
correction in one transaction:

  1. Audit log row in ``_user_corrections`` (db.corrections).
  2. Data-plane upsert in ``knot_data.<class>`` attributed to the
     reserved ``_user_corrections`` source (db.graph_store).
  3. Bandit feedback per disagreeing source — sources whose contribution
     matched the corrected value get α += 1; mismatches get β += 1
     (db.trust_posteriors). Closes the loop without manual
     ``/trust/feedback`` calls.

This module is pure composition: no SQL strings, no psycopg imports —
everything routes through ``knot.db``.

Today only ``PropertyCorrection`` is implemented; Merge / Split / Add /
Tombstone / RejectContribution land as their own slices.
"""

from __future__ import annotations

from typing import Any

import psycopg

from knot.db import corrections as db_corrections
from knot.db import graph_store, trust_posteriors
from knot.ontology import OntologyClass


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
            merge_canonical_ids=merge_canonical_ids,
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
        for source, contributed in graph_store.get_disagreeing_contributions(
            conn, cls=cls, canonical_id=canonical_id, slot_name=slot_name,
        ):
            success = contributed == value
            trust_posteriors.record_feedback(conn, source, slot_name, success)
        return correction_id
