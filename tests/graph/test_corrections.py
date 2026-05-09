"""Corrections tests — apply_property_correction, apply_merge, bandit feedback.

Uses the `corrections_db` fixture which publishes a Movie spec and
inserts rows from two sources so corrections have data to act on.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db import corrections as db_corrections
from knot.db import graph_store, trust_posteriors
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.db.trust_posteriors import PRIOR_ALPHA, PRIOR_BETA
from knot.graph.corrections import apply_merge, apply_property_correction, _values_match
from knot.spec import OntologyClass, ResolutionPolicy, Slot, Source, Spec, TypeDefinition


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def corrections_db(pg_conn):
    """Full reset, publish Movie spec, insert rows from two sources."""
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()

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
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    # Insert rows from two sources for canonical_id "tt_canonical"
    graph_store.insert_rows(pg_conn, source=src_a, spec_revision=rev,
                            rows=[{"imdb_id": "tt_canonical", "title": "From A"}],
                            canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_canonical", "title": "From A"}]])
    graph_store.insert_rows(pg_conn, source=src_b, spec_revision=rev,
                            rows=[{"imdb_id": "tt_canonical", "title": "From B"}],
                            canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_canonical", "title": "From B"}]])

    yield pg_conn, movie, src_a, src_b, rev


# ---------------------------------------------------------------------------
# 1. apply_property_correction: audit row + data-plane row + bandit feedback
# ---------------------------------------------------------------------------

def test_apply_property_correction_writes_audit_row(corrections_db):
    conn, movie, src_a, src_b, rev = corrections_db
    correction_id = apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="Corrected Title",
        spec_revision=rev,
        applied_by="tester",
    )
    log = db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == correction_id]
    assert len(matching) == 1
    assert matching[0]["correction_type"] == "property"
    assert matching[0]["applied_by"] == "tester"


def test_apply_property_correction_upserts_correction_row(corrections_db):
    conn, movie, src_a, src_b, rev = corrections_db
    apply_property_correction(
        conn,
        cls=movie,
        canonical_id="tt_canonical",
        slot_name="title",
        value="Corrected Title",
        spec_revision=rev,
    )
    contribs = graph_store.get_canonical_contributions(conn, cls=movie, canonical_id="tt_canonical")
    correction_rows = [c for c in contribs if c["_source"] == "_user_corrections"]
    assert len(correction_rows) == 1
    assert correction_rows[0]["title"] == "Corrected Title"


def test_apply_property_correction_returns_int_id(corrections_db):
    conn, movie, src_a, src_b, rev = corrections_db
    cid = apply_property_correction(
        conn, cls=movie, canonical_id="tt_canonical",
        slot_name="title", value="X", spec_revision=rev,
    )
    assert isinstance(cid, int)
    assert cid >= 1


# ---------------------------------------------------------------------------
# 2. Bandit feedback: agreement → α += 1; disagreement → β += 1
# ---------------------------------------------------------------------------

def test_correction_agreement_increments_alpha(corrections_db):
    """Source whose contribution matches the correction → success → α++."""
    conn, movie, src_a, src_b, rev = corrections_db
    # src_a contributes "From A"; correct to "From A" → src_a agrees
    apply_property_correction(
        conn, cls=movie, canonical_id="tt_canonical",
        slot_name="title", value="From A", spec_revision=rev,
    )
    p_a = trust_posteriors.get_posterior(conn, "source_a", "title")
    assert p_a.alpha > PRIOR_ALPHA  # agreement → α incremented


def test_correction_disagreement_increments_beta(corrections_db):
    """Source whose contribution mismatches the correction → failure → β++."""
    conn, movie, src_a, src_b, rev = corrections_db
    # src_a contributes "From A"; correct to "From A" → src_b disagrees ("From B" ≠ "From A")
    apply_property_correction(
        conn, cls=movie, canonical_id="tt_canonical",
        slot_name="title", value="From A", spec_revision=rev,
    )
    p_b = trust_posteriors.get_posterior(conn, "source_b", "title")
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

def test_apply_merge_writes_lineage_event(corrections_db):
    conn, movie, src_a, src_b, rev = corrections_db
    # Insert a second canonical_id to merge into the first
    graph_store.insert_rows(conn, source=src_a, spec_revision=rev,
                            rows=[{"imdb_id": "tt_secondary", "title": "Dup"}],
                            canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_secondary", "title": "Dup"}]])

    apply_merge(
        conn,
        cls=movie,
        keep_canonical_id="tt_canonical",
        merge_canonical_ids=["tt_secondary"],
        spec_revision=rev,
        applied_by="tester",
    )
    lineage = graph_store.list_lineage(conn, class_name="Movie")
    assert len(lineage) >= 1
    last = lineage[0]
    assert last["change_type"] == "merge"
    assert "tt_secondary" in last["from_canonical_ids"]
    assert "tt_canonical" in last["to_canonical_ids"]


def test_apply_merge_closes_secondary_canonical_id(corrections_db):
    conn, movie, src_a, src_b, rev = corrections_db
    graph_store.insert_rows(conn, source=src_a, spec_revision=rev,
                            rows=[{"imdb_id": "tt_sec2"}],
                            canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_sec2"}]])
    apply_merge(
        conn, cls=movie,
        keep_canonical_id="tt_canonical",
        merge_canonical_ids=["tt_sec2"],
        spec_revision=rev,
    )
    assert not graph_store.canonical_id_exists(conn, cls=movie, canonical_id="tt_sec2")


def test_apply_merge_writes_audit_entry(corrections_db):
    conn, movie, src_a, src_b, rev = corrections_db
    graph_store.insert_rows(conn, source=src_a, spec_revision=rev,
                            rows=[{"imdb_id": "tt_merge_audit"}],
                            canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt_merge_audit"}]])
    cid = apply_merge(
        conn, cls=movie,
        keep_canonical_id="tt_canonical",
        merge_canonical_ids=["tt_merge_audit"],
        spec_revision=rev,
        applied_by="merger",
    )
    log = db_corrections.list_audit_log(conn)
    matching = [e for e in log if e["id"] == cid]
    assert matching[0]["correction_type"] == "merge"
    assert matching[0]["applied_by"] == "merger"
