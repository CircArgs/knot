"""Spec lifecycle tests — draft CRUD, publish gate, constraints.

Each test is hermetic: the `clean_spec` fixture truncates spec_revisions
(and cascades to dependent tables) then re-applies the control schema
so every test starts with an empty slate.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    discard_draft,
    get_published,
    get_published_revision,
    get_revision,
    list_drafts,
    publish_draft,
    update_draft,
)
from knot.spec import OntologyClass, Primitive, Slot, Source, Spec
from knot.spec.errors import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    PublishGateError,
)
from knot.spec.metaschema import SourceBinding

# ---------------------------------------------------------------------------
# Helpers — minimal valid spec factory
# ---------------------------------------------------------------------------


def _minimal_spec(name: str = "test") -> Spec:
    """A valid spec with one class, one slot, one source."""
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="imdb_movies")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    return Spec(
        id=name,
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_spec(pg_conn):
    """Truncate spec-related state and drop all knot_data tables."""
    # Drop knot_data schema + contents first (FK order matters)
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()
    yield pg_conn


# ---------------------------------------------------------------------------
# 1. Basic draft create → update → publish flow
# ---------------------------------------------------------------------------


async def test_create_draft_returns_revision_number(clean_spec):
    rev = await create_draft(clean_spec)
    assert isinstance(rev, int)
    assert rev >= 1


async def test_draft_starts_unpublished(clean_spec):
    rev = await create_draft(clean_spec)
    drafts = await list_drafts(clean_spec)
    assert any(d["revision"] == rev for d in drafts)
    assert await get_published(clean_spec) is None


async def test_update_draft_changes_content(clean_spec):
    rev = await create_draft(clean_spec)
    spec = _minimal_spec("updated")
    await update_draft(clean_spec, rev, spec)
    reloaded = await get_revision(clean_spec, rev)
    assert reloaded.id == "updated"


async def test_publish_draft_makes_it_published(clean_spec):
    rev = await create_draft(clean_spec)
    await update_draft(clean_spec, rev, _minimal_spec())
    await publish_draft(clean_spec, rev)
    assert await get_published_revision(clean_spec) == rev


async def test_published_spec_not_in_draft_list(clean_spec):
    rev = await create_draft(clean_spec)
    await update_draft(clean_spec, rev, _minimal_spec())
    await publish_draft(clean_spec, rev)
    drafts = await list_drafts(clean_spec)
    assert not any(d["revision"] == rev for d in drafts)


# ---------------------------------------------------------------------------
# 2. Draft from specific parent_revision
# ---------------------------------------------------------------------------


async def test_draft_from_parent_revision(clean_spec):
    rev1 = await create_draft(clean_spec)
    await update_draft(clean_spec, rev1, _minimal_spec("v1"))
    await publish_draft(clean_spec, rev1)

    rev2 = await create_draft(clean_spec, parent_revision=rev1)
    loaded = await get_revision(clean_spec, rev2)
    # The draft inherits the parent's spec content
    assert loaded.id == "v1"

    drafts = await list_drafts(clean_spec)
    matching = [d for d in drafts if d["revision"] == rev2]
    assert matching[0]["parent_revision"] == rev1


# ---------------------------------------------------------------------------
# 3. Discard draft
# ---------------------------------------------------------------------------


async def test_discard_draft_removes_it(clean_spec):
    rev = await create_draft(clean_spec)
    await discard_draft(clean_spec, rev)
    with pytest.raises(DraftNotFoundError):
        await get_revision(clean_spec, rev)


async def test_discard_published_draft_raises(clean_spec):
    rev = await create_draft(clean_spec)
    await update_draft(clean_spec, rev, _minimal_spec())
    await publish_draft(clean_spec, rev)
    with pytest.raises(DraftAlreadyPublishedError):
        await discard_draft(clean_spec, rev)


async def test_update_published_draft_raises(clean_spec):
    rev = await create_draft(clean_spec)
    await update_draft(clean_spec, rev, _minimal_spec())
    await publish_draft(clean_spec, rev)
    with pytest.raises(DraftAlreadyPublishedError):
        await update_draft(clean_spec, rev, _minimal_spec("mutated"))


# ---------------------------------------------------------------------------
# 4. Publish gate — dangling refs rejected
# ---------------------------------------------------------------------------


async def test_publish_gate_rejects_dangling_classref(clean_spec):
    """A slot whose ClassRef target is not on spec.classes fails gate."""
    from knot.spec import ClassRef
    orphan_class = OntologyClass(name="Orphan", slots=[])
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    bad_slot = Slot(name="bad", type=ClassRef(target_class=orphan_class))
    movie = OntologyClass(name="Movie", slots=[id_slot, bad_slot])
    src = Source(name="src")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec = Spec(
        id="bad",
        version="1.0.0",
        classes=[movie],  # Orphan intentionally missing
        sources=[src],
        source_bindings=[binding],
    )
    rev = await create_draft(clean_spec)
    await update_draft(clean_spec, rev, spec)
    with pytest.raises(PublishGateError):
        await publish_draft(clean_spec, rev)


async def test_publish_gate_rejects_source_with_unknown_class(clean_spec):
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    ghost = OntologyClass(name="Ghost", slots=[id_slot])
    src = Source(name="src")
    binding = SourceBinding(source=src, class_=ghost, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec = Spec(
        id="bad",
        version="1.0.0",
        classes=[movie],  # ghost not here
        sources=[src],
        source_bindings=[binding],
    )
    rev = await create_draft(clean_spec)
    await update_draft(clean_spec, rev, spec)
    with pytest.raises(PublishGateError):
        await publish_draft(clean_spec, rev)


# ---------------------------------------------------------------------------
# 5. Partial unique index: at-most-one published row
# ---------------------------------------------------------------------------


async def test_only_one_published_revision_at_a_time(clean_spec):
    # Publish rev1, then publish rev2 — rev1 must be demoted
    rev1 = await create_draft(clean_spec)
    await update_draft(clean_spec, rev1, _minimal_spec("v1"))
    await publish_draft(clean_spec, rev1)

    rev2 = await create_draft(clean_spec)
    await update_draft(clean_spec, rev2, _minimal_spec("v2"))
    await publish_draft(clean_spec, rev2)

    assert await get_published_revision(clean_spec) == rev2

    # Directly check no two rows have published=TRUE
    count = (
        await (
            await clean_spec.execute("SELECT count(*) FROM spec_revisions WHERE published = TRUE")
        ).fetchone()
    )[0]
    assert count == 1


# ---------------------------------------------------------------------------
# 6. Missing revision raises DraftNotFoundError
# ---------------------------------------------------------------------------


async def test_get_revision_raises_for_unknown(clean_spec):
    with pytest.raises(DraftNotFoundError):
        await get_revision(clean_spec, 999999)


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


async def test_publish_refuses_destructive_without_flag(clean_spec):
    """Dropping a class between revisions is destructive — gate must block."""
    # Publish v1 with Movie class
    rev1 = await create_draft(clean_spec)
    await update_draft(clean_spec, rev1, _minimal_spec("v1"))
    await publish_draft(clean_spec, rev1)

    # v2: empty spec (Movie class dropped → DropClass change)
    rev2 = await create_draft(clean_spec)
    empty_spec = Spec(id="v2", version="1.0.0")
    await update_draft(clean_spec, rev2, empty_spec)
    with pytest.raises(PublishGateError, match="destructive"):
        await publish_draft(clean_spec, rev2)


async def test_publish_accepts_destructive_with_flag(clean_spec):
    rev1 = await create_draft(clean_spec)
    await update_draft(clean_spec, rev1, _minimal_spec("v1"))
    await publish_draft(clean_spec, rev1)

    rev2 = await create_draft(clean_spec)
    empty_spec = Spec(id="v2", version="1.0.0")
    await update_draft(clean_spec, rev2, empty_spec)
    result = await publish_draft(clean_spec, rev2, allow_destructive=True)
    assert result == rev2
    assert await get_published_revision(clean_spec) == rev2
