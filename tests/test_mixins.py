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
from knot.db import migration, spec_store
from knot.db.spec_store import (
    PublishGateError,
    create_draft,
    publish_draft,
    update_draft,
)
from knot.graph.corrections import apply_add
from knot.ontology import (
    OntologyClass,
    Slot,
    Source,
    Spec,
    TypeDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _string_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _ts_type() -> TypeDefinition:
    return TypeDefinition(name="datetime", base="datetime")


def _reset(conn):
    conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    conn.execute("TRUNCATE TABLE trust_config CASCADE")
    conn.execute("TRUNCATE TABLE users CASCADE")
    conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()


def _build_timestamped_movie_spec() -> tuple[Spec, OntologyClass, OntologyClass]:
    """Movie includes a Timestamped mixin contributing created_at/updated_at."""
    st, dt = _string_type(), _ts_type()

    created_at = Slot(name="created_at", range=dt)
    updated_at = Slot(name="updated_at", range=dt)
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at, updated_at],
        abstract=True,
    )

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(
        name="Movie",
        slots=[imdb_id, title],
        mixins=[timestamped],
    )

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_test",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id, title, created_at, updated_at],
        classes=[movie, timestamped],
        sources=[src],
    )
    return spec, movie, timestamped


# ---------------------------------------------------------------------------
# 1. Mixin slot materializes as a column on the class's own table
# ---------------------------------------------------------------------------

def test_mixin_slot_becomes_column(pg_conn):
    _reset(pg_conn)
    spec, movie, _ = _build_timestamped_movie_spec()
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    cols = pg_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'knot_data' AND table_name = 'movie' "
        "ORDER BY column_name"
    ).fetchall()
    names = {r[0] for r in cols}
    assert "created_at" in names
    assert "updated_at" in names
    assert "title" in names
    assert "imdb_id" in names


# ---------------------------------------------------------------------------
# 2. Mixin slot is queryable via GraphQL
# ---------------------------------------------------------------------------

def test_mixin_slot_is_queryable_via_graphql(pg_conn):
    _reset(pg_conn)
    spec, movie, _ = _build_timestamped_movie_spec()
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    apply_add(
        pg_conn,
        cls=movie,
        new_canonical_id="tt0111161",
        values={"imdb_id": "tt0111161", "title": "Shawshank",
                "created_at": "2026-01-01T00:00:00+00:00"},
        spec_revision=rev,
    )

    from knot.api._graphql_schema import get_or_build_schema
    from knot.ontology.canonical import compute_content_hash

    published = spec_store.get_published(pg_conn)
    schema = get_or_build_schema(published, compute_content_hash(published))

    result = schema.execute_sync(
        "{ movie { rows } }",
        context_value={"conn": pg_conn},
    )
    assert result.errors is None, result.errors
    rows = result.data["movie"]["rows"]
    assert any("created_at" in r for r in rows)


# ---------------------------------------------------------------------------
# 3. Transitive mixins (Mixin includes Mixin)
# ---------------------------------------------------------------------------

def test_transitive_mixin_chain(pg_conn):
    _reset(pg_conn)
    st, dt = _string_type(), _ts_type()

    audited_at = Slot(name="audited_at", range=dt)
    audited = OntologyClass(name="Audited", slots=[audited_at], abstract=True)

    created_at = Slot(name="created_at", range=dt)
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        mixins=[audited],
        abstract=True,
    )

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id], mixins=[timestamped])

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_chain",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id, created_at, audited_at],
        classes=[movie, timestamped, audited],
        sources=[src],
    )

    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    cols = pg_conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'knot_data' AND table_name = 'movie'"
    ).fetchall()
    names = {r[0] for r in cols}
    assert "created_at" in names
    assert "audited_at" in names


# ---------------------------------------------------------------------------
# 4. Own slot shadows mixin slot of same name
# ---------------------------------------------------------------------------

def test_own_slot_shadows_mixin_slot(pg_conn):
    _reset(pg_conn)
    st, dt = _string_type(), _ts_type()

    # Mixin contributes a `name` slot of type datetime
    mixin_name = Slot(name="name", range=dt)
    bad_mixin = OntologyClass(name="BadMixin", slots=[mixin_name], abstract=True)

    # Own `name` slot of type str — should win.
    own_name = Slot(name="name", range=st)
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(
        name="Movie",
        slots=[imdb_id, own_name],
        mixins=[bad_mixin],
    )
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="own_shadows_mixin",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id, own_name, mixin_name],
        classes=[movie, bad_mixin],
        sources=[src],
    )

    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)  # should NOT raise — own wins

    # Verify the column is the own slot's type (TEXT), not the mixin's (TIMESTAMPTZ).
    row = pg_conn.execute(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_schema = 'knot_data' AND table_name = 'movie' "
        "AND column_name = 'name'"
    ).fetchone()
    assert row is not None
    assert row[0].lower() == "text"


# ---------------------------------------------------------------------------
# 5. Slot name collision across mixins → PublishGateError
# ---------------------------------------------------------------------------

def test_mixin_slot_collision_rejected(pg_conn):
    _reset(pg_conn)
    st = _string_type()

    a_label = Slot(name="label", range=st)
    a = OntologyClass(name="A", slots=[a_label], abstract=True)

    b_label = Slot(name="label", range=st)
    b = OntologyClass(name="B", slots=[b_label], abstract=True)

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id], mixins=[a, b])

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_collision",
        version="1.0.0",
        types=[st],
        slots=[imdb_id, a_label, b_label],
        classes=[movie, a, b],
        sources=[src],
    )

    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    with pytest.raises(PublishGateError, match="collision"):
        publish_draft(pg_conn, rev)


# ---------------------------------------------------------------------------
# 6. Cyclic mixin chain → PublishGateError
# ---------------------------------------------------------------------------

def test_mixin_cycle_rejected(pg_conn):
    _reset(pg_conn)
    st = _string_type()

    a = OntologyClass(name="A", slots=[], abstract=True)
    b = OntologyClass(name="B", slots=[], abstract=True, mixins=[a])
    a.mixins = [b]  # close the cycle: A → B → A

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id], mixins=[a])

    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="mixin_cycle",
        version="1.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[movie, a, b],
        sources=[src],
    )

    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    with pytest.raises(PublishGateError, match="cyclic"):
        publish_draft(pg_conn, rev)


# ---------------------------------------------------------------------------
# 7. Adding a mixin shows up in the diff as AddSlot
# ---------------------------------------------------------------------------

def test_add_mixin_emits_addslot_diff():
    st, dt = _string_type(), _ts_type()

    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v1 = OntologyClass(name="Movie", slots=[imdb_id])
    src_v1 = Source(name="imdb", entity_class=movie_v1, identifier_slot=imdb_id)
    spec_v1 = Spec(
        id="add_mixin",
        version="1.0.0",
        types=[st],
        slots=[imdb_id],
        classes=[movie_v1],
        sources=[src_v1],
    )

    created_at = Slot(name="created_at", range=dt)
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        abstract=True,
    )
    imdb_id2 = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v2 = OntologyClass(
        name="Movie",
        slots=[imdb_id2],
        mixins=[timestamped],
    )
    src_v2 = Source(name="imdb", entity_class=movie_v2, identifier_slot=imdb_id2)
    spec_v2 = Spec(
        id="add_mixin",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id2, created_at],
        classes=[movie_v2, timestamped],
        sources=[src_v2],
    )

    changes = migration.diff_specs(spec_v1, spec_v2)
    add_slot_changes = [c for c in changes if isinstance(c, migration.AddSlot)]
    added_names = {c.slot.name for c in add_slot_changes}
    assert "created_at" in added_names


# ---------------------------------------------------------------------------
# 8. Removing a mixin shows up in the diff as DropSlot
# ---------------------------------------------------------------------------

def test_remove_mixin_emits_dropslot_diff():
    st, dt = _string_type(), _ts_type()

    created_at = Slot(name="created_at", range=dt)
    timestamped = OntologyClass(
        name="Timestamped",
        slots=[created_at],
        abstract=True,
    )
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v1 = OntologyClass(
        name="Movie",
        slots=[imdb_id],
        mixins=[timestamped],
    )
    src_v1 = Source(name="imdb", entity_class=movie_v1, identifier_slot=imdb_id)
    spec_v1 = Spec(
        id="remove_mixin",
        version="1.0.0",
        types=[st, dt],
        slots=[imdb_id, created_at],
        classes=[movie_v1, timestamped],
        sources=[src_v1],
    )

    imdb_id2 = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie_v2 = OntologyClass(name="Movie", slots=[imdb_id2])
    src_v2 = Source(name="imdb", entity_class=movie_v2, identifier_slot=imdb_id2)
    spec_v2 = Spec(
        id="remove_mixin",
        version="1.0.0",
        types=[st],
        slots=[imdb_id2],
        classes=[movie_v2],
        sources=[src_v2],
    )

    changes = migration.diff_specs(spec_v1, spec_v2)
    drop_slot_changes = [c for c in changes if isinstance(c, migration.DropSlot)]
    dropped_names = {c.slot_name for c in drop_slot_changes}
    assert "created_at" in dropped_names


# ---------------------------------------------------------------------------
# 9. Mixin slot is read/written via apply_add
# ---------------------------------------------------------------------------

def test_mixin_slot_round_trip_via_apply_add(pg_conn):
    _reset(pg_conn)
    spec, movie, _ = _build_timestamped_movie_spec()
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    apply_add(
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
    row = pg_conn.execute(
        "SELECT created_at, updated_at FROM knot_data.movie "
        "WHERE imdb_id = %s",
        ("tt0068646",),
    ).fetchone()
    assert row is not None
    assert row[0] is not None
    assert row[1] is not None
