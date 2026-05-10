"""Publish-gate integration tests for per-canonical-field diffs.

The pure ``diff_specs`` tests for each Change record live in
``tests/unit/spec/compile/test_migration_field_diffs.py``. This file
exercises the full draft → publish flow against postgres for the two
representative cases that are load-bearing at the gate:

  - bucket B (slot pattern) must publish without ``allow_destructive``
  - bucket A (source identifier_slot) must be rejected without the flag
"""

from __future__ import annotations

import pytest

from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import (
    OntologyClass,
    Slot,
    Source,
    Spec,
    TypeDefinition,
)
from knot.spec.errors import PublishGateError


@pytest.fixture
async def clean_db(pg_conn):
    """Same setup as test_migration.py — drop knot_data + truncate control tables."""
    from knot import db

    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()
    yield pg_conn


def _publishable_spec_pair(*, mutator):
    """Build (v1, v2) where v2 has the field mutation applied."""

    def _build():
        st = TypeDefinition(name="string", base="str")
        id_slot = Slot(name="id", range=st, identifier=True, required=True)
        movie = OntologyClass(name="Movie", slots=[id_slot])
        src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_slot],
            classes=[movie],
            sources=[src],
        )

    v1 = _build()
    v2 = _build()
    mutator(v2)
    return v1, v2


async def test_publish_allows_slot_pattern_change_without_destructive_flag(clean_db):
    """Bucket B — Slot.pattern tightening is NOT in the destructive set; the
    publish should succeed without ``allow_destructive=true``."""

    def mutate(spec):
        spec.slots[0].pattern = r"^tt[0-9]+$"

    v1, v2 = _publishable_spec_pair(mutator=mutate)

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, v2)
    await publish_draft(clean_db, rev2)  # must not raise


async def test_publish_blocks_source_identifier_slot_change_without_flag(clean_db):
    """Bucket A — Source.identifier_slot changes how rows are keyed. Publish
    must reject without ``allow_destructive=true``."""

    def _build(identifier_name):
        st = TypeDefinition(name="string", base="str")
        id_a = Slot(name="id_a", range=st, identifier=True, required=True)
        id_b = Slot(name="id_b", range=st, identifier=True, required=True)
        movie = OntologyClass(name="Movie", slots=[id_a, id_b])
        identifier = {"id_a": id_a, "id_b": id_b}[identifier_name]
        src = Source(name="imdb", entity_class=movie, identifier_slot=identifier)
        return Spec(
            id="t",
            version="1.0.0",
            types=[st],
            slots=[id_a, id_b],
            classes=[movie],
            sources=[src],
        )

    v1 = _build("id_a")
    v2 = _build("id_b")

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, v2)
    with pytest.raises(PublishGateError, match="destructive"):
        await publish_draft(clean_db, rev2)
