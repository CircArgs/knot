"""Corrections tests — apply_property_correction, apply_merge, bandit feedback.

Uses the `corrections_db` fixture which publishes a Movie spec and
inserts rows from two sources so corrections have data to act on.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db import corrections as db_corrections
from knot.db import graph_store, trust_posteriors
from knot.db.trust_posteriors import PRIOR_ALPHA, PRIOR_BETA
from knot.graph.corrections import _values_match, apply_merge, apply_property_correction
from knot.spec import OntologyClass, ResolutionPolicy, Slot, Source, Spec, TypeDefinition
from tests._helpers import publish_spec

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def corrections_db(pg_conn):
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
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st, resolution_policy=ResolutionPolicy.POSTERIOR_MEAN)
    tags = Slot(name="tags", range=st, multivalued=True)
    movie = OntologyClass(name="Movie", slots=[id_slot, title, tags])
    src_a = Source(name="source_a", entity_class=movie, identifier_slot=id_slot)
    src_b = Source(name="source_b", entity_class=movie, identifier_slot=id_slot)
    spec = Spec(
        id="corrections_test",
        version="1.0.0",
        types=[st],
        slots=[id_slot, title, tags],
        classes=[movie],
        sources=[src_a, src_b],
    )
    rev = await publish_spec(pg_conn, spec)

    # Insert rows from two sources for canonical_id "tt_canonical"
    await graph_store.insert_rows(
        pg_conn,
        source=src_a,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_canonical", "title": "From A"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_canonical", "title": "From A"}]],
    )
    await graph_store.insert_rows(
        pg_conn,
        source=src_b,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_canonical", "title": "From B"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_canonical", "title": "From B"}]],
    )

    yield pg_conn, movie, src_a, src_b, rev, spec


# ---------------------------------------------------------------------------
# 1. apply_property_correction: audit row + data-plane row + bandit feedback
# ---------------------------------------------------------------------------


async def test_apply_property_correction_writes_audit_row(corrections_db):
    conn, movie, src_a, src_b, rev, spec = corrections_db
    correction_id = await apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="Corrected Title",
        spec_revision=rev,
        applied_by="tester",
    )
    log = await db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == correction_id]
    assert len(matching) == 1
    assert matching[0]["correction_type"] == "property"
    assert matching[0]["applied_by"] == "tester"


async def test_apply_property_correction_upserts_correction_row(corrections_db):
    conn, movie, src_a, src_b, rev, spec = corrections_db
    await apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="Corrected Title",
        spec_revision=rev,
    )
    contribs = await graph_store.get_canonical_contributions(
        conn, cls=movie, canonical_id="tt_canonical"
    )
    correction_rows = [c for c in contribs if c["_source"] == "_user_corrections"]
    assert len(correction_rows) == 1
    assert correction_rows[0]["title"] == "Corrected Title"


async def test_apply_property_correction_returns_int_id(corrections_db):
    conn, movie, src_a, src_b, rev, spec = corrections_db
    cid = await apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="X",
        spec_revision=rev,
    )
    assert isinstance(cid, int)
    assert cid >= 1


# ---------------------------------------------------------------------------
# 2. Bandit feedback: agreement → α += 1; disagreement → β += 1
# ---------------------------------------------------------------------------


async def test_correction_agreement_increments_alpha(corrections_db):
    """Source whose contribution matches the correction → success → α++."""
    conn, movie, src_a, src_b, rev, spec = corrections_db
    # src_a contributes "From A"; correct to "From A" → src_a agrees
    await apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="From A",
        spec_revision=rev,
    )
    p_a = await trust_posteriors.get_posterior(conn, "source_a", "title")
    assert p_a.alpha > PRIOR_ALPHA  # agreement → α incremented


async def test_correction_disagreement_increments_beta(corrections_db):
    """Source whose contribution mismatches the correction → failure → β++."""
    conn, movie, src_a, src_b, rev, spec = corrections_db
    # src_a contributes "From A"; correct to "From A" → src_b disagrees ("From B" ≠ "From A")
    await apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="From A",
        spec_revision=rev,
    )
    p_b = await trust_posteriors.get_posterior(conn, "source_b", "title")
    assert p_b.beta > PRIOR_BETA  # disagreement → β incremented


# ---------------------------------------------------------------------------
# 3. Multivalued equality is order-insensitive
# ---------------------------------------------------------------------------


def test_values_match_multivalued_order_insensitive():
    assert _values_match(["a", "b", "c"], ["c", "a", "b"])


def test_values_match_multivalued_unequal_sets():
    assert not _values_match(["a", "b"], ["a", "c"])


def test_values_match_scalar_equal():
    assert _values_match("hello", "hello")


def test_values_match_scalar_unequal():
    assert not _values_match("hello", "world")


def test_values_match_none_not_equal_to_value():
    assert not _values_match(None, "something")


# ---------------------------------------------------------------------------
# 4. apply_merge: bindings reassigned + lineage event written
# ---------------------------------------------------------------------------


async def test_apply_merge_writes_lineage_event(corrections_db):
    conn, movie, src_a, src_b, rev, spec = corrections_db
    # Insert a second canonical_id to merge into the first
    await graph_store.insert_rows(
        conn,
        source=src_a,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_secondary", "title": "Dup"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_secondary", "title": "Dup"}]],
    )

    await apply_merge(
        conn,
        cls=movie,
        keep_canonical_id="tt_canonical",
        merge_canonical_ids=["tt_secondary"],
        spec_revision=rev,
        spec=spec,
        applied_by="tester",
    )
    lineage = await graph_store.list_lineage(conn, class_name="Movie")
    assert len(lineage) >= 1
    last = lineage[0]
    assert last["change_type"] == "merge"
    assert "tt_secondary" in last["from_canonical_ids"]
    assert "tt_canonical" in last["to_canonical_ids"]


async def test_apply_merge_closes_secondary_canonical_id(corrections_db):
    conn, movie, src_a, src_b, rev, spec = corrections_db
    await graph_store.insert_rows(
        conn,
        source=src_a,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_sec2"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_sec2"}]],
    )
    await apply_merge(
        conn,
        cls=movie,
        keep_canonical_id="tt_canonical",
        merge_canonical_ids=["tt_sec2"],
        spec_revision=rev,
        spec=spec,
    )
    assert not await graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_sec2")


async def test_apply_merge_writes_audit_entry(corrections_db):
    conn, movie, src_a, src_b, rev, spec = corrections_db
    await graph_store.insert_rows(
        conn,
        source=src_a,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_merge_audit"}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_merge_audit"}]],
    )
    cid = await apply_merge(
        conn,
        cls=movie,
        keep_canonical_id="tt_canonical",
        merge_canonical_ids=["tt_merge_audit"],
        spec_revision=rev,
        spec=spec,
        applied_by="merger",
    )
    log = await db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == cid]
    assert matching[0]["correction_type"] == "merge"
    assert matching[0]["applied_by"] == "merger"


# ---------------------------------------------------------------------------
# 5. apply_merge: rewrites cross-class FK references
# ---------------------------------------------------------------------------


async def test_apply_merge_rewrites_cross_class_fk_references(pg_conn):
    """Merging two Person canonical_ids must rewrite Movie.directed_by
    columns that referenced the merged-away id."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()

    st = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=st, identifier=True, required=True)
    person_name = Slot(name="name", range=st)
    person = OntologyClass(name="Person", slots=[person_id, person_name])

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    directed_by = Slot(name="directed_by", range=person)  # cross-class FK
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, directed_by])

    src_movies = Source(name="src_movies", entity_class=movie, identifier_slot=imdb_id)
    src_people = Source(name="src_people", entity_class=person, identifier_slot=person_id)

    spec = Spec(
        id="merge_fk_test",
        version="1.0.0",
        types=[st],
        slots=[person_id, person_name, imdb_id, title, directed_by],
        classes=[person, movie],
        sources=[src_people, src_movies],
    )
    rev = await publish_spec(pg_conn, spec)

    # Two Person canonical_ids: nolan_chris and nolan_christopher
    await graph_store.insert_rows(
        pg_conn,
        source=src_people,
        spec_revision=rev,
        rows=[
            {"person_id": "nolan_chris", "name": "Chris Nolan"},
            {"person_id": "nolan_christopher", "name": "Christopher Nolan"},
        ],
        canonical_ids=["nolan_chris", "nolan_christopher"],
    )
    # Two Movie rows: one referencing each Person canonical_id
    await graph_store.insert_rows(
        pg_conn,
        source=src_movies,
        spec_revision=rev,
        rows=[
            {"imdb_id": "tt_a", "title": "Inception", "directed_by": "nolan_chris"},
            {"imdb_id": "tt_b", "title": "Interstellar", "directed_by": "nolan_christopher"},
        ],
        canonical_ids=["tt_a", "tt_b"],
    )

    # Merge nolan_chris -> nolan_christopher
    await apply_merge(
        pg_conn,
        cls=person,
        keep_canonical_id="nolan_christopher",
        merge_canonical_ids=["nolan_chris"],
        spec_revision=rev,
        spec=spec,
    )

    # Both Movie rows should now reference nolan_christopher
    rows = await graph_store.list_rows(pg_conn, cls=movie)
    directed_by_values = sorted(r["directed_by"] for r in rows)
    assert directed_by_values == ["nolan_christopher", "nolan_christopher"], (
        f"expected both Movie rows to reference nolan_christopher, got {directed_by_values}"
    )
