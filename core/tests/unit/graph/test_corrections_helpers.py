"""Pure-Python helper tests for graph.corrections.

``_values_match`` decides whether a source contribution agrees with a
property correction. Scalar paths are straight ``==``; multivalued paths
are order-insensitive set equality. No DB.

DB-backed coverage of ``apply_property_correction``, ``apply_merge``,
and the rest of the corrections orchestration lives in
``tests/integration/graph/test_corrections.py``.
"""

from __future__ import annotations

from knot.graph.corrections import _values_match


def test_values_match_multivalued_order_insensitive():
    assert _values_match(["a", "b", "c"], ["c", "a", "b"])


def test_values_match_multivalued_unequal_sets():
    assert not _values_match(["a", "b"], ["a", "c"])


def test_values_match_scalar_equal():
    assert _values_match("hello", "hello")


def test_values_match_scalar_unequal():
    assert not _values_match("hello", "world")


def test_values_match_none_not_equal_to_value():
    assert not _values_match(None, "something")
