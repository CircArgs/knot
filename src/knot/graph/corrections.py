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
