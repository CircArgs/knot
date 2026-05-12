"""Unit tests for the spec-aware SQL rewriter in ``knot.spec.sql_validate``.

Tests cover:
  - Bare class name in FROM → schema-qualified + bindings JOIN injected
  - ClassName.slot column refs rewritten to deterministic alias
  - ``self`` keyword → outer_bindings_alias.canonical_id
  - Schema-qualified references pass through unchanged (escape hatch)
  - Unknown slot on known class → SqlPredicateError
  - Multiple class references in the same body (counter suffix)
  - compile_to_sql end-to-end with classes_by_name

No DB connection required — pure Python + sqlglot.
"""

from __future__ import annotations

import pytest

from knot.spec.metaschema import OntologyClass, Primitive, Property
from knot.spec.sql_validate import (
    SqlPredicateError,
    _rewrite_spec_references,
    compile_to_sql,
    parse_predicate,
)
from knot.spec.compile.postgres._context import CompileContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA = "knot_data"


def _credit_class() -> OntologyClass:
    role = Property(name="role", type=Primitive(name="string"))
    person = Property(name="person", type=Primitive(name="string"))
    return OntologyClass(name="Credit", properties=[role, person])


def _person_class() -> OntologyClass:
    name = Property(name="name", type=Primitive(name="string"))
    return OntologyClass(name="Person", properties=[name])


def _rewrite(body: str, classes_by_name: dict, outer_alias: str = "b") -> str:
    expr = parse_predicate(body)
    return _rewrite_spec_references(
        expr,
        classes_by_name=classes_by_name,
        outer_bindings_alias=outer_alias,
        schema_name=_SCHEMA,
    )


# ---------------------------------------------------------------------------
# 1. Bare class name in FROM rewrites to schema-qualified table + bindings JOIN
# ---------------------------------------------------------------------------


def test_bare_class_in_from_becomes_schema_qualified():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit WHERE Credit.role = 'director')",
        classes,
    )
    assert "knot_data.credit" in result.lower() or "knot_data.credit" in result
    assert "credit__c0" in result


def test_bare_class_in_from_injects_bindings_join():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit WHERE Credit.role = 'director')",
        classes,
    )
    assert "credit_bindings" in result
    assert "credit__b0" in result
    assert "valid_to IS NULL" in result


# ---------------------------------------------------------------------------
# 2. ClassName.slot column refs are rewritten to alias.slot
# ---------------------------------------------------------------------------


def test_class_dot_slot_rewrites_to_alias():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit WHERE Credit.role = 'director')",
        classes,
    )
    assert "credit__c0.role" in result
    assert "Credit.role" not in result


# ---------------------------------------------------------------------------
# 3. ``self`` resolves to outer_bindings_alias.canonical_id
# ---------------------------------------------------------------------------


def test_self_resolves_to_outer_alias_canonical_id():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit WHERE Credit.person = self AND Credit.role = 'director')",
        classes,
        outer_alias="b",
    )
    assert "b.canonical_id" in result
    # self should not appear as a bare column name
    assert " self" not in result


def test_self_resolves_with_custom_outer_alias():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit WHERE Credit.person = self)",
        classes,
        outer_alias="outer_b",
    )
    assert "outer_b.canonical_id" in result


def test_bare_self_in_predicate():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite("self IS NOT NULL", classes, outer_alias="b")
    assert "b.canonical_id" in result


# ---------------------------------------------------------------------------
# 4. Schema-qualified references pass through unchanged (escape hatch)
# ---------------------------------------------------------------------------


def test_schema_qualified_table_passes_through():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM knot_data.credit c WHERE c.role = 'director')",
        classes,
    )
    # Schema-qualified — must not be renamed to credit__c0
    assert "credit__c0" not in result
    # Original reference preserved
    assert "knot_data.credit" in result or "knot_data" in result


# ---------------------------------------------------------------------------
# 5. Unknown slot on known class raises SqlPredicateError
# ---------------------------------------------------------------------------


def test_unknown_slot_on_known_class_raises():
    credit = _credit_class()
    classes = {"Credit": credit}
    with pytest.raises(SqlPredicateError, match="Unknown slot"):
        _rewrite(
            "EXISTS (SELECT 1 FROM Credit WHERE Credit.nonexistent = 'x')",
            classes,
        )


def test_error_message_includes_class_and_slot():
    credit = _credit_class()
    classes = {"Credit": credit}
    with pytest.raises(SqlPredicateError) as exc_info:
        _rewrite(
            "EXISTS (SELECT 1 FROM Credit WHERE Credit.bogus_field = 1)",
            classes,
        )
    msg = str(exc_info.value)
    assert "bogus_field" in msg
    assert "Credit" in msg


# ---------------------------------------------------------------------------
# 6. Unknown class in FROM passes through (not a spec class)
# ---------------------------------------------------------------------------


def test_unknown_class_in_from_passes_through():
    classes: dict = {}
    # NotAClass is not in the spec — should pass through without modification
    result = _rewrite(
        "EXISTS (SELECT 1 FROM some_table WHERE some_table.col = 1)",
        classes,
    )
    assert "some_table" in result


# ---------------------------------------------------------------------------
# 7. Multiple different classes in one body (counter suffix)
# ---------------------------------------------------------------------------


def test_multiple_classes_get_unique_aliases():
    credit = _credit_class()
    person = _person_class()
    classes = {"Credit": credit, "Person": person}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit, Person WHERE Credit.person = Person.name)",
        classes,
    )
    assert "credit__c0" in result
    assert "person__c0" in result
    # Bindings aliases also present
    assert "credit__b0" in result
    assert "person__b0" in result


def test_same_class_twice_gets_different_suffixes():
    credit = _credit_class()
    classes = {"Credit": credit}
    result = _rewrite(
        "EXISTS (SELECT 1 FROM Credit c1, Credit c2 WHERE c1.role = c2.role)",
        classes,
    )
    # Two occurrences of Credit should produce c0 and c1 src aliases
    assert "credit__c0" in result
    assert "credit__c1" in result


# ---------------------------------------------------------------------------
# 8. Director scenario: full end-to-end compile_to_sql
# ---------------------------------------------------------------------------


def test_director_body_compiles_end_to_end():
    """The Netflix Director definition rewrites to correct SQL."""
    credit = _credit_class()
    person = _person_class()
    classes = {"Credit": credit, "Person": person}

    body = "EXISTS (SELECT 1 FROM Credit WHERE Credit.person = self AND Credit.role = 'director')"
    ctx = CompileContext(primary_class=person, alias="s")
    result_sql = compile_to_sql(
        body,
        person,
        ctx,
        classes_by_name=classes,
        outer_bindings_alias="b",
    )
    rendered = result_sql.as_string(None)

    # Table + bindings
    assert "knot_data" in rendered
    assert "credit__c0" in rendered
    assert "credit_bindings" in rendered
    # self → b.canonical_id
    assert "b.canonical_id" in rendered
    # slot qualified
    assert "credit__c0.role" in rendered


# ---------------------------------------------------------------------------
# 9. No classes_by_name → no spec-aware rewrite (old behaviour preserved)
# ---------------------------------------------------------------------------


def test_no_classes_by_name_no_rewrite():
    """When classes_by_name is not passed, body passes through unmodified."""
    person = _person_class()
    ctx = CompileContext(primary_class=person, alias="s")
    # This would normally raise if spec-aware validation ran (Credit unknown)
    body = "EXISTS (SELECT 1 FROM Credit WHERE Credit.role = 'director')"
    # Should NOT raise — no spec-aware pass
    result = compile_to_sql(body, person, ctx)
    rendered = result.as_string(None)
    # The table Credit is left as-is (not schema-qualified)
    assert "credit__c0" not in rendered
