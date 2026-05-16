"""knot.compile.constraints — validation SELECT emission via the
semantic builder. Class-qualified slot references are produced by the
builder directly (e.g. ``movie.col.year`` → ``Ref("Movie", "year")``);
``Ref.to_sql`` does the schema/target-suffix qualification.
"""

import sqlglot

from knot import Spec, types
from knot.compile import emit_validation, emit_validation_union


def test_uniform_column_shape(movie_spec):
    for _, sql in emit_validation(movie_spec):
        parsed = sqlglot.parse_one(sql, dialect="postgres")
        cols = [e.alias_or_name for e in parsed.expressions]
        assert cols == ["rule_id", "class_name", "severity", "message", "offending_pk"]


def test_message_null_when_unset(movie_spec):
    rewrites = dict(emit_validation(movie_spec))
    assert "NULL AS message" in rewrites["year_sane"]


def test_message_literal_when_set():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.add_constraint(
        "y",
        body=movie.col.year > 0,
        message="must be positive",
    )
    rewrites = dict(emit_validation(spec))
    assert "'must be positive' AS message" in rewrites["y"]


def test_apostrophe_in_message_escaped():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.add_constraint(
        "y",
        body=movie.col.year > 0,
        message="director's pick",
    )
    rewrites = dict(emit_validation(spec))
    assert "'director''s pick'" in rewrites["y"]


def test_bare_column_unchanged(movie_spec):
    # year_sane is built as `movie.col.year >= 1888`, which renders with
    # the full schema-qualified path; "year >= 1888" appears as a
    # substring of the qualified form.
    rewrites = dict(emit_validation(movie_spec))
    assert "year >= 1888" in rewrites["year_sane"]


def test_class_ref_renders_qualified():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    movie.add_constraint(
        "has_director",
        body=movie.has_any(credit, role="director"),
    )

    # Default target_suffix='_resolved' — refs go to the resolved views.
    rewrites = dict(emit_validation(spec))
    sql = rewrites["has_director"]
    assert "knot_data.credit_resolved" in sql
    assert "knot_data.credit_resolved.movie" in sql
    assert "knot_data.credit_resolved.role" in sql
    assert "knot_data.movie_resolved.canonical_id" in sql

    # target_suffix='' — canonical-table targeting.
    rewrites_canonical = dict(emit_validation(spec, target_suffix=""))
    sql_c = rewrites_canonical["has_director"]
    assert "knot_data.credit.movie" in sql_c
    assert "knot_data.movie.canonical_id" in sql_c
    assert "_resolved" not in sql_c


def test_has_count_in_predicate():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    credit = spec.add_class("Credit")
    credit.slot("movie", movie)
    movie.add_constraint(
        "min_three_credits",
        body=movie.has_count(credit) >= 3,
    )
    rewrites = dict(emit_validation(spec))
    sql = rewrites["min_three_credits"]
    assert "SELECT COUNT(*) FROM knot_data.credit_resolved" in sql
    assert ") >= 3" in sql


def test_boolean_composition():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.slot("runtime", types.INTEGER)
    movie.add_constraint(
        "year_and_runtime",
        body=(movie.col.year >= 1888) & (movie.col.runtime > 0),
    )
    rewrites = dict(emit_validation(spec))
    sql = rewrites["year_and_runtime"]
    assert " AND " in sql
    assert "year >= 1888" in sql
    assert "runtime > 0" in sql


def test_emit_validation_union_for_non_empty_spec(movie_spec):
    u = emit_validation_union(movie_spec)
    assert u is not None
    sqlglot.parse_one(u, dialect="postgres")


def test_emit_validation_union_empty_spec():
    spec = Spec(id="e", version="0.1")
    spec.add_class("Movie")
    assert emit_validation_union(spec) is None
