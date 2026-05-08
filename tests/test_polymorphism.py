"""Polymorphic class tests (commitment 11 slice).

Coverage:
  1. Publish a spec with an Identifier class (identifier_pattern set) — table created.
  2. Publish a spec with a normal Source on a non-polymorphic class — works.
  3. Attempt to publish a Source targeting a polymorphic class — PublishGateError.
  4. Add Identifier rows via apply_add; verify they appear via GraphQL
     identifierByDiscriminator(targetClass="Movie", key="tt0111161").
  5. identifierByDiscriminator with an unknown (targetClass, key) returns null.
  6. Verify that movieByCanonicalId and movieResolved are present for a normal
     (non-polymorphic) class, and identifierByCanonicalId is NOT present in
     the schema for the polymorphic Identifier class.
  7. DiscriminatedRef with a target_class not on spec.classes → PublishGateError.
  8. Regression: a spec with no IdentifierPattern still works as before.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from knot import db
from knot.db import graph_store, spec_store
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
from knot.ontology.metaschema import DiscriminatedRef, IdentifierPattern


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _string_type() -> TypeDefinition:
    return TypeDefinition(name="string", base="str")


def _reset(conn):
    conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    conn.execute("TRUNCATE TABLE trust_config CASCADE")
    conn.execute("TRUNCATE TABLE users CASCADE")
    conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()


def _build_identifier_spec() -> tuple[Spec, OntologyClass, OntologyClass]:
    """Build a spec with Movie + polymorphic Identifier class."""
    st = _string_type()

    # Movie class
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title])

    # Polymorphic Identifier class
    entity_class_slot = Slot(name="entity_class", range=st, required=True)
    entity_src_key = Slot(name="entity_src_key", range=st, required=True)
    label = Slot(name="label", range=st)
    identifier = OntologyClass(
        name="Identifier",
        slots=[entity_class_slot, entity_src_key, label],
        identifier_pattern=IdentifierPattern(
            class_slot=entity_class_slot,
            key_slot=entity_src_key,
        ),
    )

    # Source for Movie only (not Identifier)
    imdb_src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)

    spec = Spec(
        id="poly_test",
        version="1.0.0",
        types=[st],
        slots=[imdb_id, title, entity_class_slot, entity_src_key, label],
        classes=[movie, identifier],
        sources=[imdb_src],
    )
    return spec, movie, identifier


# ---------------------------------------------------------------------------
# Fixture: published polymorphic spec
# ---------------------------------------------------------------------------

@pytest.fixture
def poly_db(pg_conn):
    """Reset, publish Movie + Identifier spec. Yields (conn, movie, identifier, rev)."""
    _reset(pg_conn)
    spec, movie, identifier = _build_identifier_spec()
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)
    yield pg_conn, movie, identifier, rev


# ---------------------------------------------------------------------------
# 1. Table is created for the polymorphic class
# ---------------------------------------------------------------------------

def test_polymorphic_class_table_created(poly_db):
    conn, movie, identifier, rev = poly_db
    # If the table exists, we can query it without error.
    row = conn.execute(
        "SELECT count(*) FROM knot_data.identifier"
    ).fetchone()
    assert row[0] == 0  # empty, but table exists


# ---------------------------------------------------------------------------
# 2. Source on a non-polymorphic class publishes fine
# ---------------------------------------------------------------------------

def test_source_on_normal_class_publishes(pg_conn):
    """A Source targeting a normal (non-polymorphic) class goes through the gate."""
    _reset(pg_conn)
    st = _string_type()
    id_slot = Slot(name="imdb_id", range=st, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[id_slot])
    src = Source(name="imdb", entity_class=movie, identifier_slot=id_slot)
    spec = Spec(
        id="normal_src_test",
        version="1.0.0",
        types=[st],
        slots=[id_slot],
        classes=[movie],
        sources=[src],
    )
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    # Should not raise
    publish_draft(pg_conn, rev)
    assert spec_store.get_published(pg_conn) is not None


# ---------------------------------------------------------------------------
# 3. Source targeting a polymorphic class is rejected at publish gate
# ---------------------------------------------------------------------------

def test_source_on_polymorphic_class_rejected(pg_conn):
    """Publish gate rejects a Source whose entity_class has identifier_pattern."""
    _reset(pg_conn)
    st = _string_type()
    entity_class_slot = Slot(name="entity_class", range=st, required=True)
    entity_src_key = Slot(name="entity_src_key", range=st, required=True)
    identifier = OntologyClass(
        name="Identifier",
        slots=[entity_class_slot, entity_src_key],
        identifier_pattern=IdentifierPattern(
            class_slot=entity_class_slot,
            key_slot=entity_src_key,
        ),
    )
    # Source illegally targets the polymorphic class
    bad_src = Source(
        name="bad_source",
        entity_class=identifier,
        identifier_slot=entity_class_slot,
    )
    spec = Spec(
        id="poly_src_reject_test",
        version="1.0.0",
        types=[st],
        slots=[entity_class_slot, entity_src_key],
        classes=[identifier],
        sources=[bad_src],
    )
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    with pytest.raises(PublishGateError, match="polymorphic"):
        publish_draft(pg_conn, rev)


# ---------------------------------------------------------------------------
# 4. Add rows via apply_add; retrieve via GraphQL byDiscriminator
# ---------------------------------------------------------------------------

def test_add_identifier_rows_and_query_by_discriminator(poly_db):
    """Add an Identifier row and find it via identifierByDiscriminator."""
    conn, movie, identifier, rev = poly_db

    # Add two synthetic identifier rows.
    apply_add(
        conn,
        cls=identifier,
        new_canonical_id="Movie:tt0111161",
        values={"entity_class": "Movie", "entity_src_key": "tt0111161", "label": "Shawshank"},
        spec_revision=rev,
    )
    apply_add(
        conn,
        cls=identifier,
        new_canonical_id="Movie:tt0068646",
        values={"entity_class": "Movie", "entity_src_key": "tt0068646", "label": "The Godfather"},
        spec_revision=rev,
    )

    # Query via GraphQL.
    from knot.api.main import app
    from knot.api.graphql_schema import get_or_build_schema
    from knot.ontology.canonical import compute_content_hash

    published = spec_store.get_published(conn)
    content_hash = spec_store.get_published_content_hash(conn)
    schema = get_or_build_schema(published, content_hash)

    result = schema.execute_sync(
        """
        query {
            identifierByDiscriminator(targetClass: "Movie", key: "tt0111161")
        }
        """
    )
    assert result.errors is None, result.errors
    raw = result.data["identifierByDiscriminator"]
    assert raw is not None
    row = json.loads(raw)
    assert row["entity_class"] == "Movie"
    assert row["entity_src_key"] == "tt0111161"
    assert row["label"] == "Shawshank"


# ---------------------------------------------------------------------------
# 5. byDiscriminator with unknown key returns null
# ---------------------------------------------------------------------------

def test_by_discriminator_unknown_returns_null(poly_db):
    conn, movie, identifier, rev = poly_db

    from knot.api.graphql_schema import get_or_build_schema

    published = spec_store.get_published(conn)
    content_hash = spec_store.get_published_content_hash(conn)
    schema = get_or_build_schema(published, content_hash)

    result = schema.execute_sync(
        """
        query {
            identifierByDiscriminator(targetClass: "Movie", key: "doesnotexist")
        }
        """
    )
    assert result.errors is None, result.errors
    assert result.data["identifierByDiscriminator"] is None


# ---------------------------------------------------------------------------
# 6. Schema field presence: polymorphic vs non-polymorphic
# ---------------------------------------------------------------------------

def test_schema_fields_polymorphic_vs_normal(poly_db):
    """Polymorphic class has byDiscriminator, NOT byCanonicalId or Resolved.
    Normal class has byCanonicalId and Resolved, NOT byDiscriminator.
    """
    conn, movie, identifier, rev = poly_db

    from knot.api.graphql_schema import get_or_build_schema

    published = spec_store.get_published(conn)
    content_hash = spec_store.get_published_content_hash(conn)
    schema = get_or_build_schema(published, content_hash)

    # Introspect field names from the schema.
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

    # Polymorphic Identifier class.
    assert "identifierByDiscriminator" in field_names
    assert "identifierByCanonicalId" not in field_names
    assert "identifierResolved" not in field_names

    # Normal Movie class.
    assert "movieByCanonicalId" in field_names
    assert "movieResolved" in field_names
    assert "movieByDiscriminator" not in field_names


# ---------------------------------------------------------------------------
# 7. DiscriminatedRef with missing target_class → PublishGateError
# ---------------------------------------------------------------------------

def test_discriminated_ref_missing_target_class_rejected(pg_conn):
    """DiscriminatedRef.target_class must be on spec.classes."""
    _reset(pg_conn)
    st = _string_type()

    # A class that is NOT on the spec.
    orphan_class = OntologyClass(name="Orphan", slots=[])

    entity_class_slot = Slot(name="entity_class", range=st)
    entity_src_key_slot = Slot(name="entity_src_key", range=st)

    # Slot whose reference points at the orphan class.
    ref_slot = Slot(
        name="ref_field",
        range=st,
        reference=DiscriminatedRef(
            target_class=orphan_class,
            class_slot=entity_class_slot,
            key_slot=entity_src_key_slot,
        ),
    )

    some_class = OntologyClass(name="SomeClass", slots=[ref_slot])
    id_slot = Slot(name="id", range=st, identifier=True, required=True)
    some_class.slots.append(id_slot)

    spec = Spec(
        id="discref_test",
        version="1.0.0",
        types=[st],
        slots=[entity_class_slot, entity_src_key_slot, ref_slot, id_slot],
        classes=[some_class],  # Orphan intentionally NOT included
        sources=[],
    )
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    with pytest.raises(PublishGateError, match="target_class"):
        publish_draft(pg_conn, rev)


# ---------------------------------------------------------------------------
# 8. Regression: spec without IdentifierPattern works as before
# ---------------------------------------------------------------------------

def test_regression_no_identifier_pattern(pg_conn):
    """A plain spec with no polymorphic classes publishes and queries normally."""
    _reset(pg_conn)
    st = _string_type()
    it = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=st, identifier=True, required=True)
    title = Slot(name="title", range=st)
    year = Slot(name="year", range=it)
    movie = OntologyClass(name="Movie", slots=[imdb_id, title, year])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    spec = Spec(
        id="regression_test",
        version="1.0.0",
        types=[st, it],
        slots=[imdb_id, title, year],
        classes=[movie],
        sources=[src],
    )
    rev = create_draft(pg_conn)
    update_draft(pg_conn, rev, spec)
    publish_draft(pg_conn, rev)

    graph_store.insert_rows(
        pg_conn,
        source=src,
        spec_revision=rev,
        rows=[{"imdb_id": "tt0111161", "title": "Shawshank", "year": 1994}],
    )

    rows = graph_store.query_rows(
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
