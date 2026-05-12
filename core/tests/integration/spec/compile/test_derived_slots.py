"""End-to-end tests for derived-slot execution.

Coverage
--------
Unit (no DB):
  1. RelationProject handler emits array_agg subquery (forward FK)
  2. RelationProject handler emits array_agg subquery (ReverseRelation)
  3. RelationProject with FilteredRelation adds WHERE clause
  4. RelationCount handler emits count(*) subquery
  5. RelationCount with distinct=True emits DISTINCT
  6. RelationAggregate MIN/MAX/SUM/AVG emits correct aggregate function
  7. RelationAggregate COLLECT emits array_agg
  8. RelationAggregate FIRST emits LIMIT 1 subquery
  9. RelationAggregate COUNT emits count(*)
 10. FilteredRelation used standalone raises NotImplementedError (still a stub)
 11. _derived_column_exprs returns empty list for class with no derived slots
 12. _select_with_derivations is identical to _select_with_binding when no derivations

Integration (live DB):
 13. Publish Movie+Credit+Person spec where Movie.directors is derived
 14. GraphQL moviePage rows include directors array
 15. Movie with no credits → directors is null/empty array
 16. Re-publishing a spec that adds a derived slot does NOT trigger a destructive migration
 17. movieByCanonicalId returns derived slot value
 18. RelationCount derivation returns integer scalar
 19. RelationAggregate COLLECT derivation returns array
 20. FilteredRelation filters correctly at query time
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from knot import db
from knot.db import graph_store
from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.spec import is_stored
from knot.spec.compile.postgres import (
    CompileContext,
    compile_predicate,
)
from knot.spec.compile.postgres._queries import (
    derived_column_exprs as _derived_column_exprs,
)
from knot.spec.compile.postgres._queries import (
    select_with_derivations as _select_with_derivations,
)
from knot.spec.metaschema import (
    AggFunc,
    Array,
    ClassRef,
    Compare,
    CompareOp,
    FilteredRelation,
    Literal_,
    OntologyClass,
    Primitive,
    RelationAggregate,
    RelationCount,
    RelationProject,
    RelationRef,
    ReverseRelation,
    Slot,
    SlotPath,
    Source,
    Spec,
)

# ---------------------------------------------------------------------------
# Shared spec builder: Movie + Credit + Person
#
# Schema:
#   Person: person_id (identifier), name
#   Movie:  imdb_id  (identifier), title
#           directors (derived) = RelationProject(
#               FilteredRelation(ReverseRelation(Credit, Credit.movie), role='director'),
#               project=Credit.person_name,
#           )
#           credit_count (derived) = RelationCount(ReverseRelation(Credit, Credit.movie))
#   Credit: credit_id (identifier), movie (FK→Movie canonical_id), role, person_name
# ---------------------------------------------------------------------------


def _build_full_spec() -> tuple[
    Spec,
    OntologyClass,  # Movie
    OntologyClass,  # Credit
    OntologyClass,  # Person
    Slot,  # imdb_id
    Slot,  # directors (derived)
    Slot,  # credit_count (derived)
    Slot,  # credit_id
    Slot,  # credit_movie (FK)
    Slot,  # credit_role
    Slot,  # credit_person_name
    Source,  # movie_src
    Source,  # credit_src
]:
    # --- Person ---
    person_id = Slot(name="person_id", type=Primitive(name="string"), identifier=True, required=True)
    person_name_slot = Slot(name="name", type=Primitive(name="string"))
    person_cls = OntologyClass(name="Person", slots=[person_id, person_name_slot])

    # --- Movie (no derived slots yet — added below after Credit is defined) ---
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    # Placeholder movie class (needed as FK target for Credit.movie)
    movie_cls = OntologyClass(name="Movie", slots=[imdb_id, title])

    # --- Credit ---
    credit_id = Slot(name="credit_id", type=Primitive(name="string"), identifier=True, required=True)
    # movie FK: TEXT column holding the movie's canonical_id
    credit_movie = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_role = Slot(name="role", type=Primitive(name="string"))
    credit_person_name = Slot(name="person_name", type=Primitive(name="string"))
    credit_cls = OntologyClass(
        name="Credit",
        slots=[credit_id, credit_movie, credit_role, credit_person_name],
    )

    # --- Derived slots on Movie ---
    # directors = array_agg(Credit.person_name) WHERE Credit.movie = movie._canonical_id AND role='director'
    rev_rel = ReverseRelation(target_class=credit_cls, fk_slot=credit_movie)
    role_filter = Compare(
        op=CompareOp.EQ,
        left=SlotPath(from_class=credit_cls, slots=[credit_role]),
        right=Literal_(value="director"),
    )
    filtered_rev = FilteredRelation(relation=rev_rel, filter=role_filter)
    directors_derivation = RelationProject(
        relation=filtered_rev,
        project=SlotPath(from_class=credit_cls, slots=[credit_person_name]),
    )
    directors_slot = Slot(
        name="directors",
        type=Array(of=Primitive(name="string")),
        derivation=directors_derivation,
    )

    # credit_count = count(*) of all Credits for this Movie
    credit_count_derivation = RelationCount(
        relation=ReverseRelation(target_class=credit_cls, fk_slot=credit_movie),
    )
    credit_count_slot = Slot(
        name="credit_count",
        type=Primitive(name="integer"),
        derivation=credit_count_derivation,
    )

    # Patch movie_cls slots to include derived slots
    movie_cls.slots = [imdb_id, title, directors_slot, credit_count_slot]

    # --- Sources ---
    movie_src = Source(name="imdb", entity_class=movie_cls, identifier_slot=imdb_id)
    credit_src = Source(name="credits", entity_class=credit_cls, identifier_slot=credit_id)

    spec = Spec(
        id="derived_test",
        version="1.0.0",
        slots=[
            person_id,
            person_name_slot,
            imdb_id,
            title,
            directors_slot,
            credit_count_slot,
            credit_id,
            credit_movie,
            credit_role,
            credit_person_name,
        ],
        classes=[person_cls, movie_cls, credit_cls],
        sources=[movie_src, credit_src],
    )
    return (
        spec,
        movie_cls,
        credit_cls,
        person_cls,
        imdb_id,
        directors_slot,
        credit_count_slot,
        credit_id,
        credit_movie,
        credit_role,
        credit_person_name,
        movie_src,
        credit_src,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
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


@pytest_asyncio.fixture
async def full_spec_db(clean_db):
    """Publish the Movie+Credit spec and return (conn, spec, rev, entities...)."""
    conn = clean_db
    (
        spec,
        movie_cls,
        credit_cls,
        person_cls,
        imdb_id,
        directors_slot,
        credit_count_slot,
        credit_id,
        credit_movie,
        credit_role,
        credit_person_name,
        movie_src,
        credit_src,
    ) = _build_full_spec()
    rev = await create_draft(conn)
    await update_draft(conn, rev, spec)
    await publish_draft(conn, rev)

    # Ingest movies
    await graph_store.insert_rows(
        conn,
        source=movie_src,
        spec_revision=rev,
        rows=[
            {"imdb_id": "tt0000001", "title": "Film A"},
            {"imdb_id": "tt0000002", "title": "Film B"},
        ],
        canonical_ids=["tt0000001", "tt0000002"],
    )
    # Ingest credits: Film A has two directors; Film B has none
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "c001", "movie": "tt0000001", "role": "director", "person_name": "Alice"},
            {"credit_id": "c002", "movie": "tt0000001", "role": "director", "person_name": "Bob"},
            {"credit_id": "c003", "movie": "tt0000001", "role": "actor", "person_name": "Carol"},
        ],
        canonical_ids=["c001", "c002", "c003"],
    )
    return conn, spec, rev, movie_cls, credit_cls, movie_src, credit_src


@pytest.fixture
def gql_client_full(full_spec_db):
    from fastapi.testclient import TestClient

    from knot.api.main import app

    os.environ["KNOT_AUTH_DEV_MODE"] = "1"
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        os.environ.pop("KNOT_AUTH_DEV_MODE", None)


def _post(client, query: str, variables: dict | None = None) -> dict:
    resp = client.post("/graph/query", json={"query": query, "variables": variables})
    assert resp.status_code == 200, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Unit tests (no DB)
# ---------------------------------------------------------------------------


def _make_ctx(cls: OntologyClass) -> CompileContext:
    return CompileContext(primary_class=cls, alias="s")


# 1. RelationProject forward FK → array_agg subquery
def test_relation_project_forward_fk_emits_array_agg():
    name_slot = Slot(name="name", type=Primitive(name="string"))
    person_cls = OntologyClass(name="Person", slots=[name_slot])
    fk_slot = Slot(name="person", type=ClassRef(target_class=person_cls))
    movie_cls = OntologyClass(name="Movie", slots=[fk_slot])
    ctx = _make_ctx(movie_cls)

    ref = RelationRef(from_class=movie_cls, slot=fk_slot)
    node = RelationProject(
        relation=ref,
        project=SlotPath(from_class=person_cls, slots=[name_slot]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "array_agg" in rendered
    assert '"name"' in rendered
    assert "SELECT" in rendered


# 2. RelationProject ReverseRelation → array_agg subquery
def test_relation_project_reverse_relation_emits_array_agg():
    person_name = Slot(name="person_name", type=Primitive(name="string"))
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot, person_name])
    movie_cls.slots = []  # movie has no direct slots for the FK direction
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationProject(
        relation=rev,
        project=SlotPath(from_class=credit_cls, slots=[person_name]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "array_agg" in rendered
    assert '"person_name"' in rendered
    assert "movie_bindings" in rendered.lower() or '"movie"' in rendered


# 3. RelationProject with FilteredRelation adds WHERE clause
def test_relation_project_filtered_adds_where():
    role_slot = Slot(name="role", type=Primitive(name="string"))
    person_name = Slot(name="person_name", type=Primitive(name="string"))
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot, role_slot, person_name])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    role_eq = Compare(
        op=CompareOp.EQ,
        left=SlotPath(from_class=credit_cls, slots=[role_slot]),
        right=Literal_(value="director"),
    )
    filtered = FilteredRelation(relation=rev, filter=role_eq)
    node = RelationProject(
        relation=filtered,
        project=SlotPath(from_class=credit_cls, slots=[person_name]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "array_agg" in rendered
    assert ctx.params == ["director"]
    assert "AND" in rendered


# 4. RelationCount emits count(*)
def test_relation_count_emits_count_star():
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationCount(relation=rev)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "count(*)" in rendered.lower()


# 5. RelationCount distinct emits DISTINCT
def test_relation_count_distinct_emits_distinct():
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationCount(relation=rev, distinct=True)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "DISTINCT" in rendered


# 6. RelationAggregate MIN/MAX/SUM/AVG
@pytest.mark.parametrize(
    "func,expected_sql",
    [
        (AggFunc.MIN, "min"),
        (AggFunc.MAX, "max"),
        (AggFunc.SUM, "sum"),
        (AggFunc.AVG, "avg"),
    ],
)
def test_relation_aggregate_standard_funcs(func, expected_sql):
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    score_slot = Slot(name="score", type=Primitive(name="integer"))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot, score_slot])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationAggregate(
        relation=rev,
        func=func,
        operand=SlotPath(from_class=credit_cls, slots=[score_slot]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert expected_sql in rendered.lower()
    assert '"score"' in rendered


# 7. RelationAggregate COLLECT emits array_agg
def test_relation_aggregate_collect_emits_array_agg():
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    tag_slot = Slot(name="tag", type=Primitive(name="string"))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot, tag_slot])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationAggregate(
        relation=rev,
        func=AggFunc.COLLECT,
        operand=SlotPath(from_class=credit_cls, slots=[tag_slot]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "array_agg" in rendered


# 8. RelationAggregate FIRST emits LIMIT 1
def test_relation_aggregate_first_emits_limit_1():
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    name_slot = Slot(name="person_name", type=Primitive(name="string"))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot, name_slot])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationAggregate(
        relation=rev,
        func=AggFunc.FIRST,
        operand=SlotPath(from_class=credit_cls, slots=[name_slot]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "LIMIT 1" in rendered
    assert '"person_name"' in rendered


# 9. RelationAggregate COUNT emits count(*)
def test_relation_aggregate_count_emits_count_star():
    movie_cls = OntologyClass(name="Movie", slots=[])
    fk_slot = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    credit_cls = OntologyClass(name="Credit", slots=[fk_slot])
    ctx = _make_ctx(movie_cls)

    rev = ReverseRelation(target_class=credit_cls, fk_slot=fk_slot)
    node = RelationAggregate(relation=rev, func=AggFunc.COUNT)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "count(*)" in rendered.lower()


# 10. FilteredRelation used standalone raises NotImplementedError
def test_filtered_relation_standalone_raises():
    slot = Slot(name="role", type=Primitive(name="string"))
    cls = OntologyClass(name="Credit", slots=[slot])
    ctx = _make_ctx(cls)

    ref = RelationRef(from_class=cls, slot=slot)
    pred = Compare(op=CompareOp.IS_NOT_NULL, left=SlotPath(from_class=cls, slots=[slot]))
    node = FilteredRelation(relation=ref, filter=pred)
    with pytest.raises(NotImplementedError):
        compile_predicate(node, ctx)


# 11. _derived_column_exprs returns empty lists when no derived slots
def test_derived_column_exprs_empty_for_stored_only_class():
    slot = Slot(name="title", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", slots=[slot])
    derived_cols, derived_params = _derived_column_exprs(cls)
    assert derived_cols == []
    assert derived_params == []


# 12. _select_with_derivations equals _select_with_binding when no derivations
def test_select_with_derivations_no_derived_slots_same_as_binding():
    from knot.db.graph_store import _select_with_binding

    slot = Slot(name="title", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", slots=[slot])
    base = _select_with_binding(cls)
    derived_sql, derived_params = _select_with_derivations(cls)
    assert base.as_string(None) == derived_sql.as_string(None)
    assert derived_params == []


# ---------------------------------------------------------------------------
# Integration tests (live DB)
# ---------------------------------------------------------------------------


# 13. Derived slot is_stored returns False
def test_derived_slot_is_not_stored():
    (spec, movie_cls, *_) = _build_full_spec()
    directors_slot = next(s for s in movie_cls.slots if s.name == "directors")
    assert not is_stored(directors_slot)
    imdb_id_slot = next(s for s in movie_cls.slots if s.name == "imdb_id")
    assert is_stored(imdb_id_slot)


# 14. GraphQL moviePage rows include directors array
def test_graphql_derived_directors_in_rows(gql_client_full):
    query = "{ movie { imdbId directors } movieCount }"
    result = _post(gql_client_full, query)
    assert "errors" not in result, result.get("errors")
    assert result["data"]["movieCount"] == 2
    rows = result["data"]["movie"]
    film_a = next(r for r in rows if r["imdbId"] == "tt0000001")
    film_b = next(r for r in rows if r["imdbId"] == "tt0000002")

    directors_a = film_a.get("directors")
    assert directors_a is not None, f"directors missing from row: {film_a}"
    assert set(directors_a) == {"Alice", "Bob"}, f"unexpected directors: {directors_a}"

    directors_b = film_b.get("directors")
    assert directors_b is None or directors_b == [], (
        f"Film B should have no directors, got: {directors_b}"
    )


# 15. credit_count derived slot returns correct integer
def test_graphql_derived_credit_count(gql_client_full):
    query = "{ movie { imdbId creditCount } }"
    result = _post(gql_client_full, query)
    assert "errors" not in result, result.get("errors")
    rows = result["data"]["movie"]
    film_a = next(r for r in rows if r["imdbId"] == "tt0000001")
    film_b = next(r for r in rows if r["imdbId"] == "tt0000002")
    assert film_a.get("creditCount") == 3, f"expected 3, got {film_a.get('creditCount')}"
    assert film_b.get("creditCount") == 0, f"expected 0, got {film_b.get('creditCount')}"


# 16. movieByCanonicalId returns derived slot value
def test_graphql_by_canonical_id_includes_derived(gql_client_full):
    query = '{ movieByCanonicalId(canonicalId: "tt0000001") { imdbId directors creditCount } }'
    result = _post(gql_client_full, query)
    assert "errors" not in result, result.get("errors")
    row = result["data"]["movieByCanonicalId"]
    assert row is not None
    directors = row.get("directors")
    assert directors is not None
    assert set(directors) == {"Alice", "Bob"}


# 17. Re-publishing with a new derived slot does not add a column (no destructive migration)
async def test_republish_with_new_derived_slot_no_destructive_migration(clean_db):
    """Adding a derived slot to an existing published spec should publish
    without raising PublishGateError or requiring allow_destructive=True."""
    conn = clean_db

    # v1: Movie + Credit (Credit has FK back to Movie)
    imdb_id_v1 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title_v1 = Slot(name="title", type=Primitive(name="string"))
    movie_v1 = OntologyClass(name="Movie", slots=[imdb_id_v1, title_v1])
    cid_v1 = Slot(name="credit_id", type=Primitive(name="string"), identifier=True, required=True)
    cmovie_v1 = Slot(name="movie", type=ClassRef(target_class=movie_v1))
    credit_v1 = OntologyClass(name="Credit", slots=[cid_v1, cmovie_v1])
    movie_src_v1 = Source(name="imdb", entity_class=movie_v1, identifier_slot=imdb_id_v1)
    credit_src_v1 = Source(name="credits", entity_class=credit_v1, identifier_slot=cid_v1)

    spec_v1 = Spec(
        id="test",
        version="1.0.0",
        slots=[imdb_id_v1, title_v1, cid_v1, cmovie_v1],
        classes=[movie_v1, credit_v1],
        sources=[movie_src_v1, credit_src_v1],
    )
    rev1 = await create_draft(conn)
    await update_draft(conn, rev1, spec_v1)
    await publish_draft(conn, rev1)

    await graph_store.insert_rows(
        conn,
        source=movie_src_v1,
        spec_revision=rev1,
        rows=[{"imdb_id": "tt0000001", "title": "Film A"}],
        canonical_ids=["tt0000001"],
    )

    # v2: same Movie + Credit objects, Movie gains a derived slot (no new column).
    count_derivation = RelationCount(
        relation=ReverseRelation(target_class=credit_v1, fk_slot=cmovie_v1),
    )
    count_slot = Slot(name="credit_count", derivation=count_derivation)

    # Patch the same movie object — publish gate validates by identity
    movie_v1.slots = [imdb_id_v1, title_v1, count_slot]
    movie_src_v2 = Source(name="imdb", entity_class=movie_v1, identifier_slot=imdb_id_v1)
    credit_src_v2 = Source(name="credits", entity_class=credit_v1, identifier_slot=cid_v1)

    spec_v2 = Spec(
        id="test",
        version="1.0.0",
        slots=[imdb_id_v1, title_v1, count_slot, cid_v1, cmovie_v1],
        classes=[movie_v1, credit_v1],
        sources=[movie_src_v2, credit_src_v2],
    )
    rev2 = await create_draft(conn)
    await update_draft(conn, rev2, spec_v2)
    result = await publish_draft(conn, rev2)
    assert result == rev2


# 18. RelationAggregate COLLECT derivation integration test
async def test_integration_relation_aggregate_collect(clean_db):
    """Use a COLLECT derivation directly via graph_store.query_rows."""
    conn = clean_db

    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Slot(name="title", type=Primitive(name="string"))
    movie_cls = OntologyClass(name="Movie", slots=[imdb_id, title])
    cid = Slot(name="credit_id", type=Primitive(name="string"), identifier=True, required=True)
    cmovie = Slot(name="movie", type=ClassRef(target_class=movie_cls))
    crole = Slot(name="role", type=Primitive(name="string"))
    credit_cls = OntologyClass(name="Credit", slots=[cid, cmovie, crole])

    roles_derivation = RelationAggregate(
        relation=ReverseRelation(target_class=credit_cls, fk_slot=cmovie),
        func=AggFunc.COLLECT,
        operand=SlotPath(from_class=credit_cls, slots=[crole]),
    )
    roles_slot = Slot(
        name="roles",
        type=Array(of=Primitive(name="string")),
        derivation=roles_derivation,
    )
    movie_cls.slots = [imdb_id, title, roles_slot]

    movie_src = Source(name="imdb", entity_class=movie_cls, identifier_slot=imdb_id)
    credit_src = Source(name="credits", entity_class=credit_cls, identifier_slot=cid)

    spec = Spec(
        id="agg_test",
        version="1.0.0",
        slots=[imdb_id, title, roles_slot, cid, cmovie, crole],
        classes=[movie_cls, credit_cls],
        sources=[movie_src, credit_src],
    )
    rev = await create_draft(conn)
    await update_draft(conn, rev, spec)
    await publish_draft(conn, rev)

    await graph_store.insert_rows(
        conn,
        source=movie_src,
        spec_revision=rev,
        rows=[{"imdb_id": "m1", "title": "Test Film"}],
        canonical_ids=["m1"],
    )
    await graph_store.insert_rows(
        conn,
        source=credit_src,
        spec_revision=rev,
        rows=[
            {"credit_id": "x1", "movie": "m1", "role": "director"},
            {"credit_id": "x2", "movie": "m1", "role": "actor"},
        ],
        canonical_ids=["x1", "x2"],
    )

    rows = await graph_store.query_rows(
        conn,
        cls=movie_cls,
        predicate_sql=None,
        predicate_params=[],
    )
    assert len(rows) == 1
    roles = rows[0].get("roles")
    assert roles is not None
    assert set(roles) == {"director", "actor"}


# 19. FilteredRelation filters correctly in derived slot (integration)
async def test_integration_filtered_relation_derivation(full_spec_db):
    """directors derived slot only returns role='director' entries, not 'actor'."""
    conn, spec, rev, movie_cls, credit_cls, movie_src, credit_src = full_spec_db

    rows = await graph_store.query_rows(
        conn,
        cls=movie_cls,
        predicate_sql=None,
        predicate_params=[],
    )
    film_a = next(r for r in rows if r["imdb_id"] == "tt0000001")
    directors = film_a.get("directors")
    assert "Carol" not in (directors or []), f"Carol should not be in directors: {directors}"
    assert set(directors) == {"Alice", "Bob"}


# 20. Derived slot appears in WhereInput (filtering via subquery)
def test_derived_slot_present_in_where_input():
    """The WhereInput type for a class with derived slots should have a field
    for the derived slot — filtering compiles the derivation as a subquery."""
    from knot.spec.compile.graphql import _make_class_where_type

    (spec, movie_cls, *_) = _build_full_spec()
    where_type = _make_class_where_type(movie_cls)
    assert hasattr(where_type, "directors"), (
        "Derived slot 'directors' must be in WhereInput (subquery filter)"
    )
    assert hasattr(where_type, "imdb_id"), "Stored slot 'imdb_id' must be in WhereInput"
