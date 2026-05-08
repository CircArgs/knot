"""Tests for the SQL compiler (src/knot/db/sql_compiler/).

Covers:
  - Each predicate handler: Literal_, SlotPath, Compare, BoolExpr, Within,
    Between, Matches.
  - compile_constraint end-to-end against the live test DB.
  - Error cases: SlotPath on wrong class, RelationAll/RelationAny stubs.
  - Publish gate step 4: ERROR severity blocks publish; WARNING doesn't.
"""

from __future__ import annotations

import pytest
import psycopg

from knot import db
from knot.db.spec_store import (
    PublishGateError,
    create_draft,
    publish_draft,
    update_draft,
)
from knot.db.sql_compiler import (
    CompileContext,
    CompilerError,
    compile_constraint,
    compile_predicate,
)
from knot.ontology.metaschema import (
    Between,
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
    DirectRef,
    DiscriminatedRef,
    Literal_,
    Matches,
    OntologyClass,
    RelationAll,
    RelationAny,
    RelationRef,
    Severity,
    Slot,
    SlotPath,
    Source,
    Spec,
    TypeDefinition,
    Within,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_db(pg_conn):
    """Truncate all state, re-apply schema, yield the connection."""
    pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    pg_conn.execute("TRUNCATE TABLE users CASCADE")
    pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()
    yield pg_conn


def _make_movie_spec(
    *, with_constraint: Constraint | None = None, extra_slots: list[Slot] | None = None
) -> tuple[Spec, OntologyClass, Slot, Slot, Source]:
    """Build a minimal Movie spec suitable for constraint tests."""
    str_t = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    imdb_id = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    year = Slot(name="year", range=int_t)
    title = Slot(name="title", range=str_t)
    all_slots = [imdb_id, year, title] + (extra_slots or [])
    movie = OntologyClass(name="Movie", slots=all_slots)
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id)
    constraints = [with_constraint] if with_constraint else []
    spec = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=all_slots,
        classes=[movie],
        sources=[src],
        constraints=constraints,
    )
    return spec, movie, imdb_id, year, src


def _make_ctx(cls: OntologyClass) -> CompileContext:
    return CompileContext(primary_class=cls, alias="s")


# ---------------------------------------------------------------------------
# 1. Literal_ handler
# ---------------------------------------------------------------------------


def test_literal_emits_placeholder_and_pushes_param():
    str_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="imdb_id", range=str_t, identifier=True)
    cls = OntologyClass(name="Movie", slots=[slot])
    ctx = _make_ctx(cls)

    node = Literal_(value=42)
    result = compile_predicate(node, ctx)

    from psycopg import sql
    assert isinstance(result, sql.Composable)
    assert ctx.params == [42]


# ---------------------------------------------------------------------------
# 2. SlotPath handler — valid slot on primary class
# ---------------------------------------------------------------------------


def test_slot_path_emits_alias_dot_col():
    str_t = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=str_t, identifier=True)
    cls = OntologyClass(name="Movie", slots=[imdb_id])
    ctx = _make_ctx(cls)

    node = SlotPath(from_class=cls, slots=[imdb_id])
    result = compile_predicate(node, ctx)

    # No params pushed for a column reference.
    assert ctx.params == []
    # The composed SQL when rendered should include the alias and column.
    rendered = result.as_string(None)
    assert '"s"."imdb_id"' in rendered


# ---------------------------------------------------------------------------
# 3. SlotPath rejected when slot is not on primary class
# ---------------------------------------------------------------------------


def test_slot_path_rejects_foreign_slot():
    str_t = TypeDefinition(name="string", base="str")
    slot_a = Slot(name="col_a", range=str_t)
    slot_b = Slot(name="col_b", range=str_t)
    cls_a = OntologyClass(name="A", slots=[slot_a])
    cls_b = OntologyClass(name="B", slots=[slot_b])
    ctx = _make_ctx(cls_a)

    # slot_b is on cls_b, not cls_a → should raise
    node = SlotPath(from_class=cls_b, slots=[slot_b])
    with pytest.raises(CompilerError, match="not on the primary class"):
        compile_predicate(node, ctx)


# ---------------------------------------------------------------------------
# 4. Compare — binary operators
# ---------------------------------------------------------------------------


def test_compare_eq_emits_equals():
    str_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="title", range=str_t)
    cls = OntologyClass(name="Movie", slots=[slot])
    ctx = _make_ctx(cls)

    node = Compare(
        op=CompareOp.EQ,
        left=SlotPath(from_class=cls, slots=[slot]),
        right=Literal_(value="Inception"),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "=" in rendered
    assert ctx.params == ["Inception"]


def test_compare_gte_emits_gte():
    int_t = TypeDefinition(name="integer", base="int")
    year = Slot(name="year", range=int_t)
    cls = OntologyClass(name="Movie", slots=[year])
    ctx = _make_ctx(cls)

    node = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=cls, slots=[year]),
        right=Literal_(value=1888),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert ">=" in rendered
    assert ctx.params == [1888]


# ---------------------------------------------------------------------------
# 5. Compare — unary IS NULL / IS NOT NULL
# ---------------------------------------------------------------------------


def test_compare_is_null_emits_is_null():
    str_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="title", range=str_t)
    cls = OntologyClass(name="Movie", slots=[slot])
    ctx = _make_ctx(cls)

    node = Compare(op=CompareOp.IS_NULL, left=SlotPath(from_class=cls, slots=[slot]))
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "IS NULL" in rendered
    assert ctx.params == []  # no params for unary


def test_compare_is_not_null_emits_is_not_null():
    str_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="title", range=str_t)
    cls = OntologyClass(name="Movie", slots=[slot])
    ctx = _make_ctx(cls)

    node = Compare(op=CompareOp.IS_NOT_NULL, left=SlotPath(from_class=cls, slots=[slot]))
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "IS NOT NULL" in rendered
    assert ctx.params == []


# ---------------------------------------------------------------------------
# 6. Compare — IN  (= ANY)
# ---------------------------------------------------------------------------


def test_compare_in_emits_any():
    str_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="status", range=str_t)
    cls = OntologyClass(name="Movie", slots=[slot])
    ctx = _make_ctx(cls)

    node = Compare(
        op=CompareOp.IN,
        left=SlotPath(from_class=cls, slots=[slot]),
        right=Literal_(value=["released", "upcoming"]),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "ANY" in rendered
    assert ctx.params == [["released", "upcoming"]]


# ---------------------------------------------------------------------------
# 7. BoolExpr — AND / OR / NOT
# ---------------------------------------------------------------------------


def test_bool_expr_and():
    int_t = TypeDefinition(name="integer", base="int")
    year = Slot(name="year", range=int_t)
    cls = OntologyClass(name="Movie", slots=[year])
    ctx = _make_ctx(cls)

    node = BoolExpr(
        op=BoolOpKind.AND,
        operands=[
            Compare(
                op=CompareOp.GTE,
                left=SlotPath(from_class=cls, slots=[year]),
                right=Literal_(value=1888),
            ),
            Compare(
                op=CompareOp.LTE,
                left=SlotPath(from_class=cls, slots=[year]),
                right=Literal_(value=2100),
            ),
        ],
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "AND" in rendered
    assert ctx.params == [1888, 2100]


def test_bool_expr_not():
    int_t = TypeDefinition(name="integer", base="int")
    year = Slot(name="year", range=int_t)
    cls = OntologyClass(name="Movie", slots=[year])
    ctx = _make_ctx(cls)

    node = BoolExpr(
        op=BoolOpKind.NOT,
        operands=[
            Compare(
                op=CompareOp.IS_NULL,
                left=SlotPath(from_class=cls, slots=[year]),
            )
        ],
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "NOT" in rendered
    assert ctx.params == []


# ---------------------------------------------------------------------------
# 8. Within  (= ANY with list)
# ---------------------------------------------------------------------------


def test_within_emits_any_with_list():
    str_t = TypeDefinition(name="string", base="str")
    genre = Slot(name="genre", range=str_t)
    cls = OntologyClass(name="Movie", slots=[genre])
    ctx = _make_ctx(cls)

    node = Within(
        left=SlotPath(from_class=cls, slots=[genre]),
        values=[Literal_(value="action"), Literal_(value="drama")],
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "ANY" in rendered
    assert ctx.params == [["action", "drama"]]


# ---------------------------------------------------------------------------
# 9. Between
# ---------------------------------------------------------------------------


def test_between_inclusive_emits_between():
    int_t = TypeDefinition(name="integer", base="int")
    year = Slot(name="year", range=int_t)
    cls = OntologyClass(name="Movie", slots=[year])
    ctx = _make_ctx(cls)

    node = Between(
        left=SlotPath(from_class=cls, slots=[year]),
        lower=Literal_(value=1888),
        upper=Literal_(value=2100),
        inclusive=True,
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "BETWEEN" in rendered
    assert ctx.params == [1888, 2100]


# ---------------------------------------------------------------------------
# 10. Matches  (LIKE)
# ---------------------------------------------------------------------------


def test_matches_emits_like():
    str_t = TypeDefinition(name="string", base="str")
    title = Slot(name="title", range=str_t)
    cls = OntologyClass(name="Movie", slots=[title])
    ctx = _make_ctx(cls)

    node = Matches(
        left=SlotPath(from_class=cls, slots=[title]),
        pattern="The%",
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "LIKE" in rendered
    assert ctx.params == ["The%"]


# ---------------------------------------------------------------------------
# 11. RelationAll / RelationAny — error cases (no body, non-class slot)
# ---------------------------------------------------------------------------


def test_relation_all_no_body_raises_compiler_error():
    """RelationAll with body=None should raise CompilerError."""
    str_t = TypeDefinition(name="string", base="str")
    credit_cls = OntologyClass(name="Credit", slots=[])
    fk_slot = Slot(name="credits", range=credit_cls)
    movie = OntologyClass(name="Movie", slots=[fk_slot])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, slot=fk_slot)
    node = RelationAll(relation=ref, body=None)
    with pytest.raises(CompilerError, match="body must be provided"):
        compile_predicate(node, ctx)


def test_relation_all_non_class_ranged_slot_raises():
    """RelationAll whose slot range is a TypeDefinition (not OntologyClass) raises."""
    str_t = TypeDefinition(name="string", base="str")
    bad_slot = Slot(name="title", range=str_t)
    movie = OntologyClass(name="Movie", slots=[bad_slot])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, slot=bad_slot)
    body = Compare(
        op=CompareOp.IS_NOT_NULL,
        left=SlotPath(from_class=movie, slots=[bad_slot]),
    )
    node = RelationAll(relation=ref, body=body)
    with pytest.raises(CompilerError, match="cannot be traversed"):
        compile_predicate(node, ctx)


def test_relation_all_emits_not_exists():
    """RelationAll emits NOT EXISTS with NOT predicate fragment."""
    str_t = TypeDefinition(name="string", base="str")
    role_slot = Slot(name="role", range=str_t)
    credit_cls = OntologyClass(name="Credit", slots=[role_slot])
    fk_slot = Slot(name="credits", range=credit_cls)
    movie = OntologyClass(name="Movie", slots=[fk_slot])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, slot=fk_slot)
    body = Compare(
        op=CompareOp.IS_NOT_NULL,
        left=SlotPath(from_class=credit_cls, slots=[role_slot]),
    )
    node = RelationAll(relation=ref, body=body)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "NOT EXISTS" in rendered
    assert "NOT" in rendered


def test_relation_any_emits_exists():
    """RelationAny emits EXISTS."""
    str_t = TypeDefinition(name="string", base="str")
    role_slot = Slot(name="role", range=str_t)
    credit_cls = OntologyClass(name="Credit", slots=[role_slot])
    fk_slot = Slot(name="credits", range=credit_cls)
    movie = OntologyClass(name="Movie", slots=[fk_slot])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, slot=fk_slot)
    node = RelationAny(relation=ref)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "EXISTS" in rendered


def test_compile_context_with_subquery_alias_shares_params():
    """with_subquery_alias returns a child context sharing the params list."""
    str_t = TypeDefinition(name="string", base="str")
    slot = Slot(name="title", range=str_t)
    cls_a = OntologyClass(name="A", slots=[slot])
    cls_b = OntologyClass(name="B", slots=[slot])
    ctx = CompileContext(primary_class=cls_a, alias="s")

    child = ctx.with_subquery_alias(cls_b, "t")
    assert child.primary_class is cls_b
    assert child.alias == "t"
    # Shared params list — mutating child affects parent.
    child.params.append(42)
    assert ctx.params == [42]


# ---------------------------------------------------------------------------
# 12. compile_constraint end-to-end: offending rows returned
# ---------------------------------------------------------------------------


def test_compile_constraint_catches_violating_rows(clean_db):
    """Publish a spec with a year-in-range constraint, ingest a violating row,
    then compile + execute the constraint and verify the offending row is returned
    in the uniform violation shape.
    """
    conn = clean_db

    # Build spec with constraint: year must be >= 1888 AND <= 2100
    str_t = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    imdb_id_slot = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    year_slot = Slot(name="year", range=int_t)
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id_slot)

    body = BoolExpr(
        op=BoolOpKind.AND,
        operands=[
            Compare(
                op=CompareOp.GTE,
                left=SlotPath(from_class=movie, slots=[year_slot]),
                right=Literal_(value=1888),
            ),
            Compare(
                op=CompareOp.LTE,
                left=SlotPath(from_class=movie, slots=[year_slot]),
                right=Literal_(value=2100),
            ),
        ],
    )
    constraint = Constraint(
        name="year_in_range",
        primary=movie,
        body=body,
        severity=Severity.ERROR,
    )
    spec = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[imdb_id_slot, year_slot],
        classes=[movie],
        sources=[src],
        constraints=[constraint],
    )

    # Publish the spec (constraint check skipped on first publish — no prev).
    rev = create_draft(conn)
    update_draft(conn, rev, spec)
    publish_draft(conn, rev)

    # Ingest one valid and one violating row directly via the store.
    from knot.db.graph_store import insert_rows
    insert_rows(
        conn,
        source=src,
        spec_revision=rev,
        rows=[
            {"imdb_id": "tt0000001", "year": 1972},   # valid
            {"imdb_id": "tt0000002", "year": 1800},   # violates year >= 1888
        ],
        canonical_ids=[str(r["imdb_id"]) for r in [
            {"imdb_id": "tt0000001", "year": 1972},   # valid
            {"imdb_id": "tt0000002", "year": 1800},   # violates year >= 1888
        ]]
    )

    # Compile the constraint and execute it.
    stmt, params = compile_constraint(constraint, movie)
    rows = conn.execute(stmt, params).fetchall()

    assert len(rows) == 1
    rule_id, class_name, slot_name, offending_pk, detail = rows[0]
    assert rule_id == "year_in_range"
    assert class_name == "Movie"
    assert slot_name is None
    assert offending_pk == "tt0000002"


def test_compile_constraint_no_violations(clean_db):
    """All rows valid → constraint returns zero offending rows."""
    conn = clean_db

    str_t = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    imdb_id_slot = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    year_slot = Slot(name="year", range=int_t)
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id_slot)

    body = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=movie, slots=[year_slot]),
        right=Literal_(value=1888),
    )
    constraint = Constraint(
        name="year_gte_1888",
        primary=movie,
        body=body,
        severity=Severity.ERROR,
    )
    spec = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[imdb_id_slot, year_slot],
        classes=[movie],
        sources=[src],
        constraints=[constraint],
    )

    rev = create_draft(conn)
    update_draft(conn, rev, spec)
    publish_draft(conn, rev)

    from knot.db.graph_store import insert_rows
    insert_rows(
        conn,
        source=src,
        spec_revision=rev,
        rows=[{"imdb_id": "tt0000001", "year": 2000}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000001", "year": 2000}]]
    )

    stmt, params = compile_constraint(constraint, movie)
    rows = conn.execute(stmt, params).fetchall()
    assert rows == []


# ---------------------------------------------------------------------------
# 13. Publish gate step 4: ERROR severity blocks publish
# ---------------------------------------------------------------------------


def test_publish_gate_blocks_error_constraint_on_existing_data(clean_db):
    """Publish v1 (no constraints), ingest violating row, publish v2 with an
    ERROR constraint → PublishGateError raised.
    """
    conn = clean_db

    str_t = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    imdb_id_slot = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    year_slot = Slot(name="year", range=int_t)
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id_slot)

    # v1: no constraints
    spec_v1 = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[imdb_id_slot, year_slot],
        classes=[movie],
        sources=[src],
    )
    rev1 = create_draft(conn)
    update_draft(conn, rev1, spec_v1)
    publish_draft(conn, rev1)

    # Ingest a row that will violate the upcoming constraint.
    from knot.db.graph_store import insert_rows
    insert_rows(
        conn,
        source=src,
        spec_revision=rev1,
        rows=[{"imdb_id": "tt0000001", "year": 1800}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000001", "year": 1800}]]  # violates >= 1888
    )

    # v2: add ERROR constraint that the ingested row violates.
    body = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=movie, slots=[year_slot]),
        right=Literal_(value=1888),
    )
    constraint = Constraint(
        name="year_gte_1888",
        primary=movie,
        body=body,
        severity=Severity.ERROR,
    )
    spec_v2 = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[imdb_id_slot, year_slot],
        classes=[movie],
        sources=[src],
        constraints=[constraint],
    )
    rev2 = create_draft(conn)
    update_draft(conn, rev2, spec_v2)

    with pytest.raises(PublishGateError, match="year_gte_1888"):
        publish_draft(conn, rev2)


# ---------------------------------------------------------------------------
# 14. Publish gate step 4: WARNING severity passes
# ---------------------------------------------------------------------------


def test_publish_gate_warning_constraint_allows_publish(clean_db):
    """Same setup but constraint is WARNING → publish succeeds."""
    conn = clean_db

    str_t = TypeDefinition(name="string", base="str")
    int_t = TypeDefinition(name="integer", base="int")
    imdb_id_slot = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    year_slot = Slot(name="year", range=int_t)
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb", entity_class=movie, identifier_slot=imdb_id_slot)

    spec_v1 = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[imdb_id_slot, year_slot],
        classes=[movie],
        sources=[src],
    )
    rev1 = create_draft(conn)
    update_draft(conn, rev1, spec_v1)
    publish_draft(conn, rev1)

    from knot.db.graph_store import insert_rows
    insert_rows(
        conn,
        source=src,
        spec_revision=rev1,
        rows=[{"imdb_id": "tt0000001", "year": 1800}],
        canonical_ids=[str(r["imdb_id"]) for r in [{"imdb_id": "tt0000001", "year": 1800}]]  # would violate >= 1888
    )

    body = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=movie, slots=[year_slot]),
        right=Literal_(value=1888),
    )
    constraint = Constraint(
        name="year_gte_1888",
        primary=movie,
        body=body,
        severity=Severity.WARNING,  # WARNING — should not block
    )
    spec_v2 = Spec(
        id="test",
        version="1.0.0",
        types=[str_t, int_t],
        slots=[imdb_id_slot, year_slot],
        classes=[movie],
        sources=[src],
        constraints=[constraint],
    )
    rev2 = create_draft(conn)
    update_draft(conn, rev2, spec_v2)

    # Should not raise.
    result = publish_draft(conn, rev2)
    assert result == rev2


# ---------------------------------------------------------------------------
# Slot.reference traversal — DirectRef and DiscriminatedRef with static target
# ---------------------------------------------------------------------------

def test_relation_any_with_direct_ref_target_class():
    """A slot with range=string + reference=DirectRef(target_class=Movie)
    is traversable: _target_class falls through to slot.reference.target_class."""
    str_t = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])

    # Outer slot: range=str_t, but reference says target is Movie.
    fk_slot = Slot(
        name="movie_imdb_id",
        range=str_t,
        reference=DirectRef(target_class=movie, fk_slot=imdb_id),
    )
    review = OntologyClass(name="Review", slots=[fk_slot])
    ctx = _make_ctx(review)

    ref = RelationRef(from_class=review, slot=fk_slot)
    node = RelationAny(relation=ref)
    rendered = compile_predicate(node, ctx).as_string(None)

    assert "EXISTS" in rendered
    # JOIN should hit Movie's bindings, not Review's.
    assert "knot_data.\"movie_bindings\"" in rendered or '"knot_data"."movie_bindings"' in rendered


def test_relation_any_with_discriminated_ref_target_class():
    """A slot with reference=DiscriminatedRef(target_class=Movie) traverses
    to Movie just like a DirectRef would (statically-known target slice)."""
    str_t = TypeDefinition(name="string", base="str")
    imdb_id = Slot(name="imdb_id", range=str_t, identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])

    entity_class = Slot(name="entity_class", range=str_t, required=True)
    entity_src_key = Slot(name="entity_src_key", range=str_t, required=True)

    fk_slot = Slot(
        name="ref_key",
        range=str_t,
        reference=DiscriminatedRef(
            target_class=movie,
            class_slot=entity_class,
            key_slot=entity_src_key,
        ),
    )
    tag = OntologyClass(name="Tag", slots=[fk_slot])
    ctx = _make_ctx(tag)

    ref = RelationRef(from_class=tag, slot=fk_slot)
    node = RelationAny(relation=ref)
    rendered = compile_predicate(node, ctx).as_string(None)

    assert "EXISTS" in rendered
    assert "knot_data.\"movie_bindings\"" in rendered or '"knot_data"."movie_bindings"' in rendered


def test_relation_any_discriminated_ref_without_target_raises():
    """DiscriminatedRef with target_class=None is true row-level polymorphism
    and isn't supported in this slice — should raise CompilerError."""
    str_t = TypeDefinition(name="string", base="str")
    entity_class = Slot(name="entity_class", range=str_t, required=True)
    entity_src_key = Slot(name="entity_src_key", range=str_t, required=True)

    fk_slot = Slot(
        name="ref_key",
        range=str_t,
        reference=DiscriminatedRef(
            target_class=None,
            class_slot=entity_class,
            key_slot=entity_src_key,
        ),
    )
    tag = OntologyClass(name="Tag", slots=[fk_slot])
    ctx = _make_ctx(tag)

    ref = RelationRef(from_class=tag, slot=fk_slot)
    node = RelationAny(relation=ref)
    with pytest.raises(CompilerError, match="cannot be traversed"):
        compile_predicate(node, ctx)
