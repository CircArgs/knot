"""Feature 3 — FkRef.target_exists() / TargetExists node."""

import sqlglot

from knot import Spec, types
from knot.ast.expr import TargetExists
from knot.ast.select import Layer
from knot.compile.expr import compile_sql


def _fk_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT)
    credit.slot("movie", movie)
    return spec, movie, credit


def test_target_exists_via_fkref():
    spec, movie, credit = _fk_spec()
    node = credit.col.movie.target_exists()
    assert isinstance(node, TargetExists)
    assert node.fk_class_name == "Credit"
    assert node.fk_slot_name == "movie"
    assert node.target_class_name == "Movie"
    assert node.negated is False


def test_target_exists_sql_renders_exists():
    spec, movie, credit = _fk_spec()
    node = credit.col.movie.target_exists()
    sql = compile_sql(node, schema="kd", layer=Layer.RESOLVED)
    assert sql.startswith("EXISTS")
    assert "kd.movie_resolved" in sql
    assert "kd.movie_resolved.canonical_id = kd.credit_resolved.movie" in sql


def test_target_exists_negated_via_invert():
    spec, movie, credit = _fk_spec()
    node = ~credit.col.movie.target_exists()
    assert node.negated is True
    sql = compile_sql(node, schema="kd", layer=Layer.RESOLVED)
    assert sql.startswith("NOT EXISTS")


def test_target_exists_in_where_composes():
    spec, movie, credit = _fk_spec()
    # Verify it produces valid SQL when embedded in a WHERE predicate.
    node = credit.col.movie.target_exists()
    sql = compile_sql(node, schema="knot_data", layer=Layer.RESOLVED)
    full = f"SELECT 1 FROM knot_data.credit_resolved WHERE {sql}"
    parsed = sqlglot.parse_one(full, dialect="postgres")
    assert parsed is not None


def test_target_exists_sqlglot_parses():
    spec, movie, credit = _fk_spec()
    node = credit.col.movie.target_exists()
    sql = compile_sql(node, schema="kd", layer=Layer.RESOLVED)
    full = f"SELECT 1 FROM kd.credit_resolved WHERE {sql}"
    sqlglot.parse_one(full, dialect="postgres")
