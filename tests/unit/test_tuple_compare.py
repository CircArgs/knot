"""Feature 2 — TupleCompare AST node and tuple_lt/le/gt/ge factories."""

import pytest
import sqlglot

from knot import Spec, types
from knot.ast.expr import TupleCompare, tuple_ge, tuple_gt, tuple_le, tuple_lt
from knot.ast.select import Layer
from knot.compile.expr import compile_sql


def _refs(spec: Spec, cls_name: str, *slot_names: str):
    cls = spec.classes[cls_name]
    return [cls.col[n] for n in slot_names]


def _simple_spec() -> Spec:
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.slot("title", types.TEXT)
    spec.add_source("imdb").bind(movie)
    return spec


# ---------------------------------------------------------------------------
# Construction-time validation
# ---------------------------------------------------------------------------


def test_arity_mismatch_raises():
    spec = _simple_spec()
    year, title = _refs(spec, "Movie", "year", "title")
    with pytest.raises(ValueError, match="arity mismatch"):
        TupleCompare(op="<", lefts=(year,), rights=(1, 2))  # type: ignore[arg-type]


def test_empty_lefts_raises():
    with pytest.raises(ValueError, match="at least one"):
        TupleCompare(op="<", lefts=(), rights=())


def test_bad_op_raises():
    spec = _simple_spec()
    (year,) = _refs(spec, "Movie", "year")
    with pytest.raises(ValueError, match="lex-order"):
        TupleCompare(op="=", lefts=(year,), rights=(year,))


# ---------------------------------------------------------------------------
# SQL rendering
# ---------------------------------------------------------------------------


def test_tuple_lt_renders_sql():
    spec = _simple_spec()
    year, title = _refs(spec, "Movie", "year", "title")
    node = tuple_lt([year, title], [2020, "Z"])
    sql = compile_sql(node, schema="kd", layer=Layer.RESOLVED)
    assert sql == "(kd.movie_resolved.year, kd.movie_resolved.title) < (2020, 'Z')"


def test_tuple_gt_renders_sql():
    spec = _simple_spec()
    (year,) = _refs(spec, "Movie", "year")
    node = tuple_gt([year], [1900])
    sql = compile_sql(node, schema="kd", layer=Layer.RESOLVED)
    assert sql == "(kd.movie_resolved.year) > (1900)"


def test_sqlglot_parses_tuple_compare():
    spec = _simple_spec()
    year, title = _refs(spec, "Movie", "year", "title")
    node = tuple_le([year, title], [2020, "Z"])
    sql = compile_sql(node, schema="kd", layer=Layer.RESOLVED)
    # Embed in a WHERE to make it a full statement sqlglot can parse.
    full = f"SELECT 1 FROM kd.movie_resolved WHERE {sql}"
    parsed = sqlglot.parse_one(full, dialect="postgres")
    assert parsed is not None


def test_factories_set_correct_op():
    spec = _simple_spec()
    (year,) = _refs(spec, "Movie", "year")
    assert tuple_lt([year], [1]).op == "<"
    assert tuple_le([year], [1]).op == "<="
    assert tuple_gt([year], [1]).op == ">"
    assert tuple_ge([year], [1]).op == ">="
