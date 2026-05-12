"""Mixin tests — slot trait composition.

Coverage:
  1. Mixin slot materializes as a column in the class's own table.
  2. Mixin slot is queryable via the GraphQL surface.
  3. Transitive mixins (Mixin includes Mixin) materialize all slots.
  4. Own slot shadows a mixin slot of the same name (no error, own wins).
  5. Two mixins contributing the same slot name → PublishGateError.
  6. Cyclic mixin chain (A → B → A) → PublishGateError.
  7. Adding a mixin to an existing class triggers AddSlot DDL on republish.
  8. Removing a mixin triggers DropSlot DDL on republish.
  9. Mixin slot referenced via apply_add is stored and reads back.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db import spec_store
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.graph.corrections import apply_add
from knot.spec import (
    OntologyClass,
    Slot,
    Source,
    Spec,
)
from knot.spec.metaschema import Primitive
from knot.spec.errors import PublishGateError

# ---------------------------------------------------------------------------
# Helpers
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


def _build_timestamped_movie_spec() -> tuple[Spec, OntologyClass, OntologyClass]:
    """Movie includes a Timestamped mixin contributing created_at/updated_at."""
    created_at = Slot(name="created_at", type=Primitive(name="datetime"))
    updated_at = Slot(name="updated_at", type=Primitive(name="datetime"))
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at, updated_at],
        abstract=True,
    )

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    movie = OntologyClass(
        name="Movie",
        slots=[imdb_id, title],
        mixins=[timestamped],
    )

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_test",
        version="1.0.0",
        slots=[imdb_id, title, created_at, updated_at],
        classes=[movie, timestamped],
        sources=[src],
    )
    return spec, movie, timestamped


# ---------------------------------------------------------------------------
# 1. Mixin slot materializes as a column on the class's own table
# ---------------------------------------------------------------------------


async def test_mixin_slot_becomes_column(pg_conn):
    await _reset(pg_conn)
    spec, movie, _ = _build_timestamped_movie_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    cur = await pg_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'knot_data' AND table_name = 'movie' "
        "ORDER BY column_name"
    )
    cols = await cur.fetchall()
    names = {r[0] for r in cols}
    assert "created_at" in names
    assert "updated_at" in names
    assert "title" in names
    assert "imdb_id" in names


# ---------------------------------------------------------------------------
# 2. Mixin slot is queryable via GraphQL
# ---------------------------------------------------------------------------


async def test_mixin_slot_is_queryable_via_graphql(pg_conn):
    await _reset(pg_conn)
    spec, movie, _ = _build_timestamped_movie_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    await apply_add(
        pg_conn,
        cls=movie,
        new_canonical_id="tt0111161",
        values={
            "imdb_id": "tt0111161",
            "title": "Shawshank",
            "created_at": "2026-01-01T00:00:00+00:00",
        },
        spec_revision=rev,
    )

    from knot.spec.canonical import compute_content_hash
    from knot.spec.compile.graphql import get_or_build_schema

    published = await spec_store.get_published(pg_conn)
    schema = get_or_build_schema(published, compute_content_hash(published))

    result = await schema.execute(
        "{ movie { imdbId title createdAt } }",
    )
    assert result.errors is None, result.errors
    rows = result.data["movie"]
    assert any(r.get("createdAt") is not None for r in rows)


# ---------------------------------------------------------------------------
# 3. Transitive mixins (Mixin includes Mixin)
# ---------------------------------------------------------------------------


async def test_transitive_mixin_chain(pg_conn):
    await _reset(pg_conn)

    audited_at = Slot(name="audited_at", type=Primitive(name="datetime"))
    audited = OntologyClass(name="Audited", slots=[audited_at], abstract=True)

    created_at = Slot(name="created_at", type=Primitive(name="datetime"))
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        mixins=[audited],
        abstract=True,
    )

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id], mixins=[timestamped])

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_chain",
        version="1.0.0",
        slots=[imdb_id, created_at, audited_at],
        classes=[movie, timestamped, audited],
        sources=[src],
    )

    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    cur = await pg_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'knot_data' AND table_name = 'movie'"
    )
    cols = await cur.fetchall()
    names = {r[0] for r in cols}
    assert "created_at" in names
    assert "audited_at" in names


# ---------------------------------------------------------------------------
# 4. Own slot shadows mixin slot of same name
# ---------------------------------------------------------------------------


async def test_own_slot_shadows_mixin_slot(pg_conn):
    await _reset(pg_conn)

    # Mixin contributes a `name` slot of type datetime
    mixin_name = Slot(name="name", type=Primitive(name="datetime"))
    bad_mixin = OntologyClass(name="BadMixin", slots=[mixin_name], abstract=True)

    # Own `name` slot of type str — should win.
    own_name = Slot(name="name", type=Primitive(name="string"))
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(
        name="Movie",
        slots=[imdb_id, own_name],
        mixins=[bad_mixin],
    )
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="own_shadows_mixin",
        version="1.0.0",
        slots=[imdb_id, own_name, mixin_name],
        classes=[movie, bad_mixin],
        sources=[src],
    )

    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)  # should NOT raise — own wins

    # Verify the column is the own slot's type (TEXT), not the mixin's (TIMESTAMPTZ).
    cur = await pg_conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'knot_data' AND table_name = 'movie' "
        "AND column_name = 'name'"
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0].lower() == "text"


# ---------------------------------------------------------------------------
# 5. Slot name collision across mixins → PublishGateError
# ---------------------------------------------------------------------------


async def test_mixin_slot_collision_rejected(pg_conn):
    await _reset(pg_conn)

    a_label = Slot(name="label", type=Primitive(name="string"))
    a = OntologyClass(name="A", slots=[a_label], abstract=True)

    b_label = Slot(name="label", type=Primitive(name="string"))
    b = OntologyClass(name="B", slots=[b_label], abstract=True)

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id], mixins=[a, b])

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_collision",
        version="1.0.0",
        slots=[imdb_id, a_label, b_label],
        classes=[movie, a, b],
        sources=[src],
    )

    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    with pytest.raises(PublishGateError, match="collision"):
        await publish_draft(pg_conn, rev)


# ---------------------------------------------------------------------------
# 6. Cyclic mixin chain → PublishGateError
# ---------------------------------------------------------------------------


async def test_mixin_cycle_rejected(pg_conn):
    await _reset(pg_conn)

    a = OntologyClass(name="A", slots=[], abstract=True)
    b = OntologyClass(name="B", slots=[], abstract=True, mixins=[a])
    a.mixins = [b]  # close the cycle: A → B → A

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id], mixins=[a])

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_cycle",
        version="1.0.0",
        slots=[imdb_id],
        classes=[movie, a, b],
        sources=[src],
    )

    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    with pytest.raises(PublishGateError, match="cyclic"):
        await publish_draft(pg_conn, rev)


# ---------------------------------------------------------------------------
# 7. Mixin slot is read/written via apply_add
#
# Pure-Python diff tests for AddSlot / DropSlot emission on mixin add /
# remove live in tests/unit/spec/compile/test_mixins_diff.py.
# ---------------------------------------------------------------------------


async def test_mixin_slot_round_trip_via_apply_add(pg_conn):
    await _reset(pg_conn)
    spec, movie, _ = _build_timestamped_movie_spec()
    rev = await create_draft(pg_conn)
    await update_draft(pg_conn, rev, spec)
    await publish_draft(pg_conn, rev)

    await apply_add(
        pg_conn,
        cls=movie,
        new_canonical_id="tt0068646",
        values={
            "imdb_id": "tt0068646",
            "title": "The Godfather",
            "created_at": "2026-02-01T00:00:00+00:00",
            "updated_at": "2026-02-02T00:00:00+00:00",
        },
        spec_revision=rev,
    )
    cur = await pg_conn.execute(
        "SELECT created_at, updated_at FROM knot_data.movie WHERE imdb_id = %s",
        ("tt0068646",),
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0] is not None
    assert row[1] is not None
