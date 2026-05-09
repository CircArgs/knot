"""Graph store (SCD2 data plane) tests.

Tests cover insert_rows, list_rows, count_rows, canonical_id_exists,
get_canonical_contributions, merge_canonical_ids, and the partial UNIQUE
index that enforces at-most-one current binding per knot_row_id.

Each test uses the `graph_db` fixture which publishes a minimal spec so
the knot_data.movie and knot_data.movie_bindings tables exist.
"""

from __future__ import annotations

import uuid

import pytest
import psycopg

from knot import db
from knot.db import graph_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_spec() -> tuple[Spec, OntologyClass, Source, int]:
    """Returns (spec, movie_class, imdb_source, revision_placeholder).
    revision_placeholder is 0 — caller fills in the real revision after publish.
    """
    st = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    year = Slot(name="year", range=int_t)
    tags = Slot(name="tags", range=st, multivalued=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year, tags])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="test",
        version="1.0.0",
        types=[st, int_t],
        slots=[imdb_id, title, year, tags],
        classes=[movie],
        sources=[src],
    )
    return spec, movie, src, 0


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def graph_db(pg_conn):
    """Full reset then publish a minimal Movie spec so tables exist."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    spec, movie, src, _ = _build_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    yield pg_conn, movie, src, rev


# ---------------------------------------------------------------------------
# 1. insert_rows writes source row + binding
# ---------------------------------------------------------------------------

async def test_insert_rows_returns_count(graph_db):
    conn, movie, src, rev = graph_db
    n = await graph_store.insert_rows(
        conn,
        source=src,
        spec_revision=rev,
        rows=[{"imdb_id": "tt0000001", "title": "Test Movie"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000001", "title": "Test Movie"}]]
    )
    assert n == 1


async def test_insert_rows_creates_current_binding(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(
        conn, source=src, spec_revision=rev,
        rows=[{"imdb_id": "tt0000001", "title": "Test Movie"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000001", "title": "Test Movie"}]]
    )
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt0000001")


async def test_insert_rows_binding_has_null_valid_to(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(
        conn, source=src, spec_revision=rev,
        rows=[{"imdb_id": "tt0000002", "title": "Another"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000002", "title": "Another"}]]
    )
    row = await (await conn.execute(
        "SELECT valid_to FROM knot_data.movie_bindings WHERE canonical_id = %s AND valid_to IS NULL",
        ("tt0000002",),
    )).fetchone()
    assert row is not None
    assert row[0] is None


# ---------------------------------------------------------------------------
# 2. Re-pushing same (source, source_row_id) does NOT open new binding
# ---------------------------------------------------------------------------

async def test_repush_same_row_does_not_duplicate_binding(graph_db):
    conn, movie, src, rev = graph_db
    row_data = {"imdb_id": "tt0000003", "title": "Original"}
    await graph_store.insert_rows(conn, source=src, spec_revision=rev, rows=[row_data],
                        canonical_ids=[str(r["imdb_id"]) for r in [row_data]])
    await graph_store.insert_rows(
        conn, source=src, spec_revision=rev,
        rows=[{"imdb_id": "tt0000003", "title": "Updated title"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000003", "title": "Updated title"}]]
    )
    # Exactly one current binding
    count = (await (await conn.execute(
        "SELECT count(*) FROM knot_data.movie_bindings WHERE canonical_id = %s AND valid_to IS NULL",
        ("tt0000003",),
    )).fetchone())[0]
    assert count == 1


async def test_repush_updates_source_row_content(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt0000004", "title": "Old"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000004", "title": "Old"}]])
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt0000004", "title": "New"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000004", "title": "New"}]])
    contribs = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt0000004")
    titles = [c["title"] for c in contribs]
    assert "New" in titles


# ---------------------------------------------------------------------------
# 3. canonical_id_exists only looks at current bindings
# ---------------------------------------------------------------------------

async def test_canonical_id_exists_returns_false_for_unknown(graph_db):
    conn, movie, src, rev = graph_db
    assert not await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_not_here")


async def test_canonical_id_exists_after_insert(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt0000005"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000005"}]])
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt0000005")


# ---------------------------------------------------------------------------
# 4. list_rows JOINs source × current bindings; _canonical_id present
# ---------------------------------------------------------------------------

async def test_list_rows_includes_canonical_id(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt0000006", "title": "Listed"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000006", "title": "Listed"}]])
    rows = await graph_store.list_rows(conn, cls=movie)
    matching = [r for r in rows if r.get("_canonical_id") == "tt0000006"]
    assert matching, "Row not found in list_rows output"
    assert matching[0]["title"] == "Listed"


async def test_list_rows_excludes_closed_bindings(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt0000007"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000007"}]])
    # Manually close the binding
    await conn.execute(
        "UPDATE knot_data.movie_bindings SET valid_to = now() WHERE canonical_id = %s AND valid_to IS NULL",
        ("tt0000007",),
    )
    rows = await graph_store.list_rows(conn, cls=movie)
    assert not any(r.get("_canonical_id") == "tt0000007" for r in rows)


# ---------------------------------------------------------------------------
# 5. as_of filtering
# ---------------------------------------------------------------------------

async def test_list_rows_as_of_excludes_later_revisions(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt_asof"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_asof"}]])
    # as_of = rev-1 (before ingest) should exclude this row
    rows = await graph_store.list_rows(conn, cls=movie, as_of=rev - 1)
    assert not any(r.get("_canonical_id") == "tt_asof" for r in rows)


async def test_list_rows_as_of_includes_current_revision(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt_asof2"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_asof2"}]])
    rows = await graph_store.list_rows(conn, cls=movie, as_of=rev)
    assert any(r.get("_canonical_id") == "tt_asof2" for r in rows)


# ---------------------------------------------------------------------------
# 6. get_canonical_contributions
# ---------------------------------------------------------------------------

async def test_get_canonical_contributions_returns_one_row_per_source(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt0000008", "title": "Contrib"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000008", "title": "Contrib"}]])
    contribs = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt0000008")
    assert len(contribs) == 1
    assert contribs[0]["_source"] == "imdb"


async def test_get_canonical_contributions_returns_empty_for_unknown(graph_db):
    conn, movie, src, rev = graph_db
    contribs = await graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="unknown_id")
    assert contribs == []


# ---------------------------------------------------------------------------
# 7. count_rows
# ---------------------------------------------------------------------------

async def test_count_rows_reflects_current_bindings(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "cnt1"}, {"imdb_id": "cnt2"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "cnt1"}, {"imdb_id": "cnt2"}]])
    assert await graph_store.count_rows(conn, cls=movie) >= 2


# ---------------------------------------------------------------------------
# 8. merge_canonical_ids — SCD2 mechanics
# ---------------------------------------------------------------------------

async def test_merge_closes_source_binding_and_opens_new(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt_keep"}, {"imdb_id": "tt_merge"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_keep"}, {"imdb_id": "tt_merge"}]])

    rewritten = await graph_store.merge_canonical_ids(
        conn,
        cls=movie,
        keep_canonical_id="tt_keep",
        from_canonical_ids=["tt_merge"],
        spec_revision=rev,
    )
    assert rewritten == 1
    # After merge, tt_merge canonical_id should have no current binding
    assert not await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_merge")
    # The row is now bound to tt_keep
    assert await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_keep")


async def test_merge_preserves_valid_from_lt_valid_to_invariant(graph_db):
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt_inv_keep"}, {"imdb_id": "tt_inv_merge"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_inv_keep"}, {"imdb_id": "tt_inv_merge"}]])
    await graph_store.merge_canonical_ids(
        conn, cls=movie,
        keep_canonical_id="tt_inv_keep",
        from_canonical_ids=["tt_inv_merge"],
        spec_revision=rev,
    )
    # Closed binding should have valid_to > valid_from
    bad = (await (await conn.execute(
        "SELECT count(*) FROM knot_data.movie_bindings "
        "WHERE canonical_id = 'tt_inv_merge' AND valid_to IS NOT NULL AND valid_to <= valid_from"
    )).fetchone())[0]
    assert bad == 0


async def test_partial_unique_index_holds_after_merge(graph_db):
    """After merge no knot_row_id should have two current bindings."""
    conn, movie, src, rev = graph_db
    await graph_store.insert_rows(conn, source=src, spec_revision=rev,
                        rows=[{"imdb_id": "tt_u1"}, {"imdb_id": "tt_u2"}],
                        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_u1"}, {"imdb_id": "tt_u2"}]])
    await graph_store.merge_canonical_ids(
        conn, cls=movie,
        keep_canonical_id="tt_u1",
        from_canonical_ids=["tt_u2"],
        spec_revision=rev,
    )
    # Query for any knot_row_id that has > 1 current binding
    dups = await (await conn.execute(
        "SELECT knot_row_id, count(*) "
        "FROM knot_data.movie_bindings WHERE valid_to IS NULL "
        "GROUP BY knot_row_id HAVING count(*) > 1"
    )).fetchall()
    assert dups == [], f"Duplicate current bindings found: {dups}"
