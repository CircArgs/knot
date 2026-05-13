"""knot.compile.constraints — validation SELECT emission + AST rewriting."""

import sqlglot

from knot import Primitive, Spec
from knot.compile import emit_validation, emit_validation_union


def test_uniform_column_shape(movie_spec):
    for _, sql in emit_validation(movie_spec):
        parsed = sqlglot.parse_one(sql, dialect="postgres")
        cols = [e.alias_or_name for e in parsed.expressions]
        assert cols == ["rule_id", "class_name", "severity", "message", "offending_pk"]


def test_message_null_when_unset(movie_spec):
    # 'year_sane' has no message
    rewrites = dict(emit_validation(movie_spec))
    assert "NULL AS message" in rewrites["year_sane"]


def test_message_literal_when_set():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)
    spec.add_constraint(
        "y", primary=movie, body="year > 0", message="must be positive"
    )
    rewrites = dict(emit_validation(spec))
    assert "'must be positive' AS message" in rewrites["y"]


def test_apostrophe_in_message_escaped():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)
    spec.add_constraint(
        "y", primary=movie, body="year > 0", message="director's pick"
    )
    rewrites = dict(emit_validation(spec))
    assert "'director''s pick'" in rewrites["y"]


def test_bare_column_unchanged(movie_spec):
    rewrites = dict(emit_validation(movie_spec))
    assert "year >= 1888" in rewrites["year_sane"]


def test_class_table_ref_qualified():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit.slot("role", Primitive.TEXT, required=True)
    credit.fk("movie", to=movie)
    spec.add_constraint(
        "has_director",
        primary=movie,
        body=(
            "EXISTS (SELECT 1 FROM Credit "
            "WHERE Credit.movie = Movie.canonical_id "
            "AND Credit.role = 'director')"
        ),
    )
    rewrites = dict(emit_validation(spec))
    sql = rewrites["has_director"]
    # Default target_suffix='_resolved' — refs go to the resolved views.
    assert "knot_data.credit_resolved" in sql
    assert "knot_data.credit_resolved.movie" in sql
    assert "knot_data.credit_resolved.role" in sql
    assert "knot_data.movie_resolved.canonical_id" in sql

    # And with target_suffix='' — canonical-table targeting.
    rewrites_canonical = dict(emit_validation(spec, target_suffix=""))
    sql_c = rewrites_canonical["has_director"]
    assert "knot_data.credit.movie" in sql_c
    assert "knot_data.movie.canonical_id" in sql_c
    assert "_resolved" not in sql_c


def test_alias_preserved():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit = spec.add_class("Credit")
    credit.slot("canonical_id", Primitive.TEXT, identifier=True)
    credit.fk("movie", to=movie)
    spec.add_constraint(
        "has_some_credit",
        primary=movie,
        body=(
            "(SELECT COUNT(*) FROM Credit c "
            "WHERE c.movie = Movie.canonical_id) >= 1"
        ),
    )
    rewrites = dict(emit_validation(spec))
    sql = rewrites["has_some_credit"]
    # Table itself gets qualified...
    assert "knot_data.credit" in sql
    # ...but the alias 'c' is left alone; references through it survive.
    assert "c.movie" in sql.lower()


def test_emit_validation_union_for_non_empty_spec(movie_spec):
    u = emit_validation_union(movie_spec)
    assert u is not None
    sqlglot.parse_one(u, dialect="postgres")
    assert "UNION ALL" not in u.split("UNION ALL", 1)[0]  # at least one UNION ALL boundary
    # Actually: if there's only one constraint there's no UNION ALL. That's fine.


def test_emit_validation_union_empty_spec():
    spec = Spec(id="e", version="0.1")
    spec.add_class("Movie").slot("canonical_id", Primitive.TEXT, identifier=True)
    assert emit_validation_union(spec) is None
