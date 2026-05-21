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
    spec = Spec(identifier_slot_name="canonical_id")
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
    spec = Spec(identifier_slot_name="canonical_id")
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
    from knot import this

    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    credit = spec.add_class("Credit")
    credit.slot("role", types.TEXT, required=True)
    credit.slot("movie", movie)
    movie.add_constraint(
        "has_director",
        body=((credit.col.movie == this.Movie) & (credit.col.role == "director")).any(),
    )

    # Default layer=Layer.RESOLVED — refs go to the resolved views.
    rewrites = dict(emit_validation(spec))
    sql = rewrites["has_director"]
    assert "knot_data.credit_resolved" in sql
    assert "knot_data.credit_resolved.movie" in sql
    assert "knot_data.credit_resolved.role" in sql
    assert "knot_data.movie_resolved.canonical_id" in sql


def test_count_in_predicate():
    from knot import this

    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    credit = spec.add_class("Credit")
    credit.slot("movie", movie)
    movie.add_constraint(
        "min_three_credits",
        body=(credit.col.movie == this.Movie).count() >= 3,
    )
    rewrites = dict(emit_validation(spec))
    sql = rewrites["min_three_credits"]
    assert "SELECT COUNT(*) FROM knot_data.credit_resolved" in sql
    assert ") >= 3" in sql


def test_boolean_composition():
    spec = Spec(identifier_slot_name="canonical_id")
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
    spec = Spec(identifier_slot_name="canonical_id")
    spec.add_class("Movie")
    assert emit_validation_union(spec) is None


# ---------------------------------------------------------------------------
# scope_to_source_identifiers — delta-only validation
# ---------------------------------------------------------------------------


def _constraint_spec():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.add_constraint("year_sane", body=movie.col.year >= 1888)
    return spec


def test_scope_none_produces_unscoped_sql():
    spec = _constraint_spec()
    rewrites = dict(emit_validation(spec, scope_to_source_identifiers=None))
    sql = rewrites["year_sane"]
    assert "IN (" not in sql
    assert "canonical_id IS NOT NULL" not in sql


def test_scope_empty_dict_equals_none():
    spec = _constraint_spec()
    unscoped = dict(emit_validation(spec))["year_sane"]
    scoped = dict(emit_validation(spec, scope_to_source_identifiers={}))["year_sane"]
    assert unscoped == scoped


def test_scope_single_source_inlines_subquery():
    spec = _constraint_spec()
    rewrites = dict(
        emit_validation(
            spec,
            scope_to_source_identifiers={"imdb": ["tt001", "tt002"]},
        )
    )
    sql = rewrites["year_sane"]
    assert "canonical_id IN" in sql
    assert "movie_bindings" in sql
    assert "('imdb', 'tt001')" in sql
    assert "('imdb', 'tt002')" in sql
    assert "canonical_id IS NOT NULL" in sql
    sqlglot.parse_one(sql, dialect="postgres")


def test_scope_multi_source_inlines_all_pairs():
    spec = _constraint_spec()
    rewrites = dict(
        emit_validation(
            spec,
            scope_to_source_identifiers={
                "imdb": ["tt001"],
                "tmdb": ["m999"],
            },
        )
    )
    sql = rewrites["year_sane"]
    assert "('imdb', 'tt001')" in sql
    assert "('tmdb', 'm999')" in sql
    sqlglot.parse_one(sql, dialect="postgres")


# ---------------------------------------------------------------------------
# built-in constraints — auto-shipped from spec shape
# ---------------------------------------------------------------------------


def _fk_spec():
    """Person, Movie (director FK), Movie.title required."""
    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT, required=True)
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("director", person)
    return spec, person, movie


def test_builtin_fk_orphan_constraint_emitted():
    """Every ClassRef slot gets a `_builtin_fk_orphan_<Class>_<slot>`
    validation that catches non-null FKs pointing at no canonical_id."""
    spec, _person, _movie = _fk_spec()
    names = dict(emit_validation(spec)).keys()
    assert "_builtin_fk_orphan_Movie_director" in names


def test_builtin_required_null_constraint_emitted():
    """Every required non-identifier slot gets a
    `_builtin_required_null_<Class>_<slot>` validation."""
    spec, _person, _movie = _fk_spec()
    names = dict(emit_validation(spec)).keys()
    assert "_builtin_required_null_Movie_title" in names
    assert "_builtin_required_null_Person_name" in names


def test_builtin_skipped_for_non_required_non_fk():
    """Non-required, non-FK slots get no built-in. ``Movie.year``
    is INTEGER non-required → no built-in."""
    spec, _person, _movie = _fk_spec()
    names = dict(emit_validation(spec)).keys()
    assert "_builtin_required_null_Movie_year" not in names
    assert "_builtin_fk_orphan_Movie_year" not in names


def test_builtin_skipped_for_identifier_slot():
    """The identifier slot is required by convention but has its own
    NULL handling (resolved view filters); no built-in for it."""
    spec, _person, _movie = _fk_spec()
    names = dict(emit_validation(spec)).keys()
    assert "_builtin_required_null_Movie_canonical_id" not in names


def test_include_builtins_false_drops_them():
    """The opt-out kwarg returns only user-declared constraints."""
    spec, _person, movie = _fk_spec()
    movie.add_constraint("year_sane", body=movie.col.year >= 1888)
    names = dict(emit_validation(spec, include_builtins=False)).keys()
    assert names == {"year_sane"}


def test_fk_orphan_body_allows_null_fk():
    """A null FK is valid (pre-ER state or genuinely unset).
    The SQL should compile such that null FKs aren't flagged."""
    spec, _person, _movie = _fk_spec()
    rewrites = dict(emit_validation(spec))
    sql = rewrites["_builtin_fk_orphan_Movie_director"]
    sqlglot.parse_one(sql, dialect="postgres")
    # Body is `is_null() | target_exists()`; WHERE NOT (...) inverts.
    assert "IS NULL" in sql
    assert "EXISTS" in sql


def test_builtin_constraints_have_error_or_warning_severity():
    """FK orphans are ERROR (structural); required-null are WARNING
    (host may tolerate during early ingest)."""
    spec, _person, _movie = _fk_spec()
    rewrites = dict(emit_validation(spec))
    fk_sql = rewrites["_builtin_fk_orphan_Movie_director"]
    req_sql = rewrites["_builtin_required_null_Movie_title"]
    assert "'error'" in fk_sql
    assert "'warning'" in req_sql
