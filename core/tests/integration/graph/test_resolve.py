"""Resolution policy tests — DB-backed paths.

Pure-Python helper tests for ``_argmax_trust`` / ``_posterior_mean`` /
``_lcb`` / ``_union_multivalued`` live in
``tests/unit/graph/test_resolve_helpers.py``. This file covers
``record_feedback`` (writes to ``trust_posteriors``) and
``resolve_entity`` (joins source contributions + posteriors).
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db import graph_store, trust_config, trust_posteriors
from knot.db.trust_posteriors import PRIOR_ALPHA, PRIOR_BETA
from knot.graph.resolve import resolve_entity
from knot.spec import (
    OntologyClass,
    ResolutionPolicy,
    Slot,
    Source,
    Spec,
    TypeDefinition,
)
from tests._helpers import publish_spec

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def resolve_db(pg_conn):
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
    title = Slot(name="title", range=st, resolution_policy=ResolutionPolicy.ARGMAX_TRUST)
    pm_slot = Slot(name="pm_field", range=st, resolution_policy=ResolutionPolicy.POSTERIOR_MEAN)
    lcb_slot = Slot(name="lcb_field", range=st, resolution_policy=ResolutionPolicy.LCB)
    tags = Slot(name="tags", range=st, multivalued=True)
    movie = OntologyClass(name="Movie", slots=[id_slot, title, pm_slot, lcb_slot, tags])
    src_a = Source(name="source_a", entity_class=movie, identifier_slot=id_slot)
    src_b = Source(name="source_b", entity_class=movie, identifier_slot=id_slot)
    spec = Spec(
        id="resolve_test",
        version="1.0.0",
        types=[st],
        slots=[id_slot, title, pm_slot, lcb_slot, tags],
        classes=[movie],
        sources=[src_a, src_b],
    )
    rev = await publish_spec(pg_conn, spec)
    yield pg_conn, movie, src_a, src_b, rev


# ---------------------------------------------------------------------------
# record_feedback — atomic UPSERT delta
# ---------------------------------------------------------------------------


async def test_record_feedback_increments_alpha_on_success(resolve_db):
    conn, movie, src_a, src_b, rev = resolve_db
    p1 = await trust_posteriors.record_feedback(conn, "source_a", "title", success=True)
    assert p1.alpha == PRIOR_ALPHA + 1.0
    assert p1.beta == PRIOR_BETA


async def test_record_feedback_increments_beta_on_failure(resolve_db):
    conn, movie, src_a, src_b, rev = resolve_db
    p1 = await trust_posteriors.record_feedback(conn, "source_a", "pm_field", success=False)
    assert p1.alpha == PRIOR_ALPHA
    assert p1.beta == PRIOR_BETA + 1.0


async def test_record_feedback_accumulates_across_calls(resolve_db):
    conn, movie, src_a, src_b, rev = resolve_db
    await trust_posteriors.record_feedback(conn, "source_b", "pm_field", success=True)
    await trust_posteriors.record_feedback(conn, "source_b", "pm_field", success=True)
    p = await trust_posteriors.get_posterior(conn, "source_b", "pm_field")
    assert p.alpha == PRIOR_ALPHA + 2.0


# ---------------------------------------------------------------------------
# resolve_entity — full integration
# ---------------------------------------------------------------------------


async def test_resolve_entity_returns_none_for_unknown_canonical_id(resolve_db):
    conn, movie, src_a, src_b, rev = resolve_db
    result = await resolve_entity(conn, cls=movie, canonical_id="not_here")
    assert result is None


async def test_resolve_entity_argmax_trust_picks_highest_trust_source(resolve_db):
    conn, movie, src_a, src_b, rev = resolve_db
    await graph_store.insert_rows(
        conn,
        source=src_a,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_res1", "title": "Title from A"}],
        canonical_ids=[
            str(r["imdb_id"]) for r in [{"imdb_id": "tt_res1", "title": "Title from A"}]
        ],
    )
    await graph_store.insert_rows(
        conn,
        source=src_b,
        spec_revision=rev,
        rows=[{"imdb_id": "tt_res1", "title": "Title from B"}],
        canonical_ids=[
            str(r["imdb_id"]) for r in [{"imdb_id": "tt_res1", "title": "Title from B"}]
        ],
    )
    # Set source_a higher trust
    await trust_config.set_score(conn, "source_a", 0.9)
    await trust_config.set_score(conn, "source_b", 0.1)

    result = await resolve_entity(conn, cls=movie, canonical_id="tt_res1")
    assert result is not None
    assert result["title"] == "Title from A"
