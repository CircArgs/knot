"""knot.compile.write — emit_validate_rows_sql pre-write row validation."""

import sqlglot

from knot import Spec, types
from knot.compile import emit_validate_rows_sql

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_COLUMNS = {
    "row_index",
    "source_identifier",
    "violation_kind",
    "slot_name",
    "detail",
    "payload",
}


def _columns_from_sql(sql: str) -> set[str]:
    """Extract projected column aliases from the first SELECT in the SQL."""
    parsed = sqlglot.parse_one(sql, dialect="postgres")
    cols: set[str] = set()
    for sel in parsed.find_all(sqlglot.exp.Select):
        for expr in sel.expressions:
            alias = expr.alias or (expr.name if hasattr(expr, "name") else None)
            if alias:
                cols.add(alias)
    return cols


def _make_spec_with_types() -> tuple[Spec, object]:
    """Spec with one concrete class that covers every interesting slot type."""
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("title", types.TEXT, required=True)
    movie.slot("year", types.INTEGER, required=True)
    movie.slot("score", types.FLOAT)
    movie.slot("active", types.BOOLEAN)
    movie.slot("release_date", types.DATE)
    movie.slot("created_at", types.TIMESTAMP)
    movie.slot("genres", types.ARRAY(types.TEXT))
    movie.slot("embedding", types.VECTOR(128))
    imdb = spec.add_source("imdb")
    binding = imdb.bind(movie)
    return spec, binding


# ---------------------------------------------------------------------------
# 1. SQL parses as valid postgres
# ---------------------------------------------------------------------------


def test_sql_parses_as_postgres():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    sqlglot.parse_one(sql, dialect="postgres")


# ---------------------------------------------------------------------------
# 2. Contains %(rows)s::jsonb parameter
# ---------------------------------------------------------------------------


def test_sql_contains_rows_parameter():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    assert "%(rows)s::jsonb" in sql


# ---------------------------------------------------------------------------
# 3. Output columns are correct
# ---------------------------------------------------------------------------


def test_output_columns_present():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    # Check each column name appears in the SQL text (all SELECT branches
    # name their columns consistently).
    for col in _EXPECTED_COLUMNS:
        assert col in sql, f"column {col!r} not found in SQL"


# ---------------------------------------------------------------------------
# 4. Missing source_identifier check is emitted
# ---------------------------------------------------------------------------


def test_missing_source_identifier_check_emitted():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    assert "missing_source_identifier" in sql
    assert "payload->>'source_identifier' IS NULL" in sql


# ---------------------------------------------------------------------------
# 5. Each required non-identifier slot gets a missing-required-slot check
# ---------------------------------------------------------------------------


def test_required_slot_missing_check_emitted():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    # title and year are required non-identifier slots
    assert "missing_required_slot" in sql
    assert "'title'" in sql
    assert "'year'" in sql


def test_required_slot_check_for_all_required_non_identifier_slots():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("alpha", types.TEXT, required=True)
    cls.slot("beta", types.INTEGER, required=True)
    cls.slot("gamma", types.TEXT)  # optional — no missing check
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)

    # Both required non-identifier slots appear as slot_name literals
    assert "'alpha'" in sql
    assert "'beta'" in sql
    # missing_required_slot appears twice (once per required slot)
    assert sql.count("missing_required_slot") == 2


# ---------------------------------------------------------------------------
# 6. Integer slot gets a regex pre-check
# ---------------------------------------------------------------------------


def test_integer_slot_regex_check():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    # INTEGER check: regex for integer pattern
    assert "'^-?[0-9]+$'" in sql
    assert "'year'" in sql


# ---------------------------------------------------------------------------
# 7. Float slot gets a regex pre-check
# ---------------------------------------------------------------------------


def test_float_slot_regex_check():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    assert "float" in sql.lower() or "score" in sql
    # Float regex contains decimal-optional pattern
    assert "[0-9]+)" in sql  # part of the float regex


# ---------------------------------------------------------------------------
# 8. Vector slot gets a jsonb_array_length check matching dim
# ---------------------------------------------------------------------------


def test_vector_slot_jsonb_array_length_check():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    assert "jsonb_array_length" in sql
    assert "128" in sql  # the declared dim
    assert "jsonb_typeof" in sql


# ---------------------------------------------------------------------------
# 9. Optional non-required slots get type checks but NOT missing checks
# ---------------------------------------------------------------------------


def test_optional_slot_no_missing_check():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("optional_year", types.INTEGER)  # optional
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)

    # No missing_required_slot for optional_year
    assert "missing_required_slot" not in sql
    # But it still gets an integer type-coercion check
    assert "'^-?[0-9]+$'" in sql
    assert "'optional_year'" in sql


# ---------------------------------------------------------------------------
# 10. Identifier slot is skipped (no missing-required check, no type check)
# ---------------------------------------------------------------------------


def test_identifier_slot_skipped():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)

    # canonical_id is the identifier; should never appear as a slot_name
    # in missing_required_slot or type_coercion_failed checks.
    # missing_source_identifier is the only check that references NULL slot_name.
    assert "missing_source_identifier" in sql
    # canonical_id should not appear as a slot name literal in violation rows
    assert "'canonical_id'" not in sql


# ---------------------------------------------------------------------------
# 11. Slots with explicit SQL mappings are skipped for type-coercion checks
# ---------------------------------------------------------------------------


def test_explicit_sql_mapping_skips_type_check():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Movie")
    cls.slot("runtime_minutes", types.INTEGER)
    src = spec.add_source("imdb")
    binding = src.bind(cls)
    # Explicit SQL transform — knot can't pre-validate this
    binding.slot(
        class_slot="runtime_minutes",
        source_slot="runtime",
        sql="(regexp_match(runtime, '[0-9]+'))[1]::int",
    )
    sql = emit_validate_rows_sql(binding)
    # No type_coercion_failed check for runtime_minutes since it has explicit SQL
    assert "type_coercion_failed" not in sql


# ---------------------------------------------------------------------------
# 12. Schema / suffix kwargs propagate
# ---------------------------------------------------------------------------


def test_schema_suffix_kwargs_propagate():
    spec, binding = _make_spec_with_types()
    # Should not error; schema/suffix don't affect the SELECT logic
    # (validate_rows_sql doesn't reference the bindings table) but
    # we verify the function accepts the kwargs without raising.
    sql = emit_validate_rows_sql(binding, schema="alt", bindings_suffix="__b")
    assert "%(rows)s::jsonb" in sql


# ---------------------------------------------------------------------------
# 13. binding.validate_rows_sql() facade delegates correctly
# ---------------------------------------------------------------------------


def test_binding_method_matches_free_function():
    spec, binding = _make_spec_with_types()
    assert binding.validate_rows_sql() == emit_validate_rows_sql(binding)


# ---------------------------------------------------------------------------
# 14. Source-slot remapping uses the source field name in checks
# ---------------------------------------------------------------------------


def test_source_slot_remapping_uses_source_field():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Movie")
    cls.slot("year", types.INTEGER, required=True)
    src = spec.add_source("imdb")
    binding = src.bind(cls)
    binding.slot(class_slot="year", source_slot="release_year")
    sql = emit_validate_rows_sql(binding)
    # The source field name "release_year" should appear in the checks,
    # not the class slot name "year"
    assert "release_year" in sql


# ---------------------------------------------------------------------------
# 15. Boolean slot gets a validity check
# ---------------------------------------------------------------------------


def test_boolean_slot_check():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("active", types.BOOLEAN)
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)
    assert "type_coercion_failed" in sql
    assert "'active'" in sql
    assert "true" in sql.lower()
    assert "false" in sql.lower()


# ---------------------------------------------------------------------------
# 16. DATE slot gets a structural pre-check
# ---------------------------------------------------------------------------


def test_date_slot_check():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("release_date", types.DATE)
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)
    assert "type_coercion_failed" in sql
    assert "'release_date'" in sql
    # YYYY-MM-DD pattern
    assert "[0-9]{4}" in sql or "0-9]{4}" in sql


# ---------------------------------------------------------------------------
# 17. TEXT and ARRAY slots have no type_coercion_failed check
# ---------------------------------------------------------------------------


def test_text_and_array_slots_no_type_check():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("name", types.TEXT)
    cls.slot("tags", types.ARRAY(types.TEXT))
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)
    # No type coercion check for TEXT or ARRAY
    assert "type_coercion_failed" not in sql


# ---------------------------------------------------------------------------
# 18. ClassRef slot has no type_coercion_failed check
# ---------------------------------------------------------------------------


def test_classref_slot_no_type_check():
    spec = Spec(identifier_slot_name="canonical_id")
    person = spec.add_class("Person")
    person.slot("name", types.TEXT)
    movie = spec.add_class("Movie")
    movie.slot("director", person)
    src = spec.add_source("s")
    binding = src.bind(movie)
    sql = emit_validate_rows_sql(binding)
    # director is a ClassRef — no type_coercion_failed check
    assert "type_coercion_failed" not in sql


# ---------------------------------------------------------------------------
# 19. payload column present in every SELECT branch
# ---------------------------------------------------------------------------


def test_payload_column_present_in_every_branch():
    spec, binding = _make_spec_with_types()
    sql = emit_validate_rows_sql(binding)
    # Every UNION ALL branch must project payload.
    # Count SELECT blocks and verify each has "payload".
    branches = sql.split("UNION ALL")
    for i, branch in enumerate(branches):
        # Strip the leading CTE and trailing semicolon from first/last.
        assert "payload" in branch, (
            f"branch {i} missing 'payload' column:\n{branch[:300]}"
        )


# ---------------------------------------------------------------------------
# 20. Zero-rows when no typeable slots (only TEXT)  [was 19]
# ---------------------------------------------------------------------------


def test_only_source_identifier_and_text_produces_minimal_sql():
    spec = Spec(identifier_slot_name="canonical_id")
    cls = spec.add_class("Thing")
    cls.slot("label", types.TEXT)
    src = spec.add_source("s")
    binding = src.bind(cls)
    sql = emit_validate_rows_sql(binding)
    # Only missing_source_identifier check (no required, no typeable)
    assert "missing_source_identifier" in sql
    assert "missing_required_slot" not in sql
    assert "type_coercion_failed" not in sql
