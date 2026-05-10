"""Tests for DELETE /spec/drafts/{draft_id}/{kind}/{name}.

Coverage:
  - Happy paths for each entity kind (type, slot, class, source, constraint).
  - Reference-protection 409s (cannot remove a type referenced by a slot,
    a slot referenced by a class or used as a source's identifier, a class
    referenced by a slot's range / another class's is_a / a source / a
    constraint).
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
    TypeDefinition,
)

# ---------------------------------------------------------------------------
# Helpers — minimal spec factories
# ---------------------------------------------------------------------------


def _dev_principal() -> Principal:
    return Principal(username="dev:default", is_admin=False)


def _spec_with_unreferenced_extras() -> Spec:
    """Movie with imdb_id + year, plus an unreferenced TypeDefinition
    ('color') that nothing else uses, plus a Source and Constraint that
    can be deleted freely."""
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    color = TypeDefinition(name="color", base="str")  # unreferenced
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
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
        types=[st, it, color],
        slots=[imdb_id, year],
        classes=[movie],
        sources=[src],
        constraints=[year_check],
    )


def _spec_with_class_range() -> Spec:
    """Movie + Person where Movie.directed_by has range Person."""
    st = TypeDefinition(name="string", base="str")
    person_id = Slot(name="person_id", range=st, identifier=True, required=True)
    person = OntologyClass(name="Person", slots=[person_id])

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    directed_by = Slot(name="directed_by", range=person)
    movie = OntologyClass(name="Movie", slots=[imdb_id, directed_by])

    src_movie = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
    src_person = Source(name="wiki_people", entity_class=person, identifier_slot=person_id)
    return Spec(
        id="test",
        version="1.0.0",
        types=[st],
        slots=[person_id, imdb_id, directed_by],
        classes=[person, movie],
        sources=[src_movie, src_person],
    )


def _spec_with_is_a_chain() -> Spec:
    """Movie is_a Title (parent class), so deleting Title should fail."""
    st = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = OntologyClass(name="Title", slots=[imdb_id])
    movie = OntologyClass(name="Movie", slots=[imdb_id], is_a=title)
    src = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id="test",
        version="1.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[title, movie],
        sources=[src],
    )


def _spec_with_constraint_and_class() -> Spec:
    """Movie with a year constraint; deleting Movie must fail because
    the constraint references it."""
    st = TypeDefinition(name="string", base="str")
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, year])
    src = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
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
        types=[st, it],
        slots=[imdb_id, year],
        classes=[movie],
        sources=[src],
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
# Type removal
# ---------------------------------------------------------------------------


async def test_delete_type_removes_from_spec(clean_db, client):
    """Happy path: an unreferenced type is removed."""
    from knot.db.spec_store import get_revision

    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/types/color")
    assert r.status_code == 200, r.text
    assert r.json()["spec_summary"]["types"] == 2  # was 3 (string, integer, color)

    # Confirm via the rehydrated spec that 'color' is gone.
    spec = await get_revision(clean_db, rev)
    assert "color" not in {t.name for t in spec.types}
    assert {t.name for t in spec.types} == {"string", "integer"}


async def test_delete_type_referenced_by_slot_returns_409(clean_db, client):
    """A type referenced by any slot's range cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/types/string")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "string" in detail
    # imdb_id is one of the referencing slots (string-typed identifier)
    assert "imdb_id" in detail


# ---------------------------------------------------------------------------
# Slot removal
# ---------------------------------------------------------------------------


async def test_delete_slot_referenced_by_class_returns_409(clean_db, client):
    """A slot listed on any class cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/slots/year")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "year" in detail
    assert "Movie" in detail


async def test_delete_slot_used_as_identifier_returns_409(clean_db, client):
    """A slot used as a source's identifier_slot cannot be removed.

    `imdb_id` is on `Movie` AND is the identifier_slot of `imdb_movies`,
    so the references list should mention both.
    """
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/slots/imdb_id")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "imdb_id" in detail
    # Must mention the source, since that's the identifier_slot link.
    assert "imdb_movies" in detail


# ---------------------------------------------------------------------------
# Class removal
# ---------------------------------------------------------------------------


async def test_delete_class_referenced_by_slot_range_returns_409(clean_db, client):
    """A class used as the range of any slot cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_class_range())

    r = client.delete(f"/spec/drafts/{rev}/classes/Person")
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "Person" in detail
    assert "directed_by" in detail


async def test_delete_class_referenced_by_source_returns_409(clean_db, client):
    """A class used as a source's entity_class cannot be removed."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_class_range())

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
    # Movie is the constraint's primary AND the source's entity_class — at
    # least one (likely both) reference should appear.
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

    r = client.delete(f"/spec/drafts/{rev}/types/color")
    assert r.status_code == 409
    assert "published" in r.json()["detail"].lower()


async def test_delete_unknown_name_returns_404(clean_db, client):
    """DELETE of a name not on the draft is 404."""
    rev = await create_draft(clean_db)
    await update_draft(clean_db, rev, _spec_with_unreferenced_extras())

    r = client.delete(f"/spec/drafts/{rev}/types/nonexistent")
    assert r.status_code == 404
