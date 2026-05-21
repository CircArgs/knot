"""types.ENUM — construction, DDL emission, validate_rows_sql, expr composition."""

from __future__ import annotations

import pytest
import sqlglot

from knot import Spec, types
from knot.ast.types import Enum
from knot.compile import emit_ddl, emit_validate_rows_sql

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot(
        "role", types.ENUM("director", "actor", "writer", "producer"), required=True
    )
    movie.slot("status", types.ENUM("active", "archived"))  # optional
    imdb = spec.add_source("imdb")
    binding = imdb.bind(movie)
    return spec, movie, binding


# ---------------------------------------------------------------------------
# Construction validation
# ---------------------------------------------------------------------------


def test_enum_empty_raises():
    with pytest.raises(ValueError, match="at least one value"):
        types.ENUM()


def test_enum_non_string_raises():
    with pytest.raises(TypeError, match="must all be strings"):
        Enum(values=(1, "actor"))  # type: ignore[arg-type]


def test_enum_duplicate_raises():
    with pytest.raises(ValueError, match="unique"):
        types.ENUM("director", "actor", "director")


def test_enum_single_value_ok():
    e = types.ENUM("only")
    assert e.values == ("only",)


def test_enum_construction_ok():
    e = types.ENUM("director", "actor", "writer")
    assert isinstance(e, Enum)
    assert e.values == ("director", "actor", "writer")


# ---------------------------------------------------------------------------
# DDL emission
# ---------------------------------------------------------------------------


def test_enum_ddl_emits_text_check():
    spec, movie, binding = _make_spec()
    stmts = emit_ddl(spec)
    bindings_ddl = next(
        s for s in stmts if "movie_bindings" in s and "CREATE TABLE" in s
    )
    # required enum slot
    assert "role text CHECK" in bindings_ddl
    assert "'director'" in bindings_ddl
    assert "'actor'" in bindings_ddl
    assert "'writer'" in bindings_ddl
    assert "'producer'" in bindings_ddl
    # optional (nullable) enum slot — must allow NULL
    assert "status text CHECK" in bindings_ddl
    assert "status IS NULL OR status IN" in bindings_ddl


def test_enum_ddl_single_value():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("kind", types.ENUM("only"), required=True)
    spec.add_source("s").bind(cls)
    stmts = emit_ddl(spec)
    ddl = next(s for s in stmts if "thing_bindings" in s and "CREATE TABLE" in s)
    assert "kind text CHECK" in ddl
    assert "'only'" in ddl


def test_enum_ddl_parses_postgres():
    spec, movie, binding = _make_spec()
    stmts = emit_ddl(spec)
    for stmt in stmts:
        if "movie_bindings" in stmt and "CREATE TABLE" in stmt:
            sqlglot.parse_one(stmt, dialect="postgres")


# ---------------------------------------------------------------------------
# validate_rows_sql includes enum membership check
# ---------------------------------------------------------------------------


def test_validate_rows_sql_includes_enum_check():
    spec, movie, binding = _make_spec()
    sql = emit_validate_rows_sql(binding)
    # The NOT IN check should appear for required enum slot
    assert "NOT IN" in sql
    assert "'director'" in sql
    assert "'actor'" in sql


def test_validate_rows_sql_parses_postgres():
    spec, movie, binding = _make_spec()
    sql = emit_validate_rows_sql(binding)
    sqlglot.parse_one(sql, dialect="postgres")


# ---------------------------------------------------------------------------
# Expr composition — enum slot usable in predicates
# ---------------------------------------------------------------------------


def test_enum_slot_col_is_comparable():
    spec, movie, binding = _make_spec()
    # .col access should return a Ref (comparable via _ValueExpr operators)
    pred = movie.col.role == "director"
    from knot.ast.expr import Compare

    assert isinstance(pred, Compare)


def test_enum_slot_in_where_clause():
    spec, movie, binding = _make_spec()
    q = movie.resolved.where(movie.col.role == "director")
    sql = q.sql()
    assert "role = 'director'" in sql


def test_non_required_enum_slot_allows_null_check_in_ddl():
    """Optional enum slot DDL allows NULL via the IS NULL branch."""
    spec, movie, binding = _make_spec()
    stmts = emit_ddl(spec)
    ddl = next(s for s in stmts if "movie_bindings" in s and "CREATE TABLE" in s)
    # status is optional — its CHECK must allow NULL
    assert "status IS NULL OR status IN" in ddl
