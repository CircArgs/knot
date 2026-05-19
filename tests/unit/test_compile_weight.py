"""knot.compile.weight — runtime read + upsert SQL emitters.

Weights are runtime-only (no spec-level seed). These tests verify
the SQL templates the host runs to read + write per-(source, class,
slot) weight values in the ``source_weight`` table.
"""

import sqlglot

from knot import Spec, types
from knot.compile import (
    emit_ddl,
    emit_read_weights_sql,
    emit_source_read_weights_sql,
    emit_upsert_weight_sql,
    emit_upsert_weights_sql,
)

# ---------------------------------------------------------------------------
# Weight TABLE (still emitted by emit_ddl; only the seed is gone)
# ---------------------------------------------------------------------------


def test_weight_table_emitted_by_default(movie_spec):
    stmts = emit_ddl(movie_spec)
    weight = next(
        s for s in stmts if s.startswith("CREATE TABLE") and "source_weight" in s
    )
    assert "source_name text NOT NULL" in weight
    assert "class_name" in weight
    assert "slot_name" in weight
    assert "weight" in weight and "double precision" in weight
    assert "CHECK" not in weight
    assert "PRIMARY KEY (source_name, class_name, slot_name)" in weight
    sqlglot.parse_one(weight, dialect="postgres")


def test_weight_table_can_be_disabled(movie_spec):
    stmts = emit_ddl(movie_spec, emit_weight_table=False)
    assert not any("source_weight" in s and s.startswith("CREATE TABLE") for s in stmts)


def test_weight_table_idempotent_with_if_not_exists(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    weight = next(
        s for s in stmts if "source_weight" in s and s.startswith("CREATE TABLE")
    )
    assert weight.startswith("CREATE TABLE IF NOT EXISTS")


def test_weight_table_name_kwarg(movie_spec):
    stmts = emit_ddl(movie_spec, weight_table_name="custom_weight")
    weight = next(
        s for s in stmts if "custom_weight" in s and s.startswith("CREATE TABLE")
    )
    assert "knot_data.custom_weight" in weight
    view = next(s for s in stmts if "_resolved AS" in s)
    assert "knot_data.custom_weight" in view


# ---------------------------------------------------------------------------
# Runtime read SQL
# ---------------------------------------------------------------------------


def _movie_binding():
    spec = Spec(identifier_slot_name="canonical_id")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    movie.slot("title", types.TEXT)
    imdb = spec.add_source("imdb")
    return spec, imdb.bind(movie)


def test_read_weights_sql_selects_for_one_binding():
    _, binding = _movie_binding()
    sql = emit_read_weights_sql(binding)
    assert "SELECT slot_name, weight" in sql
    assert "FROM knot_data.source_weight" in sql
    assert "source_name = 'imdb'" in sql
    assert "class_name = 'Movie'" in sql
    sqlglot.parse_one(sql, dialect="postgres")


def test_read_weights_sql_respects_schema_on_spec():
    spec = Spec(identifier_slot_name="canonical_id", schema="alt")
    movie = spec.add_class("Movie")
    movie.slot("year", types.INTEGER)
    binding = spec.add_source("imdb").bind(movie)
    assert "FROM alt.source_weight" in emit_read_weights_sql(binding)


def test_source_read_weights_sql_scopes_to_source():
    _, binding = _movie_binding()
    sql = emit_source_read_weights_sql(binding.source)
    assert "SELECT class_name, slot_name, weight" in sql
    where = sql.split("WHERE")[1]
    assert "source_name = 'imdb'" in where
    # Source scope is by source_name only — class is in the SELECT
    # projection, not in the WHERE predicate.
    assert "class_name =" not in where
    sqlglot.parse_one(sql, dialect="postgres")


# ---------------------------------------------------------------------------
# Runtime upsert SQL — single + bulk
# ---------------------------------------------------------------------------


def test_upsert_weight_sql_single_slot():
    _, binding = _movie_binding()
    sql = emit_upsert_weight_sql(binding)
    assert "INSERT INTO knot_data.source_weight" in sql
    assert "'imdb'" in sql and "'Movie'" in sql
    assert "%(slot_name)s" in sql and "%(weight)s" in sql
    assert "ON CONFLICT (source_name, class_name, slot_name)" in sql
    assert "DO UPDATE SET weight = EXCLUDED.weight" in sql
    sqlglot.parse_one(sql, dialect="postgres")


def test_upsert_weights_sql_bulk_via_jsonb():
    _, binding = _movie_binding()
    sql = emit_upsert_weights_sql(binding)
    assert "INSERT INTO knot_data.source_weight" in sql
    assert "jsonb_each(%(weights)s::jsonb)" in sql
    assert "ON CONFLICT (source_name, class_name, slot_name)" in sql
    assert "DO UPDATE SET weight = EXCLUDED.weight" in sql
    sqlglot.parse_one(sql, dialect="postgres")


def test_binding_facade_methods_match_free_functions():
    _, binding = _movie_binding()
    assert binding.read_weights_sql() == emit_read_weights_sql(binding)
    assert binding.upsert_weight_sql() == emit_upsert_weight_sql(binding)
    assert binding.upsert_weights_sql() == emit_upsert_weights_sql(binding)


def test_weight_emitters_raise_when_binding_unattached():
    import pytest

    from knot import Source, SourceBinding

    src = Source(name="unattached")
    cls = _movie_binding()[1].class_
    binding = SourceBinding(source=src, class_=cls)
    with pytest.raises(RuntimeError, match="not attached to a Spec"):
        binding.read_weights_sql()
