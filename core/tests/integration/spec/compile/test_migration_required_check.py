"""Integration tests for required=True postgres CHECK constraints (Item 4).

Covers:
- AddClass with required slot emits CHECK; inserting NULL for real source fails;
  inserting NULL for _user_corrections succeeds.
- AddSlot with required=True emits CHECK.
- ChangeSlotRequired false→true with NULLs → required_violation blocker.
- ChangeSlotRequired false→true with all rows populated → succeeds, CHECK added.
- ChangeSlotRequired true→false → CHECK dropped.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import OntologyClass, Slot, Source, SourceBinding, Spec
from knot.spec.compile.postgres._naming import schema, user_corrections_source
from knot.spec.compile.postgres.migration import apply_changes, diff_specs
from knot.spec.errors import PublishGateError
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


async def _ensure_revision(conn) -> int:
    row = await (
        await conn.execute(
            """
            INSERT INTO spec_revisions (spec, content_hash, published, created_at)
            VALUES ('{}'::jsonb, %s, FALSE, now())
            RETURNING revision
            """,
            ("0" * 64,),
        )
    ).fetchone()
    return row[0]


async def _check_exists(conn, table_name: str, check_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM pg_constraint c "
            "JOIN pg_class r ON r.oid = c.conrelid "
            "JOIN pg_namespace n ON n.oid = r.relnamespace "
            "WHERE c.conname = %s AND n.nspname = %s AND r.relname = %s",
            (check_name, schema(), table_name),
        )
    ).fetchone()
    return row is not None


def _simple_spec(id_slot, movie) -> Spec:
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    return Spec(
        id="t",
        version="1.0.0",
        slots=[id_slot],
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )


# ---------------------------------------------------------------------------
# AddClass — required slot emits CHECK
# ---------------------------------------------------------------------------


async def test_add_class_with_required_slot_emits_check(clean_db):
    """AddClass: table creation emits CHECK constraint for required slot."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    spec = _simple_spec(id_slot, movie)

    await apply_changes(clean_db, diff_specs(None, spec))

    assert await _check_exists(clean_db, "movie", "movie_imdb_id_required_chk")


async def test_add_class_required_slot_blocks_null_for_real_source(clean_db):
    """Inserting NULL for a required slot from a real source violates the CHECK."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    spec = _simple_spec(id_slot, movie)
    await apply_changes(clean_db, diff_specs(None, spec))
    rev = await _ensure_revision(clean_db)

    import psycopg

    with pytest.raises(psycopg.Error, match="movie_imdb_id_required_chk"):
        await clean_db.execute(
            f"INSERT INTO {schema()}.movie "
            "(_source, _source_row_id, _spec_revision, imdb_id) "
            "VALUES ('imdb', 'tt1', %s, NULL)",
            (rev,),
        )


async def test_add_class_required_slot_allows_null_for_user_corrections(clean_db):
    """Inserting NULL for a required slot from _user_corrections is allowed
    (user-corrections rows are partial by design — exempted by the CHECK)."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    spec = _simple_spec(id_slot, movie)
    await apply_changes(clean_db, diff_specs(None, spec))
    rev = await _ensure_revision(clean_db)

    uc = user_corrections_source()
    # Must not raise.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id) "
        "VALUES (%s, 'corr1', %s, NULL)",
        (uc, rev),
    )


# ---------------------------------------------------------------------------
# AddSlot required=True — emits CHECK
# ---------------------------------------------------------------------------


async def test_add_slot_required_emits_check(clean_db):
    """AddSlot with required=True: ADD COLUMN followed by CHECK constraint."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v1 = OntologyClass(name="Movie", slots=[id_slot])
    spec_v1 = _simple_spec(id_slot, movie_v1)
    await apply_changes(clean_db, diff_specs(None, spec_v1))

    # v2 adds a required slot.
    title = Slot(name="title", type=Primitive(name="string"), required=True)
    movie_v2 = OntologyClass(name="Movie", slots=[id_slot, title])
    src_v2 = Source(name="imdb")
    binding_v2 = SourceBinding(source=src_v2, class_=movie_v2, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[id_slot, title], classes=[movie_v2],
        sources=[src_v2], source_bindings=[binding_v2],
    )
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))

    assert await _check_exists(clean_db, "movie", "movie_title_required_chk")


# ---------------------------------------------------------------------------
# ChangeSlotRequired false→true — preflight blocks when NULLs exist
# ---------------------------------------------------------------------------


async def test_change_slot_required_false_to_true_with_nulls_blocked(clean_db):
    """ChangeSlotRequired false→true: preflight raises PublishGateError when
    existing non-correction rows have NULLs in the slot."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"), required=False)
    movie = OntologyClass(name="Movie", slots=[id_slot, title])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[id_slot, title], classes=[movie],
        sources=[src], source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert a row with title=NULL (valid when not required).
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', NULL)",
        (rev1,),
    )

    # v2 flips title to required=True.
    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title2 = Slot(name="title", type=Primitive(name="string"), required=True)
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, title2])
    src2 = Source(name="imdb")
    binding2 = SourceBinding(source=src2, class_=movie2, identifier_slot=id_slot2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[id_slot2, title2], classes=[movie2],
        sources=[src2], source_bindings=[binding2],
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    with pytest.raises(PublishGateError, match="required_violation"):
        await publish_draft(clean_db, rev2)


# ---------------------------------------------------------------------------
# ChangeSlotRequired false→true — succeeds when all rows populated
# ---------------------------------------------------------------------------


async def test_change_slot_required_false_to_true_with_all_rows_ok(clean_db):
    """ChangeSlotRequired false→true: succeeds when no NULL rows exist for
    real sources; CHECK constraint is added to the table."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"), required=False)
    movie = OntologyClass(name="Movie", slots=[id_slot, title])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[id_slot, title], classes=[movie],
        sources=[src], source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert rows with non-NULL title.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'Inception')",
        (rev1,),
    )

    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title2 = Slot(name="title", type=Primitive(name="string"), required=True)
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, title2])
    src2 = Source(name="imdb")
    binding2 = SourceBinding(source=src2, class_=movie2, identifier_slot=id_slot2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[id_slot2, title2], classes=[movie2],
        sources=[src2], source_bindings=[binding2],
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)
    # Must not raise.
    await publish_draft(clean_db, rev2)

    assert await _check_exists(clean_db, "movie", "movie_title_required_chk")


# ---------------------------------------------------------------------------
# ChangeSlotRequired true→false — CHECK dropped
# ---------------------------------------------------------------------------


async def test_change_slot_required_true_to_false_drops_check(clean_db):
    """ChangeSlotRequired true→false: CHECK constraint is dropped."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"), required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot, title])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[id_slot, title], classes=[movie],
        sources=[src], source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # CHECK should exist after publishing v1 (title is required=True).
    assert await _check_exists(clean_db, "movie", "movie_title_required_chk")

    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title2 = Slot(name="title", type=Primitive(name="string"), required=False)
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, title2])
    src2 = Source(name="imdb")
    binding2 = SourceBinding(source=src2, class_=movie2, identifier_slot=id_slot2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[id_slot2, title2], classes=[movie2],
        sources=[src2], source_bindings=[binding2],
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)
    await publish_draft(clean_db, rev2)

    # CHECK should be gone.
    assert not await _check_exists(clean_db, "movie", "movie_title_required_chk")
