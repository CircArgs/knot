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
    SourceBinding,
    Spec,
)
from knot.spec.errors import PublishGateError
from knot.spec.metaschema import Primitive, SlotConstraints


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


def _build_spec() -> Spec:
    id_slot = Slot(name="id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    return Spec(
        id="t",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )


async def test_publish_allows_slot_pattern_change_without_destructive_flag(clean_db):
    """Bucket B — Slot.pattern tightening is NOT in the destructive set; the
    publish should succeed without ``allow_destructive=true``."""
    v1 = _build_spec()
    v2 = _build_spec()
    v2.classes[0].slots[0].constraints = SlotConstraints(pattern=r"^tt[0-9]+$")

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, v2)
    await publish_draft(clean_db, rev2)  # must not raise


async def test_publish_blocks_source_identifier_slot_change_without_flag(clean_db):
    """Bucket A — Source.identifier_slot changes how rows are keyed. Publish
    must reject without ``allow_destructive=true``."""

    def _build_with_identifier(identifier_name: str) -> Spec:
        id_a = Slot(name="id_a", type=Primitive(name="string"), identifier=True, required=True)
        id_b = Slot(name="id_b", type=Primitive(name="string"), identifier=True, required=True)
        movie = OntologyClass(name="Movie", slots=[id_a, id_b])
        identifier = {"id_a": id_a, "id_b": id_b}[identifier_name]
        src = Source(name="imdb")
        binding = SourceBinding(source=src, class_=movie, identifier_slot=identifier)  # type: ignore[call-arg]
        return Spec(
            id="t",
            version="1.0.0",
            classes=[movie],
            sources=[src],
            source_bindings=[binding],
        )

    v1 = _build_with_identifier("id_a")
    v2 = _build_with_identifier("id_b")

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, v2)
    with pytest.raises(PublishGateError, match="destructive"):
        await publish_draft(clean_db, rev2)
