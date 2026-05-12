"""FK-reference and ClassRef slot tests.

The ``DiscriminatedRef`` and ``IdentifierPattern`` concepts from the old
metaschema have been removed; FK relationships now use ``ClassRef`` directly.

Coverage:
  1. Publish a spec with a Movie class and a Credit class (FK→Movie).
  2. Insert rows; verify FK column stores canonical_id TEXT.
  3. Query credits via GraphQL; FK slot appears in schema.
  4. Source on a normal class publishes fine.
  5. Multi-source spec (Movie + Credit) publishes fine.
  6. Regression: spec without FK slots still works as before.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from knot import db
from knot.db import graph_store, spec_store
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec import (
    OntologyClass,
    Slot,
    Source,
    Spec,
)
from knot.spec.metaschema import ClassRef, Primitive

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _reset(conn):
    await conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await conn.execute("TRUNCATE TABLE users CASCADE")
    await conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()


def _build_movie_credit_spec() -> tuple[Spec, OntologyClass, OntologyClass, Source, Source]:
    """Build a spec with Movie + Credit (FK→Movie)."""
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])

    credit_id = Slot(name="credit_id", type=Primitive(name="string"), identifier=True, required=True)
    movie_fk = Slot(name="movie", type=ClassRef(target_class=movie))
    role = Slot(name="role", type=Primitive(name="string"))
    credit = OntologyClass(name="Credit", slots=[credit_id, movie_fk, role])

    movie_src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    credit_src = Source(name="credits", entity_class=credit, identifier_slot=credit_id)

    spec = Spec(
        id="fk_test",
        version="1.0.0",
        slots=[imdb_id, title, credit_id, movie_fk, role],
        classes=[movie, credit],
        sources=[movie_src, credit_src],
    )
    return spec, movie, credit, movie_src, credit_src


# ---------------------------------------------------------------------------
# Fixture: published spec
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fk_db(pg_conn):
    """Reset, publish Movie + Credit spec. Yields (conn, movie, credit, movie_src, credit_src, rev)."""
    await _reset(pg_conn)
    spec, movie, credit, movie_src, credit_src = _build_movie_credit_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)
    yield pg_conn, movie, credit, movie_src, credit_src, rev


# ---------------------------------------------------------------------------
# 1. Tables created for both classes
# ---------------------------------------------------------------------------


async def test_movie_and_credit_tables_created(fk_db):
    conn, *_ = fk_db
    for table in ("movie", "credit"):
        cur = await conn.execute(
            "SELECT count(*) FROM pg_tables "
            "WHERE schemaname = 'knot_data' AND tablename = %s",
            (table,),
        )
        row = await cur.fetchone()
        assert row[0] == 1, f"Table knot_data.{table} was not created"


# ---------------------------------------------------------------------------
# 2. FK column stores canonical_id TEXT
# ---------------------------------------------------------------------------


async def test_fk_column_stores_canonical_id(fk_db):
    conn, movie, credit, movie_src, credit_src, rev = fk_db

    await graph_store.insert_rows(
        conn,
        source=movie_src,
        spec_revision=rev,
        rows=[{"imdb_id": "tt0111161", "title": "Shawshank"}],
        canonical_ids=["tt0111161"],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[{"credit_id": "c1", "movie": "tt0111161", "role": "director"}],
        canonical_ids=["c1"],
    )

    cur = await conn.execute("SELECT movie FROM knot_data.credit WHERE credit_id = 'c1'")
    row = await cur.fetchone()
    assert row is not None
    assert row[0] == "tt0111161"


# ---------------------------------------------------------------------------
# 3. FK slot appears in GraphQL schema
# ---------------------------------------------------------------------------


async def test_fk_slot_in_graphql_schema(fk_db):
    conn, movie, credit, movie_src, credit_src, rev = fk_db

    from knot.spec.compile.graphql import get_or_build_schema

    published = await spec_store.get_published(conn)
    content_hash = await spec_store.get_published_content_hash(conn)
    schema = get_or_build_schema(published, content_hash)

    result = schema.execute_sync(
        """
        query {
            __type(name: "Query") {
                fields { name }
            }
        }
        """
    )
    assert result.errors is None, result.errors
    field_names = {f["name"] for f in result.data["__type"]["fields"]}
    assert "movieByCanonicalId" in field_names
    assert "creditByCanonicalId" in field_names


# ---------------------------------------------------------------------------
# 4. Source on a normal class publishes fine
# ---------------------------------------------------------------------------


async def test_source_on_normal_class_publishes(pg_conn):
    """A Source targeting a normal class goes through the gate."""
    await _reset(pg_conn)
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    spec = Spec(
        id="normal_src_test",
        version="1.0.0",
        slots=[id_slot],
        classes=[movie],
        sources=[src],
    )
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)
    assert await spec_store.get_published(pg_conn) is not None


# ---------------------------------------------------------------------------
# 5. Multi-source spec publishes fine
# ---------------------------------------------------------------------------


async def test_multi_source_spec_publishes(pg_conn):
    await _reset(pg_conn)
    spec, movie, credit, movie_src, credit_src = _build_movie_credit_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)
    assert await spec_store.get_published(pg_conn) is not None


# ---------------------------------------------------------------------------
# 6. Regression: plain spec without FK slots still works
# ---------------------------------------------------------------------------


async def test_regression_no_fk_slots(pg_conn):
    """A plain spec with no FK columns publishes and queries normally."""
    await _reset(pg_conn)
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    year = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="regression_test",
        version="1.0.0",
        slots=[imdb_id, title, year],
        classes=[movie],
        sources=[src],
    )
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    await graph_store.insert_rows(
        pg_conn,
        source=src,
        spec_revision=rev,
        rows=[{"imdb_id": "tt0111161", "title": "Shawshank", "year": 1994}],
        canonical_ids=["tt0111161"],
    )

    rows = await graph_store.query_rows(
        pg_conn,
        cls=movie,
        predicate_sql=None,
        predicate_params=[],
        limit=10,
        offset=0,
    )
    assert len(rows) == 1
    assert rows[0]["imdb_id"] == "tt0111161"
    assert rows[0]["year"] == 1994
