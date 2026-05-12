"""RenameClass integration tests.

Verifies that the rename-class flow:
  - Emits ALTER TABLE … RENAME TO for source + bindings table (Bucket C).
  - Renames the two partial indexes on the bindings table.
  - Renames required-slot CHECK constraints that embed the class name.
  - Preserves existing row data.
  - Handles class rename + slot rename on the same draft in one publish.
  - SourceBinding.class_ still points at the renamed class post-rename.
  - Does not require allow_destructive (Bucket C).
  - Rejects rename when new_name collides with an existing class.
  - Rejects rename when old_name isn't on the draft.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.graph.spec import (
    CollisionError,
    EntityNotOnDraftError,
    rename_class,
    rename_property,
)
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


def _make_movie_spec(*, required_title: bool = False) -> Spec:
    id_slot = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title_slot = Property(name="title", type=Primitive(name="string"), required=required_title)
    movie = OntologyClass(name="Movie", properties=[id_slot, title_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_property=id_slot)  # type: ignore[call-arg]
    return Spec(
        id="t",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )


async def _table_exists(conn, table_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s",
            (schema(), table_name),
        )
    ).fetchone()
    return row is not None


async def _index_exists(conn, index_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
            (schema(), index_name),
        )
    ).fetchone()
    return row is not None


async def _constraint_exists(conn, table_name: str, constraint_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM pg_constraint c "
            "JOIN pg_class r ON r.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = r.relnamespace "
            "WHERE n.nspname = %s AND r.relname = %s AND c.conname = %s",
            (schema(), table_name, constraint_name),
        )
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Test: basic rename — table, bindings table, and indexes are renamed
# ---------------------------------------------------------------------------


async def test_rename_class_renames_tables_and_indexes(clean_db):
    """ALTER TABLE RENAME renames source table, bindings table, and both indexes."""
    spec_v1 = _make_movie_spec()

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    assert await _table_exists(clean_db, "movie")
    assert await _table_exists(clean_db, "movie_bindings")
    assert await _index_exists(clean_db, "movie_bindings_current")
    assert await _index_exists(clean_db, "movie_bindings_one_current_per_row")

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_class(clean_db, rev2, "Movie", "Film")
    await publish_draft(clean_db, rev2)

    # Old names gone.
    assert not await _table_exists(clean_db, "movie"), "Old source table should be gone"
    assert not await _table_exists(clean_db, "movie_bindings"), "Old bindings table should be gone"
    assert not await _index_exists(clean_db, "movie_bindings_current")
    assert not await _index_exists(clean_db, "movie_bindings_one_current_per_row")

    # New names present.
    assert await _table_exists(clean_db, "film"), "New source table not found"
    assert await _table_exists(clean_db, "film_bindings"), "New bindings table not found"
    assert await _index_exists(clean_db, "film_bindings_current")
    assert await _index_exists(clean_db, "film_bindings_one_current_per_row")


# ---------------------------------------------------------------------------
# Test: class rename is Bucket C — no allow_destructive needed
# ---------------------------------------------------------------------------


async def test_rename_class_is_not_destructive(clean_db):
    """RenameClass is Bucket C — publish succeeds without allow_destructive."""
    spec_v1 = _make_movie_spec()

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_class(clean_db, rev2, "Movie", "Film")

    result = await publish_draft(clean_db, rev2)
    assert result == rev2


# ---------------------------------------------------------------------------
# Test: class rename preserves existing row data
# ---------------------------------------------------------------------------


async def test_rename_class_preserves_data(clean_db):
    """After rename, existing rows are accessible under the new table name."""
    spec_v1 = _make_movie_spec()

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'The Matrix')",
        (rev1,),
    )

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_class(clean_db, rev2, "Movie", "Film")
    await publish_draft(clean_db, rev2)

    row = await (
        await clean_db.execute(f"SELECT title FROM {schema()}.film WHERE _source_row_id = 'tt1'")
    ).fetchone()
    assert row is not None
    assert row[0] == "The Matrix"


# ---------------------------------------------------------------------------
# Test: required-slot CHECK constraints are renamed with the class
# ---------------------------------------------------------------------------


async def test_rename_class_renames_required_check_constraints(clean_db):
    """Required CHECK constraint names are updated when the class is renamed."""
    spec_v1 = _make_movie_spec(required_title=True)

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Verify old constraint exists.
    assert await _constraint_exists(clean_db, "movie", "movie_title_required_chk"), (
        "Old constraint should exist before rename"
    )

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    await rename_class(clean_db, rev2, "Movie", "Film")
    await publish_draft(clean_db, rev2)

    assert not await _constraint_exists(clean_db, "film", "movie_title_required_chk"), (
        "Old constraint name should be gone after rename"
    )
    assert await _constraint_exists(clean_db, "film", "film_title_required_chk"), (
        "New constraint name should exist after rename"
    )


# ---------------------------------------------------------------------------
# Test: class rename + slot rename on the same draft publish together
# ---------------------------------------------------------------------------


async def test_rename_class_and_slot_together(clean_db):
    """Class rename + slot rename on same draft publishes as two non-destructive changes."""
    spec_v1 = _make_movie_spec()

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'Inception')",
        (rev1,),
    )

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    # Rename class first (slot_renames hint uses new class name).
    await rename_class(clean_db, rev2, "Movie", "Film")
    # Rename slot on the renamed class.
    await rename_property(clean_db, rev2, "Film", "title", "name")

    # Both are Bucket C — no allow_destructive needed.
    await publish_draft(clean_db, rev2)

    row = await (
        await clean_db.execute(f"SELECT name FROM {schema()}.film WHERE _source_row_id = 'tt1'")
    ).fetchone()
    assert row is not None
    assert row[0] == "Inception"


# ---------------------------------------------------------------------------
# Test: SourceBinding.class_ references the renamed class
# ---------------------------------------------------------------------------


async def test_rename_class_source_binding_follows_rename(clean_db):
    """After rename_class(), the draft spec's SourceBinding still has class_ = Film."""

    spec_v1 = _make_movie_spec()

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    rev2 = await create_draft(clean_db, parent_revision=rev1)
    spec_after = await rename_class(clean_db, rev2, "Movie", "Film")

    binding_class_names = {b.class_.name for b in spec_after.source_bindings}
    assert "Film" in binding_class_names, "SourceBinding should reference the renamed class 'Film'"
    assert "Movie" not in binding_class_names, "Old class name 'Movie' should not appear"


# ---------------------------------------------------------------------------
# Test: collision raises CollisionError
# ---------------------------------------------------------------------------


async def test_rename_class_collision(clean_db):
    """Renaming to an existing class name raises CollisionError."""
    id_slot1 = Property(name="id1", type=Primitive(name="string"), identifier=True, required=True)
    id_slot2 = Property(name="id2", type=Primitive(name="string"), identifier=True, required=True)
    cls_a = OntologyClass(name="Alpha", properties=[id_slot1])
    cls_b = OntologyClass(name="Beta", properties=[id_slot2])
    src = Source(name="src")
    b1 = SourceBinding(source=src, class_=cls_a, identifier_property=id_slot1)  # type: ignore[call-arg]
    b2 = SourceBinding(source=src, class_=cls_b, identifier_property=id_slot2)  # type: ignore[call-arg]
    spec = Spec(
        id="t",
        version="1.0.0",
        classes=[cls_a, cls_b],
        sources=[src],
        source_bindings=[b1, b2],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec)

    with pytest.raises(CollisionError):
        await rename_class(clean_db, rev1, "Alpha", "Beta")


# ---------------------------------------------------------------------------
# Test: old_name not found raises EntityNotOnDraftError
# ---------------------------------------------------------------------------


async def test_rename_class_not_found(clean_db):
    """Renaming a class that doesn't exist raises EntityNotOnDraftError."""
    spec_v1 = _make_movie_spec()
    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)

    with pytest.raises(EntityNotOnDraftError):
        await rename_class(clean_db, rev1, "Nonexistent", "Something")
