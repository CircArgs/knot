"""Migration apply_changes + publish-gate tests (DB-bound).

Pure-Python diff_specs + is_destructive unit tests for AddClass, AddSlot,
DropClass, DropSlot, ChangeSlotType live in
``tests/unit/spec/compile/test_migration_diff.py``.

Each test here starts from a clean slate via the ``clean_db`` fixture
and applies DDL against the running postgres docker stack.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.compile.postgres._naming import schema
from knot.spec.compile.postgres.migration import (
    apply_changes,
    diff_specs,
)
from knot.spec.errors import PublishGateError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _minimal_spec(*extra_classes: OntologyClass) -> Spec:
    st = _str_type()
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    src = Source(name="imdb_src", entity_class=movie, identifier_slot=id_slot)
    all_classes = [movie, *extra_classes]
    return Spec(
        id="test",
        version="1.0.0",
        types=[st],
        slots=[id_slot],
        classes=all_classes,
        sources=[src],
    )


async def _table_exists(conn, table_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema(), table_name),
        )
    ).fetchone()
    return row is not None


async def _column_exists(conn, table_name: str, column_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            (schema(), table_name, column_name),
        )
    ).fetchone()
    return row is not None


async def _index_exists(conn, index_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM pg_indexes WHERE indexname = %s",
            (index_name,),
        )
    ).fetchone()
    return row is not None


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_db(pg_conn):
    """Full reset: drop knot_data + truncate all control tables."""
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
# apply_changes — DDL idempotency + table/index creation
#
# diff_specs + is_destructive unit tests live in
# tests/unit/spec/compile/test_migration_diff.py.
# ---------------------------------------------------------------------------


async def test_apply_changes_creates_source_table(clean_db):
    spec = _minimal_spec()
    changes = diff_specs(None, spec)
    await apply_changes(clean_db, changes)
    assert await _table_exists(clean_db, "movie")


async def test_apply_changes_creates_bindings_table(clean_db):
    spec = _minimal_spec()
    changes = diff_specs(None, spec)
    await apply_changes(clean_db, changes)
    assert await _table_exists(clean_db, "movie_bindings")


async def test_apply_changes_creates_partial_unique_index_on_bindings(clean_db):
    spec = _minimal_spec()
    changes = diff_specs(None, spec)
    await apply_changes(clean_db, changes)
    # The partial UNIQUE index enforcing one current binding per knot_row_id
    assert await _index_exists(clean_db, "movie_bindings_one_current_per_row")


async def test_apply_changes_is_idempotent(clean_db):
    spec = _minimal_spec()
    changes = diff_specs(None, spec)
    await apply_changes(clean_db, changes)
    await apply_changes(clean_db, changes)  # second run should not raise
    assert await _table_exists(clean_db, "movie")


async def test_apply_changes_add_slot_creates_column(clean_db):
    st = _str_type()
    id_slot = Slot(name="imdb_id", range=st, identifier=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    v1 = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[movie])
    await apply_changes(clean_db, diff_specs(None, v1))

    title = Slot(name="title", range=st)
    movie_v2 = OntologyClass(name="Movie", slots=[id_slot, title])
    v2 = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[movie_v2])
    await apply_changes(clean_db, diff_specs(v1, v2))
    assert await _column_exists(clean_db, "movie", "title")


# ---------------------------------------------------------------------------
# 4. Publish pipeline: destructive gating via spec_store
# ---------------------------------------------------------------------------


async def test_publish_blocks_destructive_slot_drop_without_flag(clean_db):
    st = _str_type()
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(name="Movie", slots=[id_slot, title])
    src = Source(name="s", entity_class=movie, identifier_slot=id_slot)
    spec_v1 = Spec(
        id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[movie], sources=[src]
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # v2 drops "title" → DropSlot (destructive).
    # Source must reference the new class object so the publish gate passes ref checks.
    id_slot2 = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v2 = OntologyClass(name="Movie", slots=[id_slot2])
    src_v2 = Source(name="s", entity_class=movie_v2, identifier_slot=id_slot2)
    spec_v2 = Spec(
        id="t", version="1.0.0", types=[st], slots=[id_slot2], classes=[movie_v2], sources=[src_v2]
    )
    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)
    with pytest.raises(PublishGateError, match="destructive"):
        await publish_draft(clean_db, rev2)


async def test_publish_allows_destructive_slot_drop_with_flag(clean_db):
    st = _str_type()
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(name="Movie", slots=[id_slot, title])
    src = Source(name="s", entity_class=movie, identifier_slot=id_slot)
    spec_v1 = Spec(
        id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[movie], sources=[src]
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    id_slot2 = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v2 = OntologyClass(name="Movie", slots=[id_slot2])
    src_v2 = Source(name="s", entity_class=movie_v2, identifier_slot=id_slot2)
    spec_v2 = Spec(
        id="t", version="1.0.0", types=[st], slots=[id_slot2], classes=[movie_v2], sources=[src_v2]
    )
    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)
    await publish_draft(clean_db, rev2, allow_destructive=True)  # must not raise
    assert not await _column_exists(clean_db, "movie", "title")
