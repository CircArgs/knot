"""knot.compile.explain — emit_explain_winner_sql unit tests."""

import pytest
import sqlglot

from knot import Spec, types
from knot.compile.explain import emit_explain_winner_sql

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_spec() -> tuple[Spec, object]:
    """Two-slot Movie spec: canonical_id (identifier) + year + title."""
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("year", types.INTEGER)
    return spec, movie


# ---------------------------------------------------------------------------
# 1. All-slots emission contains UNION ALL across each non-identifier slot
# ---------------------------------------------------------------------------


def test_all_slots_union_all_across_non_identifier_slots():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    # Two non-identifier slots → one UNION ALL between the two branches.
    assert sql.count("UNION ALL") == 1
    assert "'title' AS slot_name" in sql
    assert "'year' AS slot_name" in sql


def test_all_slots_with_many_slots():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)
    sql = emit_explain_winner_sql(movie)
    # Three non-identifier slots → two UNION ALLs.
    assert sql.count("UNION ALL") == 2
    assert "'title' AS slot_name" in sql
    assert "'year' AS slot_name" in sql
    assert "'runtime_minutes' AS slot_name" in sql


# ---------------------------------------------------------------------------
# 2. Identifier slot is skipped
# ---------------------------------------------------------------------------


def test_identifier_slot_not_in_output():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    # canonical_id is the identifier — it must NOT appear as a slot_name literal.
    assert "'canonical_id' AS slot_name" not in sql
    # But the canonical_id column name appears as the partition key.
    assert "canonical_id" in sql


# ---------------------------------------------------------------------------
# 3. Specific-slot mode emits only one branch (no UNION ALL)
# ---------------------------------------------------------------------------


def test_single_slot_no_union_all():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie, slot="year")
    assert "UNION ALL" not in sql
    assert "'year' AS slot_name" in sql
    assert "'title' AS slot_name" not in sql


def test_single_slot_title():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie, slot="title")
    assert "'title' AS slot_name" in sql
    assert "'year' AS slot_name" not in sql


# ---------------------------------------------------------------------------
# 4. Slot typo raises KeyError
# ---------------------------------------------------------------------------


def test_slot_typo_raises_key_error():
    spec, movie = _simple_spec()
    with pytest.raises(KeyError):
        emit_explain_winner_sql(movie, slot="nonexistent_slot")


# ---------------------------------------------------------------------------
# 5. Virtual class raises ValueError
# ---------------------------------------------------------------------------


def test_virtual_class_raises_value_error(movie_spec):
    directed = movie_spec.classes["DirectedMovie"]
    with pytest.raises(ValueError, match="concrete classes"):
        emit_explain_winner_sql(directed)


# ---------------------------------------------------------------------------
# 6. Schema kwarg threads through
# ---------------------------------------------------------------------------


def test_schema_kwarg_threads_through():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie, schema="my_schema")
    assert "my_schema.movie_bindings" in sql
    assert "my_schema.source_weight" in sql
    # Default schema must not appear.
    assert "knot_data" not in sql


# ---------------------------------------------------------------------------
# 7. Weight table kwarg threads through
# ---------------------------------------------------------------------------


def test_weight_table_kwarg_threads_through():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie, weight_table="custom_weights")
    assert "custom_weights" in sql
    assert "source_weight" not in sql


# ---------------------------------------------------------------------------
# 8. sqlglot parses emitted SQL as postgres
# ---------------------------------------------------------------------------


def test_sqlglot_parses_all_slots(movie_spec):
    movie = movie_spec.classes["Movie"]
    sql = emit_explain_winner_sql(movie)
    sqlglot.parse_one(sql, dialect="postgres")


def test_sqlglot_parses_single_slot(movie_spec):
    movie = movie_spec.classes["Movie"]
    sql = emit_explain_winner_sql(movie, slot="year")
    sqlglot.parse_one(sql, dialect="postgres")


def test_sqlglot_parses_simple_spec():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    sqlglot.parse_one(sql, dialect="postgres")


# ---------------------------------------------------------------------------
# 9. Result columns: canonical_id, slot_name, source_name, slot_value,
#    weight, is_winner, margin
# ---------------------------------------------------------------------------


def test_output_columns_present():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    # All required output columns appear in the final SELECT.
    assert "canonical_id" in sql
    assert "slot_name" in sql
    assert "source_name" in sql
    assert "slot_value" in sql
    assert "weight" in sql
    assert "is_winner" in sql
    assert "margin" in sql


def test_is_winner_uses_row_number_eq_1():
    """ROW_NUMBER (not RANK) with (weight DESC, source_name) tie-break
    matches the resolver view's choice exactly — RANK would have
    falsely labeled tied-weight rows both is_winner=true while the
    resolver picked the alphabetically-first source."""
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    assert "(rn = 1) AS is_winner" in sql
    assert "ROW_NUMBER() OVER" in sql
    assert "ORDER BY weight DESC NULLS LAST, source_name" in sql


def test_margin_uses_max_minus_runner_up():
    """Margin = winner.weight - runner_up.weight. The runner-up is
    looked up via a correlated subquery against the ``ranked`` CTE
    where rn=2. NULL when the winner has no runner-up — more honest
    than 0.0 which reads as 'tied with second'."""
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    assert "CASE" in sql
    assert "AND r2.rn = 2" in sql
    assert "max_w - (" in sql


# ---------------------------------------------------------------------------
# 10. spec-level method delegates correctly
# ---------------------------------------------------------------------------


def test_spec_method_matches_free_function(movie_spec):
    movie = movie_spec.classes["Movie"]
    assert movie.explain_winner_sql() == emit_explain_winner_sql(movie)


def test_spec_method_slot_kwarg(movie_spec):
    movie = movie_spec.classes["Movie"]
    assert movie.explain_winner_sql(slot="year") == emit_explain_winner_sql(
        movie, slot="year"
    )


def test_spec_method_schema_kwarg(movie_spec):
    movie = movie_spec.classes["Movie"]
    assert movie.explain_winner_sql(schema="alt") == emit_explain_winner_sql(
        movie, schema="alt"
    )


# ---------------------------------------------------------------------------
# 11. Inherited slots included (is_a chain)
# ---------------------------------------------------------------------------


def test_inherited_slots_included(movie_spec):
    """Movie inherits 'name' from Title (abstract). It must appear in the output."""
    movie = movie_spec.classes["Movie"]
    sql = emit_explain_winner_sql(movie)
    assert "'name' AS slot_name" in sql


# ---------------------------------------------------------------------------
# 12. Abstract class raises ValueError
# ---------------------------------------------------------------------------


def test_abstract_class_raises_value_error(movie_spec):
    title = movie_spec.classes["Title"]
    with pytest.raises(ValueError, match="concrete classes"):
        emit_explain_winner_sql(title)


# ---------------------------------------------------------------------------
# 13. bindings_suffix kwarg threads through
# ---------------------------------------------------------------------------


def test_bindings_suffix_kwarg_threads_through():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie, bindings_suffix="__b")
    assert "movie__b" in sql
    assert "movie_bindings" not in sql


# ---------------------------------------------------------------------------
# 14. Ordering clause present
# ---------------------------------------------------------------------------


def test_order_by_clause_present():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    # Outer ORDER BY uses the ROW_NUMBER (rn) so winners come first
    # per (canonical, slot); within-row ordering already matched the
    # resolver via the ranked CTE's (weight DESC, source_name) tuple.
    assert "ORDER BY canonical_id, slot_name, rn" in sql


# ---------------------------------------------------------------------------
# 15. Weight JOIN uses correct (source, class, slot) triple
# ---------------------------------------------------------------------------


def test_weight_join_uses_class_name():
    spec, movie = _simple_spec()
    sql = emit_explain_winner_sql(movie)
    assert "w.class_name = 'Movie'" in sql
    assert "w.source_name = ps.source_name" in sql
    assert "w.slot_name = ps.slot_name" in sql
