"""Pure-Python tests for the SQL compiler (src/knot/spec/compile/postgres/).

Covers each predicate handler's compile-output: Literal_, PropertyPath,
Compare, BoolExpr, Within, Between, Matches, RelationAll/RelationAny.
All assertions are over rendered SQL strings and ``CompileContext`` state
— no DB connection required.

Execute-against-DB tests for ``compile_constraint`` end-to-end and the
publish-gate constraint pass live in
``tests/integration/spec/compile/test_sql_compiler_execution.py``.
"""

from __future__ import annotations

import pytest

from knot.spec.compile.postgres import (
    CompileContext,
    CompilerError,
    compile_predicate,
)
from knot.spec.metaschema import (
    Between,
    BoolExpr,
    BoolOpKind,
    ClassRef,
    Compare,
    CompareOp,
    Literal_,
    Matches,
    OntologyClass,
    Primitive,
    RelationAll,
    RelationAny,
    RelationRef,
    Property,
    PropertyPath,
    Within,
)


def _make_ctx(cls: OntologyClass) -> CompileContext:
    return CompileContext(primary_class=cls, alias="s")


# ---------------------------------------------------------------------------
# 1. Literal_ handler
# ---------------------------------------------------------------------------


def test_literal_emits_placeholder_and_pushes_param():
    prop = Property(name="imdb_id", type=Primitive(name="string"), identifier=True)
    cls = OntologyClass(name="Movie", properties=[prop])
    ctx = _make_ctx(cls)

    node = Literal_(value=42)
    result = compile_predicate(node, ctx)

    from psycopg import sql

    assert isinstance(result, sql.Composable)
    assert ctx.params == [42]


# ---------------------------------------------------------------------------
# 2. PropertyPath handler — valid prop on primary class
# ---------------------------------------------------------------------------


def test_slot_path_emits_alias_dot_col():
    imdb_id = Property(name="imdb_id", type=Primitive(name="string"), identifier=True)
    cls = OntologyClass(name="Movie", properties=[imdb_id])
    ctx = _make_ctx(cls)

    node = PropertyPath(from_class=cls, properties=[imdb_id])
    result = compile_predicate(node, ctx)

    # No params pushed for a column reference.
    assert ctx.params == []
    # The composed SQL when rendered should include the alias and column.
    rendered = result.as_string(None)
    assert '"s"."imdb_id"' in rendered


# ---------------------------------------------------------------------------
# 3. PropertyPath rejected when prop is not on primary class
# ---------------------------------------------------------------------------


def test_slot_path_rejects_foreign_slot():
    slot_a = Property(name="col_a", type=Primitive(name="string"))
    slot_b = Property(name="col_b", type=Primitive(name="string"))
    cls_a = OntologyClass(name="A", properties=[slot_a])
    cls_b = OntologyClass(name="B", properties=[slot_b])
    ctx = _make_ctx(cls_a)

    # slot_b is on cls_b, not cls_a → should raise
    node = PropertyPath(from_class=cls_b, properties=[slot_b])
    with pytest.raises(CompilerError, match="not on the primary class"):
        compile_predicate(node, ctx)


# ---------------------------------------------------------------------------
# 4. Compare — binary operators
# ---------------------------------------------------------------------------


def test_compare_eq_emits_equals():
    prop = Property(name="title", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", properties=[prop])
    ctx = _make_ctx(cls)

    node = Compare(
        op=CompareOp.EQ,
        left=PropertyPath(from_class=cls, properties=[prop]),
        right=Literal_(value="Inception"),
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "=" in rendered
    assert ctx.params == ["Inception"]


def test_compare_gte_emits_gte():
    year = Property(name="year", type=Primitive(name="integer"))
    cls = OntologyClass(name="Movie", properties=[year])
    ctx = _make_ctx(cls)

    node = Compare(
        op=CompareOp.GTE,
        left=PropertyPath(from_class=cls, properties=[year]),
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
    prop = Property(name="title", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", properties=[prop])
    ctx = _make_ctx(cls)

    node = Compare(op=CompareOp.IS_NULL, left=PropertyPath(from_class=cls, properties=[prop]))
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "IS NULL" in rendered
    assert ctx.params == []  # no params for unary


def test_compare_is_not_null_emits_is_not_null():
    prop = Property(name="title", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", properties=[prop])
    ctx = _make_ctx(cls)

    node = Compare(op=CompareOp.IS_NOT_NULL, left=PropertyPath(from_class=cls, properties=[prop]))
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "IS NOT NULL" in rendered
    assert ctx.params == []


# ---------------------------------------------------------------------------
# 6. Compare — IN  (= ANY)
# ---------------------------------------------------------------------------


def test_compare_in_emits_any():
    prop = Property(name="status", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", properties=[prop])
    ctx = _make_ctx(cls)

    node = Compare(
        op=CompareOp.IN,
        left=PropertyPath(from_class=cls, properties=[prop]),
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
    year = Property(name="year", type=Primitive(name="integer"))
    cls = OntologyClass(name="Movie", properties=[year])
    ctx = _make_ctx(cls)

    node = BoolExpr(
        op=BoolOpKind.AND,
        operands=[
            Compare(
                op=CompareOp.GTE,
                left=PropertyPath(from_class=cls, properties=[year]),
                right=Literal_(value=1888),
            ),
            Compare(
                op=CompareOp.LTE,
                left=PropertyPath(from_class=cls, properties=[year]),
                right=Literal_(value=2100),
            ),
        ],
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "AND" in rendered
    assert ctx.params == [1888, 2100]


def test_bool_expr_not():
    year = Property(name="year", type=Primitive(name="integer"))
    cls = OntologyClass(name="Movie", properties=[year])
    ctx = _make_ctx(cls)

    node = BoolExpr(
        op=BoolOpKind.NOT,
        operands=[
            Compare(
                op=CompareOp.IS_NULL,
                left=PropertyPath(from_class=cls, properties=[year]),
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
    genre = Property(name="genre", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", properties=[genre])
    ctx = _make_ctx(cls)

    node = Within(
        left=PropertyPath(from_class=cls, properties=[genre]),
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
    year = Property(name="year", type=Primitive(name="integer"))
    cls = OntologyClass(name="Movie", properties=[year])
    ctx = _make_ctx(cls)

    node = Between(
        left=PropertyPath(from_class=cls, properties=[year]),
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
    title = Property(name="title", type=Primitive(name="string"))
    cls = OntologyClass(name="Movie", properties=[title])
    ctx = _make_ctx(cls)

    node = Matches(
        left=PropertyPath(from_class=cls, properties=[title]),
        pattern="The%",
    )
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "LIKE" in rendered
    assert ctx.params == ["The%"]


# ---------------------------------------------------------------------------
# 11. RelationAll / RelationAny — error cases + non-EXISTS shape
# ---------------------------------------------------------------------------


def test_relation_all_no_body_raises_compiler_error():
    """RelationAll with body=None should raise CompilerError."""
    credit_cls = OntologyClass(name="Credit", properties=[])
    fk_property = Property(name="credits", type=ClassRef(target_class=credit_cls))
    movie = OntologyClass(name="Movie", properties=[fk_property])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, property=fk_property)
    node = RelationAll(relation=ref, body=None)
    with pytest.raises(CompilerError, match="body must be provided"):
        compile_predicate(node, ctx)


def test_relation_all_non_class_ranged_slot_raises():
    """RelationAll whose prop type is Primitive (not ClassRef) raises."""
    bad_slot = Property(name="title", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", properties=[bad_slot])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, property=bad_slot)
    body = Compare(
        op=CompareOp.IS_NOT_NULL,
        left=PropertyPath(from_class=movie, properties=[bad_slot]),
    )
    node = RelationAll(relation=ref, body=body)
    with pytest.raises(CompilerError, match="cannot be traversed"):
        compile_predicate(node, ctx)


def test_relation_all_emits_not_exists():
    """RelationAll emits NOT EXISTS with NOT predicate fragment."""
    role_slot = Property(name="role", type=Primitive(name="string"))
    credit_cls = OntologyClass(name="Credit", properties=[role_slot])
    fk_property = Property(name="credits", type=ClassRef(target_class=credit_cls))
    movie = OntologyClass(name="Movie", properties=[fk_property])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, property=fk_property)
    body = Compare(
        op=CompareOp.IS_NOT_NULL,
        left=PropertyPath(from_class=credit_cls, properties=[role_slot]),
    )
    node = RelationAll(relation=ref, body=body)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "NOT EXISTS" in rendered
    assert "NOT" in rendered


def test_relation_any_emits_exists():
    """RelationAny emits EXISTS."""
    credit_cls = OntologyClass(name="Credit", properties=[])
    fk_property = Property(name="credits", type=ClassRef(target_class=credit_cls))
    movie = OntologyClass(name="Movie", properties=[fk_property])
    ctx = _make_ctx(movie)

    ref = RelationRef(from_class=movie, property=fk_property)
    node = RelationAny(relation=ref)
    result = compile_predicate(node, ctx)
    rendered = result.as_string(None)
    assert "EXISTS" in rendered


def test_compile_context_with_subquery_alias_shares_params():
    """with_subquery_alias returns a child context sharing the params list."""
    prop = Property(name="title", type=Primitive(name="string"))
    cls_a = OntologyClass(name="A", properties=[prop])
    cls_b = OntologyClass(name="B", properties=[prop])
    ctx = CompileContext(primary_class=cls_a, alias="s")

    child = ctx.with_subquery_alias(cls_b, "t")
    assert child.primary_class is cls_b
    assert child.alias == "t"
    # Shared params list — mutating child affects parent.
    child.params.append(42)
    assert ctx.params == [42]


# ---------------------------------------------------------------------------
# ClassRef traversal
# ---------------------------------------------------------------------------


def test_relation_any_with_class_ref_traverses_to_target():
    """A prop with type=ClassRef(target_class=Movie) is traversable via RelationAny."""
    imdb_id = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", properties=[imdb_id])

    fk_property = Property(name="movie_ref", type=ClassRef(target_class=movie))
    review = OntologyClass(name="Review", properties=[fk_property])
    ctx = _make_ctx(review)

    ref = RelationRef(from_class=review, property=fk_property)
    node = RelationAny(relation=ref)
    rendered = compile_predicate(node, ctx).as_string(None)

    assert "EXISTS" in rendered
    # JOIN should hit Movie's bindings.
    assert 'knot_data."movie_bindings"' in rendered or '"knot_data"."movie_bindings"' in rendered


def test_relation_any_non_classref_slot_raises():
    """A prop with type=Primitive cannot be traversed — should raise CompilerError."""
    bad_slot = Property(name="ref_key", type=Primitive(name="string"))
    tag = OntologyClass(name="Tag", properties=[bad_slot])
    ctx = _make_ctx(tag)

    ref = RelationRef(from_class=tag, property=bad_slot)
    node = RelationAny(relation=ref)
    with pytest.raises(CompilerError, match="cannot be traversed"):
        compile_predicate(node, ctx)
