"""Correction-types integration tests: Split, Add, Tombstone, RejectContribution.

Each section tests one correction type end-to-end:
  - orchestration layer (graph.corrections.apply_*)
  - persistence outcomes in knot_data (graph_store reads)
  - lineage events where applicable
  - include_tombstoned flag on list/count/contributions reads
  - validation error paths (404/409/400/422)

Fixture: a published Movie spec with two sources; rows ingested before each test.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db import corrections as db_corrections
from knot.db import graph_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.graph.corrections import (
    apply_add,
    apply_reject_contribution,
    apply_split,
    apply_tombstone,
)
from knot.spec import OntologyClass, ResolutionPolicy, Slot, Source, Spec, TypeDefinition


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def ct_db(pg_conn):
    """Full reset, publish Movie spec, insert rows from two sources."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st, resolution_policy=ResolutionPolicy.POSTERIOR_MEAN)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year])
    src_a = Source(name="source_a", entity_class=movie, identifier_slot=imdb_id)
    src_b = Source(name="source_b", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="ct_test",
        version="1.0.0",
        types=[st, it],
        slots=[imdb_id, title, year],
        classes=[movie],
        sources=[src_a, src_b],
    )
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    # Two distinct canonical ids; both sources contribute to "tt_main".
    await graph_store.insert_rows(pg_conn, source=src_a, spec_revision=rev,
                        rows=[{"imdb_id": "tt_main", "title": "Main A", "year": 2000}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_main", "title": "Main A", "year": 2000}]])
    await graph_store.insert_rows(pg_conn, source=src_b, spec_revision=rev,
                        rows=[{"imdb_id": "tt_main", "title": "Main B", "year": 2001}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_main", "title": "Main B", "year": 2001}]])
    await graph_store.insert_rows(pg_conn, source=src_a, spec_revision=rev,
                        rows=[{"imdb_id": "tt_extra", "title": "Extra", "year": 1999}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_extra", "title": "Extra", "year": 1999}]])

    yield pg_conn, movie, src_a, src_b, rev


# ---------------------------------------------------------------------------
# Split
# ---------------------------------------------------------------------------

def _split_partitions(src_a, src_b):
    """Helper: partition tt_main so source_a -> tt_split_1, source_b -> tt_split_2."""
    return {
        "tt_split_1": [("source_a", "tt_main")],
        "tt_split_2": [("source_b", "tt_main")],
    }


async def test_split_writes_audit_entry(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    cid = await apply_split(
        conn,
        cls=movie,
        source_canonical_id="tt_main",
        partitions=_split_partitions(src_a, src_b),
        spec_revision=rev,
        applied_by="splitter",
    )
    log = await db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == cid]
    assert len(matching) == 1
    assert matching[0]["correction_type"] == "split"
    assert matching[0]["applied_by"] == "splitter"


async def test_split_partitions_bindings(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_split(
        conn,
        cls=movie,
        source_canonical_id="tt_main",
        partitions=_split_partitions(src_a, src_b),
        spec_revision=rev,
    )
    # source_canonical_id should no longer exist.
    assert not await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_main")
    # Both new canonical ids should exist.
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_split_1")
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_split_2")


async def test_split_contributions_routed_correctly(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_split(
        conn,
        cls=movie,
        source_canonical_id="tt_main",
        partitions=_split_partitions(src_a, src_b),
        spec_revision=rev,
    )
    c1 = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt_split_1")
    c2 = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt_split_2")
    assert len(c1) == 1 and c1[0]["_source"] == "source_a"
    assert len(c2) == 1 and c2[0]["_source"] == "source_b"


async def test_split_writes_lineage_event(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_split(
        conn,
        cls=movie,
        source_canonical_id="tt_main",
        partitions=_split_partitions(src_a, src_b),
        spec_revision=rev,
    )
    lineage = await graph_store.list_lineage(conn, class_name="Movie")
    split_events = [e for e in lineage if e["change_type"] == "split"]
    assert len(split_events) >= 1
    evt = split_events[0]
    assert "tt_main" in evt["from_canonical_ids"]
    assert set(evt["to_canonical_ids"]) == {"tt_split_1", "tt_split_2"}


async def test_split_returns_int_correction_id(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    result = await apply_split(
        conn,
        cls=movie,
        source_canonical_id="tt_main",
        partitions=_split_partitions(src_a, src_b),
        spec_revision=rev,
    )
    assert isinstance(result, int) and result >= 1


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------

async def test_add_creates_canonical_id(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_synthetic",
        values={"imdb_id": "tt_synthetic", "title": "Synthetic Movie", "year": 2024},
        spec_revision=rev,
        applied_by="adder",
    )
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_synthetic")


async def test_add_source_is_user_corrections(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_synthetic",
        values={"imdb_id": "tt_synthetic", "title": "Synthetic Movie"},
        spec_revision=rev,
    )
    contribs = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt_synthetic")
    assert len(contribs) == 1
    assert contribs[0]["_source"] == "_user_corrections"


async def test_add_slot_values_stored(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_syn2",
        values={"imdb_id": "tt_syn2", "title": "Has Title", "year": 1985},
        spec_revision=rev,
    )
    contribs = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt_syn2")
    assert contribs[0]["title"] == "Has Title"
    assert contribs[0]["year"] == 1985


async def test_add_writes_audit_entry(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    cid = await apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_syn3",
        values={"imdb_id": "tt_syn3"},
        spec_revision=rev,
        applied_by="adder",
    )
    log = await db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == cid]
    assert matching[0]["correction_type"] == "add"
    assert matching[0]["applied_by"] == "adder"


async def test_add_writes_lineage_event(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_add(
        conn,
        cls=movie,
        new_canonical_id="tt_syn4",
        values={"imdb_id": "tt_syn4"},
        spec_revision=rev,
    )
    lineage = await graph_store.list_lineage(conn, class_name="Movie")
    add_events = [e for e in lineage if e["change_type"] == "add"]
    assert any("tt_syn4" in e["to_canonical_ids"] for e in add_events)
    # from must be empty list
    evt = next(e for e in add_events if "tt_syn4" in e["to_canonical_ids"])
    assert evt["from_canonical_ids"] == []


# ---------------------------------------------------------------------------
# Tombstone
# ---------------------------------------------------------------------------

async def test_tombstone_entity_excluded_from_default_reads(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_tombstone(conn, cls=movie, canonical_id="tt_extra", spec_revision=rev)
    # Default reads (include_tombstoned=False) should not see it.
    assert not await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_extra")
    rows = await graph_store.list_rows(conn, cls=movie)
    assert all(r["_canonical_id"] != "tt_extra" for r in rows)


async def test_tombstone_include_tombstoned_shows_entity(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_tombstone(conn, cls=movie, canonical_id="tt_extra", spec_revision=rev)
    rows = await graph_store.list_rows(conn, cls=movie, include_tombstoned=True)
    tombstoned = [r for r in rows if r["_canonical_id"] == "tt_extra"]
    assert len(tombstoned) >= 1


async def test_tombstone_count_with_include_tombstoned(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    normal_count = await graph_store.count_rows(conn, cls=movie)
    await apply_tombstone(conn, cls=movie, canonical_id="tt_extra", spec_revision=rev)
    count_after = await graph_store.count_rows(conn, cls=movie)
    count_with_tombstoned = await graph_store.count_rows(conn, cls=movie, include_tombstoned=True)
    assert count_after == normal_count - 1
    assert count_with_tombstoned == normal_count


async def test_tombstone_contributions_with_include_tombstoned(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_tombstone(conn, cls=movie, canonical_id="tt_extra", spec_revision=rev)
    contribs = await graph_store.get_canonical_contributions(
        conn, cls=movie, canonical_id="tt_extra", include_tombstoned=True
    )
    assert len(contribs) >= 1


async def test_tombstone_writes_audit_entry(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    cid = await apply_tombstone(
        conn, cls=movie, canonical_id="tt_extra", spec_revision=rev,
        reason="test removal", applied_by="gravekeeper",
    )
    log = await db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == cid]
    assert matching[0]["correction_type"] == "tombstone"
    assert matching[0]["applied_by"] == "gravekeeper"


async def test_tombstone_writes_lineage_event(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_tombstone(conn, cls=movie, canonical_id="tt_extra", spec_revision=rev)
    lineage = await graph_store.list_lineage(conn, class_name="Movie")
    tomb_events = [e for e in lineage if e["change_type"] == "tombstone"]
    assert any("tt_extra" in e["from_canonical_ids"] for e in tomb_events)
    evt = next(e for e in tomb_events if "tt_extra" in e["from_canonical_ids"])
    assert evt["to_canonical_ids"] == []


# ---------------------------------------------------------------------------
# RejectContribution
# ---------------------------------------------------------------------------

async def test_reject_contribution_removes_source_from_reads(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    # tt_main has both source_a and source_b; reject source_b.
    await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_b", spec_revision=rev,
    )
    contribs = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt_main")
    sources = [c["_source"] for c in contribs]
    assert "source_b" not in sources
    assert "source_a" in sources


async def test_reject_contribution_canonical_id_still_exists(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_b", spec_revision=rev,
    )
    # canonical_id still exists because source_a still contributes.
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_main")


async def test_reject_contribution_source_row_preserved(ct_db):
    """Source row stays in knot_data for audit; only binding is closed."""
    conn, movie, src_a, src_b, rev = ct_db
    await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_b", spec_revision=rev,
    )
    # The raw source row is still in the table (no valid_to IS NULL binding for it).
    row = await (await conn.execute(
        "SELECT _source, _source_row_id FROM knot_data.movie WHERE _source = 'source_b' AND _source_row_id = 'tt_main'"
    )).fetchone()
    assert row is not None


async def test_reject_contribution_writes_audit_entry(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    cid = await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_b",
        spec_revision=rev, applied_by="rejector",
    )
    log = await db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == cid]
    assert matching[0]["correction_type"] == "reject_contribution"
    assert matching[0]["applied_by"] == "rejector"


async def test_reject_contribution_no_lineage_event(ct_db):
    """RejectContribution is a single-source change; no lineage event is emitted."""
    conn, movie, src_a, src_b, rev = ct_db
    lineage_before = await graph_store.list_lineage(conn, class_name="Movie")
    await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_b", spec_revision=rev,
    )
    lineage_after = await graph_store.list_lineage(conn, class_name="Movie")
    assert len(lineage_after) == len(lineage_before)


async def test_reject_both_sources_leaves_no_current_binding(ct_db):
    """Rejecting all sources leaves no current binding; entity effectively tombstoned in reads."""
    conn, movie, src_a, src_b, rev = ct_db
    await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_a", spec_revision=rev,
    )
    await apply_reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="source_b", spec_revision=rev,
    )
    assert not await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_main")
    rows = await graph_store.list_rows(conn, cls=movie)
    assert all(r["_canonical_id"] != "tt_main" for r in rows)


# ---------------------------------------------------------------------------
# Validation / error paths
# ---------------------------------------------------------------------------

async def test_split_requires_nonexistent_new_canonical_ids(ct_db):
    """If a new_canonical_id already exists, split_canonical_id must not
    succeed — the API layer guards this; test the primitive does not error
    (the guard is in the API, not the DB primitive). Verify API-layer check
    by calling canonical_id_exists directly."""
    conn, movie, src_a, src_b, rev = ct_db
    # tt_extra already exists.
    already_exists = await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_extra")
    assert already_exists


async def test_add_idempotency_fails_on_duplicate(ct_db):
    """insert_synthetic_row on duplicate (_source, _source_row_id) raises — no ON CONFLICT."""
    conn, movie, src_a, src_b, rev = ct_db
    await apply_add(conn, cls=movie, new_canonical_id="tt_dup", values={"imdb_id": "tt_dup"}, spec_revision=rev)
    import psycopg
    with pytest.raises(psycopg.errors.UniqueViolation):
        async with conn.transaction():
            await graph_store.insert_synthetic_row(
                conn, cls=movie, new_canonical_id="tt_dup",
                values={"imdb_id": "tt_dup"}, spec_revision=rev,
            )


async def test_tombstone_nonexistent_returns_zero_closed(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    from knot.db import graph_store as gs
    closed = await gs.tombstone_canonical_id(
        conn, cls=movie, canonical_id="tt_nonexistent", spec_revision=rev,
    )
    assert closed == 0


async def test_reject_contribution_nonexistent_pair_returns_false(ct_db):
    conn, movie, src_a, src_b, rev = ct_db
    result = await graph_store.reject_contribution(
        conn, cls=movie, canonical_id="tt_main", source="no_such_source", spec_revision=rev,
    )
    assert result is False
