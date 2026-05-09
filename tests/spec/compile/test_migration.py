"""Migration tests — diff_specs, emit_ddl, apply_changes, is_destructive.

Each test starts from a clean slate via the `clean_db` fixture.
DDL changes are applied against the running postgres docker stack.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db._naming import schema
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.compile.postgres.migration import (
    AddClass,
    AddSlot,
    ChangeSlotType,
    DropClass,
    DropSlot,
    apply_changes,
    diff_specs,
    is_destructive,
)
from knot.spec.errors import PublishGateError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _str_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _int_type() -> TypeDefinition:
    return TypeDefinition(name="integer", base="int")


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
# 1. diff_specs — change-event generation
# ---------------------------------------------------------------------------


def test_diff_from_none_produces_add_class_for_each_concrete_class():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    series = OntologyClass(name="Series", slots=[id_slot])
    spec = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[movie, series])
    changes = diff_specs(None, spec)
    types_ = {type(c).__name__ for c in changes}
    assert "AddClass" in types_
    add_names = {c.cls.name for c in changes if isinstance(c, AddClass)}
    assert add_names == {"Movie", "Series"}


def test_diff_from_none_skips_abstract_classes():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    abstract = OntologyClass(name="Base", slots=[id_slot], abstract=True)
    concrete = OntologyClass(name="Movie", slots=[id_slot])
    spec = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[abstract, concrete])
    changes = diff_specs(None, spec)
    add_names = {c.cls.name for c in changes if isinstance(c, AddClass)}
    assert "Base" not in add_names
    assert "Movie" in add_names


def test_diff_add_slot_detected():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    title = Slot(name="title", range=st)
    prev_movie = OntologyClass(name="Movie", slots=[id_slot])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, title])
    prev = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    add_slots = [c for c in changes if isinstance(c, AddSlot)]
    assert len(add_slots) == 1
    assert add_slots[0].slot.name == "title"


def test_diff_drop_slot_detected():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    title = Slot(name="title", range=st)
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, title])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot])
    prev = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot, title], classes=[prev_movie])
    cand = Spec(id="t", version="1.0.0", types=[st], slots=[id_slot], classes=[cand_movie])
    changes = diff_specs(prev, cand)
    drop_slots = [c for c in changes if isinstance(c, DropSlot)]
    assert any(c.slot_name == "title" for c in drop_slots)


def test_diff_change_slot_type_detected():
    str_t = _str_type()
    int_t = _int_type()
    id_slot = Slot(name="id", range=str_t, identifier=True)
    year_str = Slot(name="year", range=str_t)
    year_int = Slot(name="year", range=int_t)
    prev_movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    cand_movie = OntologyClass(name="Movie", slots=[id_slot, year_int])
    prev = Spec(
        id="t", version="1.0.0", types=[str_t], slots=[id_slot, year_str], classes=[prev_movie]
    )
    cand = Spec(
        id="t",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[id_slot, year_int],
        classes=[cand_movie],
    )
    changes = diff_specs(prev, cand)
    type_changes = [c for c in changes if isinstance(c, ChangeSlotType)]
    assert any(c.slot.name == "year" for c in type_changes)


# ---------------------------------------------------------------------------
# 2. is_destructive classification
# ---------------------------------------------------------------------------


def test_add_class_is_not_destructive():
    st = _str_type()
    id_slot = Slot(name="id", range=st, identifier=True)
    cls = OntologyClass(name="Movie", slots=[id_slot])
    assert not is_destructive(AddClass(cls=cls))


def test_add_slot_is_not_destructive():
    st = _str_type()
    cls = OntologyClass(name="Movie", slots=[])
    slot = Slot(name="title", range=st)
    assert not is_destructive(AddSlot(cls=cls, slot=slot))


def test_drop_class_is_destructive():
    assert is_destructive(DropClass(class_name="Movie"))


def test_drop_slot_is_destructive():
    cls = OntologyClass(name="Movie", slots=[])
    assert is_destructive(DropSlot(cls=cls, slot_name="title"))


def test_change_slot_type_is_destructive():
    int_t = _int_type()
    cls = OntologyClass(name="Movie", slots=[])
    slot = Slot(name="year", range=int_t)
    assert is_destructive(
        ChangeSlotType(cls=cls, slot=slot, prev_pg_type="TEXT", new_pg_type="BIGINT")
    )


# ---------------------------------------------------------------------------
# 3. apply_changes — DDL idempotency + table/index creation
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
