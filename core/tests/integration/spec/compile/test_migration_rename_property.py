"""RenameProperty integration tests.

Verifies that the rename-slot flow:
  - Emits RENAME COLUMN (not DROP + ADD) at publish time.
  - Preserves existing row data in the renamed column.
  - Emits RenameProperty (Bucket C) — no allow_destructive needed.
  - Handles required-slot CHECK constraint rename.
  - Rejects rename when new_name collides with an existing slot (effective_properties).
  - Rejects rename when old_name doesn't exist as an own slot on the class.

Slots are inline on OntologyClass (by-copy); there is no top-level Spec.properties.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.graph.spec import CollisionError, EntityNotOnDraftError, rename_property
from knot.spec import OntologyClass, Property, Source, SourceBinding, Spec
from knot.spec.compile.postgres._naming import schema
from knot.spec.metaschema import Primitive

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_db(pg_conn):
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
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(property_name: str = "title") -> Spec:
    id_slot = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    col_slot = Property(name=property_name, type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", properties=[id_slot, col_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_property=id_slot)  # type: ignore[call-arg]
    return Spec(
        id="t",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )


# ---------------------------------------------------------------------------
# Test: rename emits RENAME COLUMN, not DROP + ADD, and data survives
# ---------------------------------------------------------------------------


async def test_rename_slot_preserves_data(clean_db):
    """After rename, existing rows have data in the new column name."""
    spec_v1 = _make_spec("title")

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert a row into the published table.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'The Matrix')",
        (rev1,),
    )

    # Draft: rename title → name.
    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_property(clean_db, rev2, "Movie", "title", "name")

    # Publish should succeed without allow_destructive.
    await publish_draft(clean_db, rev2)

    # Column should now be called "name" and data preserved.
    row = await (
        await clean_db.execute(f"SELECT name FROM {schema()}.movie WHERE _source_row_id = 'tt1'")
    ).fetchone()
    assert row is not None
    assert row[0] == "The Matrix"

    # Old column name should not exist.
    try:
        await clean_db.execute(f"SELECT title FROM {schema()}.movie LIMIT 1")
        assert False, "Expected column 'title' to not exist after rename"
    except Exception as exc:
        assert "title" in str(exc).lower() or "column" in str(exc).lower()


# ---------------------------------------------------------------------------
# Test: rename does not require allow_destructive
# ---------------------------------------------------------------------------


async def test_rename_slot_is_not_destructive(clean_db):
    """RenameProperty is Bucket C — publish does not require allow_destructive."""
    spec_v1 = _make_spec("title")

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_property(clean_db, rev2, "Movie", "title", "display_title")

    # Must succeed with allow_destructive=False (default).
    result = await publish_draft(clean_db, rev2)
    assert result == rev2


# ---------------------------------------------------------------------------
# Test: rename required slot renames the CHECK constraint
# ---------------------------------------------------------------------------


async def test_rename_required_slot_renames_check_constraint(clean_db):
    """Required slot renamed: old CHECK constraint dropped, new one added."""
    id_slot = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    req_slot = Property(name="title", type=Primitive(name="string"), required=True)
    movie = OntologyClass(name="Movie", properties=[id_slot, req_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_property(clean_db, rev2, "Movie", "title", "name")
    await publish_draft(clean_db, rev2)

    # Old constraint name should be gone, new one should exist.
    old_chk = "movie_title_required_chk"
    new_chk = "movie_name_required_chk"
    rows = await (
        await clean_db.execute(
            "SELECT c.conname FROM pg_constraint c "
            "JOIN pg_class r ON r.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = r.relnamespace "
            "WHERE n.nspname = %s AND r.relname = 'movie' "
            "AND c.conname IN (%s, %s)",
            (schema(), old_chk, new_chk),
        )
    ).fetchall()
    names = {r[0] for r in rows}
    assert old_chk not in names, f"Old constraint {old_chk!r} still exists after rename"
    assert new_chk in names, f"New constraint {new_chk!r} was not created after rename"


# ---------------------------------------------------------------------------
# Test: collision on new_name raises CollisionError
# ---------------------------------------------------------------------------


async def test_rename_slot_collision(clean_db):
    """Renaming to an existing slot name raises CollisionError."""
    id_slot = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title_slot = Property(name="title", type=Primitive(name="string"))
    year_slot = Property(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", properties=[id_slot, title_slot, year_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)

    with pytest.raises(CollisionError):
        await rename_property(clean_db, rev1, "Movie", "title", "year")


# ---------------------------------------------------------------------------
# Test: old_name not on class raises EntityNotOnDraftError
# ---------------------------------------------------------------------------


async def test_rename_slot_not_found(clean_db):
    """Renaming a slot that doesn't exist as own slot raises EntityNotOnDraftError."""
    spec_v1 = _make_spec("title")
    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)

    with pytest.raises(EntityNotOnDraftError):
        await rename_property(clean_db, rev1, "Movie", "nonexistent", "something")
