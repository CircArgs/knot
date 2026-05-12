"""Tests for DELETE /spec/drafts/{draft_id}/{kind}/{name}.

Coverage:
  - Happy paths for entity kinds: class, source, constraint.
  - Reference-protection 409s (a class referenced by a slot's ClassRef /
    another class's is_a / a source / a constraint).
  - DELETE on a published revision is 409.
  - DELETE of an unknown name is 404.

Each test gets a fresh draft holding a small spec built with the
`_make_spec_*` helpers below; we drive the API via FastAPI's TestClient
with the auth dependency overridden by a dev principal.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.api.auth.security import Principal, require_user
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
    Literal_,
    OntologyClass,
    Slot,
    SlotPath,
    Source,
    Spec,
)
from knot.spec.metaschema import ClassRef, Primitive, SourceBinding

# ---------------------------------------------------------------------------
# Helpers — minimal spec factories
# ---------------------------------------------------------------------------


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _spec_with_unreferenced_extras() -> Spec:
    """Movie with imdb_id + year, plus a Source and Constraint that
    can be deleted freely."""
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb_movies")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    year_check = Constraint(
        name="year_positive",
        primary=movie,
        body=Compare(
            op=CompareOp.GTE,
            left=SlotPath(from_class=movie, slots=[year]),
            right=Literal_(value=0),
        ),
    )
    return Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=[year_check],
    )


def _spec_with_class_ref() -> Spec:
    """Movie + Person where Movie.directed_by has ClassRef→Person."""
    person_id = Slot(name="person_id", type=Primitive(name="string"), identifier=True, required=True)
    person = OntologyClass(name="Person", slots=[person_id])

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    directed_by = Slot(name="directed_by", type=ClassRef(target_class=person))
    movie = OntologyClass(name="Movie", slots=[imdb_id, directed_by])

    src_movie = Source(name="imdb_movies")
    src_person = Source(name="wiki_people")
    binding_movie = SourceBinding(source=src_movie, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    binding_person = SourceBinding(source=src_person, class_=person, identifier_slot=person_id)  # type: ignore[call-arg]
    return Spec(
        id="test",
        version="1.0.0",
        classes=[person, movie],
        sources=[src_movie, src_person],
        source_bindings=[binding_movie, binding_person],
    )


def _spec_with_is_a_chain() -> Spec:
    """Movie is_a Title (parent class), so deleting Title should fail."""
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = OntologyClass(name="Title", slots=[imdb_id])
    movie = OntologyClass(name="Movie", slots=[imdb_id], is_a=title)
    src = Source(name="imdb_movies")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    return Spec(
        id="test",
        version="1.0.0",
        classes=[title, movie],
        sources=[src],
        source_bindings=[binding],
    )


def _spec_with_constraint_and_class() -> Spec:
    """Movie with a year constraint; deleting Movie must fail because
    the constraint references it."""
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb_movies")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    nonneg = Constraint(
        name="year_positive",
        primary=movie,
        body=BoolExpr(
            op=BoolOpKind.AND,
            operands=[
                Compare(
                    op=CompareOp.GTE,
                    left=SlotPath(from_class=movie, slots=[year]),
                    right=Literal_(value=0),
                ),
            ],
        ),
    )
    return Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=[nonneg],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_db(pg_conn):
    """Reset all spec-related state. Tests build their own spec from scratch."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()
    yield pg_conn


@pytest.fixture
def client():
    from knot.api.main import app

    app.dependency_overrides[require_user] = _dev_principal
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.pop(require_user, None)


# ---------------------------------------------------------------------------
# Class removal
# ---------------------------------------------------------------------------


async def test_delete_class_referenced_by_slot_classref_returns_409(clean_db, client):
    """A class used as the ClassRef target of any slot cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_class_ref())

    r = client.delete(f"/spec/drafts/{rev}/classes/Person")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Person" in detail
    assert "directed_by" in detail


async def test_delete_class_referenced_by_source_returns_409(clean_db, client):
    """A class used as a source's entity_class cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_class_ref())

    r = client.delete(f"/spec/drafts/{rev}/classes/Movie")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Movie" in detail
    assert "imdb_movies" in detail


async def test_delete_class_in_is_a_chain_returns_409(clean_db, client):
    """A class used as the is_a of another class cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_is_a_chain())

    r = client.delete(f"/spec/drafts/{rev}/classes/Title")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Title" in detail
    assert "Movie" in detail


async def test_delete_class_referenced_by_constraint_returns_409(clean_db, client):
    """A class used as a constraint's primary cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_constraint_and_class())

    r = client.delete(f"/spec/drafts/{rev}/classes/Movie")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Movie" in detail
    assert ("year_positive" in detail) or ("imdb_movies" in detail)


# ---------------------------------------------------------------------------
# Source / constraint removal — no inbound refs possible
# ---------------------------------------------------------------------------


async def test_delete_source_succeeds_without_refs(clean_db, client):
    from knot.db.spec_store import get_revision

    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/sources/imdb_movies")
    assert r.status_code == 200, r.text
    assert r.json()["spec_summary"]["sources"] == 0

    spec = await get_revision(clean_db, rev)
    assert spec.sources == []


async def test_delete_constraint_succeeds_without_refs(clean_db, client):
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/constraints/year_positive")
    assert r.status_code == 200, r.text
    assert r.json()["spec_summary"]["constraints"] == 0


# ---------------------------------------------------------------------------
# Lifecycle errors
# ---------------------------------------------------------------------------


async def test_delete_on_published_draft_returns_409(clean_db, client):
    """Once a draft is published, all DELETE attempts must 409 (immutable)."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())
    await publish_draft(clean_db, rev, allow_destructive=False)

    r = client.delete(f"/spec/drafts/{rev}/sources/imdb_movies")
    assert r.status_code == 409
    assert "published" in r.json()["detail"].lower()


async def test_delete_unknown_name_returns_404(clean_db, client):
    """DELETE of a name not on the draft is 404."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/sources/nonexistent")
    assert r.status_code == 404
