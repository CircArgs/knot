"""Pre-flight gate integration tests.

Each test drives the publish flow against real postgres so the gate's
savepoint-based cast probe and identifier-slot integrity checks are
exercised against actual data.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import OntologyClass, Slot, Source, Spec
from knot.spec.metaschema import Array, Primitive
from knot.spec.compile.postgres._naming import schema
from knot.spec.compile.postgres.migration import apply_changes, diff_specs
from knot.spec.errors import PublishGateError


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


# ---------------------------------------------------------------------------
# 2a. ChangeSlotTypeExpression cast feasibility
# ---------------------------------------------------------------------------


async def test_preflight_type_cast_failure_blocks_publish(clean_db):
    """A TEXT→INTEGER cast fails when existing rows contain non-numeric values.
    publish_draft must raise PublishGateError with kind=type_cast_failure."""

    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_str = Slot(name="year", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[id_slot, year_str], classes=[movie], sources=[src]
    )

    # Publish v1 first so the table exists with data.
    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert a row whose 'year' is not a valid integer.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, year) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'not-a-number')",
        (rev1,),
    )

    # v2 changes year: string → integer (TEXT → INTEGER).
    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_int = Slot(name="year", type=Primitive(name="integer"))
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, year_int])
    src2 = Source(name="imdb", entity_class=movie2, identifier_slot=id_slot2)
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[id_slot2, year_int], classes=[movie2], sources=[src2]
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    with pytest.raises(PublishGateError, match="type_cast_failure"):
        await publish_draft(clean_db, rev2, allow_destructive=True)


async def test_preflight_type_cast_succeeds_with_compatible_data(clean_db):
    """TEXT→INTEGER cast succeeds when all existing rows contain valid integers."""

    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_str = Slot(name="year", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[id_slot, year_str], classes=[movie], sources=[src]
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert rows with valid integer strings.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, year) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', '2010'), ('imdb', 'tt2', %s, 'tt2', '2020')",
        (rev1, rev1),
    )

    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_int = Slot(name="year", type=Primitive(name="integer"))
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, year_int])
    src2 = Source(name="imdb", entity_class=movie2, identifier_slot=id_slot2)
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[id_slot2, year_int], classes=[movie2], sources=[src2]
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)
    # Must not raise.
    await publish_draft(clean_db, rev2, allow_destructive=True)


# ---------------------------------------------------------------------------
# 2b. ChangeSourceIdentifierSlot — NULL check
# ---------------------------------------------------------------------------


async def test_preflight_identifier_nulls_blocks_publish(clean_db):
    """ChangeSourceIdentifierSlot: new slot has NULL rows → PublishGateError
    with kind=identifier_nulls."""

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[imdb_id, title], classes=[movie], sources=[src]
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert a row where 'title' is NULL — would be invalid as identifier slot.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', NULL)",
        (rev1,),
    )

    # v2 switches identifier slot to 'title'.
    imdb_id2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title2 = Slot(name="title", type=Primitive(name="string"), identifier=True)
    movie2 = OntologyClass(name="Movie", slots=[imdb_id2, title2])
    src2 = Source(name="imdb", entity_class=movie2, identifier_slot=title2)
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[imdb_id2, title2], classes=[movie2], sources=[src2]
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    with pytest.raises(PublishGateError, match="identifier_nulls"):
        await publish_draft(clean_db, rev2, allow_destructive=True)


# ---------------------------------------------------------------------------
# 2b. ChangeSourceIdentifierSlot — duplicate check
# ---------------------------------------------------------------------------


async def test_preflight_identifier_duplicates_blocks_publish(clean_db):
    """ChangeSourceIdentifierSlot: new slot has duplicate values per source
    → PublishGateError with kind=identifier_duplicates."""

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec_v1 = Spec(
        id="t", version="1.0.0", slots=[imdb_id, title], classes=[movie], sources=[src]
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Two rows with the same 'title' — would collide as identifier slot.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'Inception'), "
        "       ('imdb', 'tt2', %s, 'tt2', 'Inception')",
        (rev1, rev1),
    )

    imdb_id2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title2 = Slot(name="title", type=Primitive(name="string"), identifier=True)
    movie2 = OntologyClass(name="Movie", slots=[imdb_id2, title2])
    src2 = Source(name="imdb", entity_class=movie2, identifier_slot=title2)
    spec_v2 = Spec(
        id="t", version="1.0.0", slots=[imdb_id2, title2], classes=[movie2], sources=[src2]
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    with pytest.raises(PublishGateError, match="identifier_duplicates"):
        await publish_draft(clean_db, rev2, allow_destructive=True)
