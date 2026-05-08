"""Spec lifecycle tests — draft CRUD, publish gate, constraints.

Each test is hermetic: the `clean_spec` fixture truncates spec_revisions
(and cascades to dependent tables) then re-applies the control schema
so every test starts with an empty slate.
"""

from __future__ import annotations

import pytest
import psycopg

from knot import db
from knot.db.spec_store import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    PublishGateError,
    create_draft,
    discard_draft,
    get_published,
    get_published_revision,
    get_revision,
    list_drafts,
    publish_draft,
    spec_from_dict,
    spec_to_dict,
    update_draft,
)
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition


# ---------------------------------------------------------------------------
# Helpers — minimal valid spec factory
# ---------------------------------------------------------------------------

def _string_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _minimal_spec(name: str = "test") -> Spec:
    """A valid spec with one class, one slot, one source."""
    st = _string_type()
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="imdb_movies", entity_class=movie, identifier_slot=imdb_id)
    return Spec(
        id=name,
        version="1.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[movie],
        sources=[src],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_spec(pg_conn):
    """Truncate spec-related state and drop all knot_data tables."""
    # Drop knot_data schema + contents first (FK order matters)
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()
    yield pg_conn


# ---------------------------------------------------------------------------
# 1. Basic draft create → update → publish flow
# ---------------------------------------------------------------------------

def test_create_draft_returns_revision_number(clean_spec):
    rev = create_draft(clean_spec)
    assert isinstance(rev, int)
    assert rev >= 1


def test_draft_starts_unpublished(clean_spec):
    rev = create_draft(clean_spec)
    drafts = list_drafts(clean_spec)
    assert any(d["revision"] == rev for d in drafts)
    assert get_published(clean_spec) is None


def test_update_draft_changes_content(clean_spec):
    rev = create_draft(clean_spec)
    spec = _minimal_spec("updated")
    update_draft(clean_spec, rev, spec)
    reloaded = get_revision(clean_spec, rev)
    assert reloaded.id == "updated"


def test_publish_draft_makes_it_published(clean_spec):
    rev = create_draft(clean_spec)
    update_draft(clean_spec, rev, _minimal_spec())
    publish_draft(clean_spec, rev)
    assert get_published_revision(clean_spec) == rev


def test_published_spec_not_in_draft_list(clean_spec):
    rev = create_draft(clean_spec)
    update_draft(clean_spec, rev, _minimal_spec())
    publish_draft(clean_spec, rev)
    drafts = list_drafts(clean_spec)
    assert not any(d["revision"] == rev for d in drafts)


# ---------------------------------------------------------------------------
# 2. Draft from specific parent_revision
# ---------------------------------------------------------------------------

def test_draft_from_parent_revision(clean_spec):
    rev1 = create_draft(clean_spec)
    update_draft(clean_spec, rev1, _minimal_spec("v1"))
    publish_draft(clean_spec, rev1)

    rev2 = create_draft(clean_spec, parent_revision=rev1)
    loaded = get_revision(clean_spec, rev2)
    # The draft inherits the parent's spec content
    assert loaded.id == "v1"

    drafts = list_drafts(clean_spec)
    matching = [d for d in drafts if d["revision"] == rev2]
    assert matching[0]["parent_revision"] == rev1


# ---------------------------------------------------------------------------
# 3. Discard draft
# ---------------------------------------------------------------------------

def test_discard_draft_removes_it(clean_spec):
    rev = create_draft(clean_spec)
    discard_draft(clean_spec, rev)
    with pytest.raises(DraftNotFoundError):
        get_revision(clean_spec, rev)


def test_discard_published_draft_raises(clean_spec):
    rev = create_draft(clean_spec)
    update_draft(clean_spec, rev, _minimal_spec())
    publish_draft(clean_spec, rev)
    with pytest.raises(DraftAlreadyPublishedError):
        discard_draft(clean_spec, rev)


def test_update_published_draft_raises(clean_spec):
    rev = create_draft(clean_spec)
    update_draft(clean_spec, rev, _minimal_spec())
    publish_draft(clean_spec, rev)
    with pytest.raises(DraftAlreadyPublishedError):
        update_draft(clean_spec, rev, _minimal_spec("mutated"))


# ---------------------------------------------------------------------------
# 4. Publish gate — dangling refs rejected
# ---------------------------------------------------------------------------

def test_publish_gate_rejects_dangling_slot_range(clean_spec):
    """A slot whose range OntologyClass is not on spec.classes fails gate."""
    st = _string_type()
    orphan_class = OntologyClass(name="Orphan", slots=[])
    bad_slot = Slot(name="bad", range=orphan_class)
    movie = OntologyClass(name="Movie", slots=[bad_slot])
    # identifier must exist on spec.slots as a real slot
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie.slots.insert(0, id_slot)
    src = Source(name="src", entity_class=movie, identifier_slot=id_slot)
    spec = Spec(
        id="bad",
        version="1.0.0",
        types=[st],
        slots=[id_slot, bad_slot],
        classes=[movie],  # Orphan intentionally missing
        sources=[src],
    )
    rev = create_draft(clean_spec)
    update_draft(clean_spec, rev, spec)
    with pytest.raises(PublishGateError):
        publish_draft(clean_spec, rev)


def test_publish_gate_rejects_source_with_unknown_class(clean_spec):
    st = _string_type()
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    ghost = OntologyClass(name="Ghost", slots=[id_slot])
    src = Source(name="src", entity_class=ghost, identifier_slot=id_slot)
    spec = Spec(
        id="bad",
        version="1.0.0",
        types=[st],
        slots=[id_slot],
        classes=[movie],  # ghost not here
        sources=[src],
    )
    rev = create_draft(clean_spec)
    update_draft(clean_spec, rev, spec)
    with pytest.raises(PublishGateError):
        publish_draft(clean_spec, rev)


# ---------------------------------------------------------------------------
# 5. Partial unique index: at-most-one published row
# ---------------------------------------------------------------------------

def test_only_one_published_revision_at_a_time(clean_spec):
    # Publish rev1, then publish rev2 — rev1 must be demoted
    rev1 = create_draft(clean_spec)
    update_draft(clean_spec, rev1, _minimal_spec("v1"))
    publish_draft(clean_spec, rev1)

    rev2 = create_draft(clean_spec)
    update_draft(clean_spec, rev2, _minimal_spec("v2"))
    publish_draft(clean_spec, rev2)

    assert get_published_revision(clean_spec) == rev2

    # Directly check no two rows have published=TRUE
    count = clean_spec.execute(
        "SELECT count(*) FROM spec_revisions WHERE published = TRUE"
    ).fetchone()[0]
    assert count == 1


# ---------------------------------------------------------------------------
# 6. Missing revision raises DraftNotFoundError
# ---------------------------------------------------------------------------

def test_get_revision_raises_for_unknown(clean_spec):
    with pytest.raises(DraftNotFoundError):
        get_revision(clean_spec, 999999)


# ---------------------------------------------------------------------------
# 7. Spec name pattern — validated by metaschema Pydantic model
# ---------------------------------------------------------------------------

def test_entity_name_pattern_rejects_bad_names():
    """OntologyClass/Slot names must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$."""
    from pydantic import ValidationError
    for bad in ("bad name", "1bad", "foo;DROP TABLE", "", " leading"):
        with pytest.raises(ValidationError):
            OntologyClass(name=bad, slots=[])


def test_entity_name_pattern_allows_good_names():
    for good in ("Movie", "imdb_id", "_internal", "A1B2C3"):
        cls = OntologyClass(name=good, slots=[])
        assert cls.name == good


# ---------------------------------------------------------------------------
# 8. Case-twin collision detection
# ---------------------------------------------------------------------------

def test_case_twin_classes_can_be_constructed():
    """The metaschema doesn't prevent case-twins; publish gate relies on
    DDL (lowercasing to the same table name) being caught at migration time.
    This test confirms both objects can be built, not that they're valid together."""
    cls_a = OntologyClass(name="Movie", slots=[])
    cls_b = OntologyClass(name="movie", slots=[])
    assert cls_a.name == "Movie"
    assert cls_b.name == "movie"


# ---------------------------------------------------------------------------
# 9. Destructive change: publish refuses without allow_destructive
# ---------------------------------------------------------------------------

def test_publish_refuses_destructive_without_flag(clean_spec):
    """Dropping a class between revisions is destructive — gate must block."""
    # Publish v1 with Movie class
    rev1 = create_draft(clean_spec)
    update_draft(clean_spec, rev1, _minimal_spec("v1"))
    publish_draft(clean_spec, rev1)

    # v2: empty spec (Movie class dropped → DropClass change)
    rev2 = create_draft(clean_spec)
    empty_spec = Spec(id="v2", version="1.0.0")
    update_draft(clean_spec, rev2, empty_spec)
    with pytest.raises(PublishGateError, match="destructive"):
        publish_draft(clean_spec, rev2)


def test_publish_accepts_destructive_with_flag(clean_spec):
    rev1 = create_draft(clean_spec)
    update_draft(clean_spec, rev1, _minimal_spec("v1"))
    publish_draft(clean_spec, rev1)

    rev2 = create_draft(clean_spec)
    empty_spec = Spec(id="v2", version="1.0.0")
    update_draft(clean_spec, rev2, empty_spec)
    result = publish_draft(clean_spec, rev2, allow_destructive=True)
    assert result == rev2
    assert get_published_revision(clean_spec) == rev2
