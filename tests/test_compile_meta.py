"""knot.compile.meta.emit_meta_ddl — spec-storage meta-tables."""

import sqlglot

from knot.compile import emit_meta_ddl


def test_emits_schema_plus_nine_tables():
    stmts = emit_meta_ddl()
    assert len(stmts) == 10  # CREATE SCHEMA + 9 tables
    assert stmts[0].startswith("CREATE SCHEMA IF NOT EXISTS knot_meta")


def test_all_parse_under_sqlglot_postgres():
    for s in emit_meta_ddl():
        sqlglot.parse_one(s, dialect="postgres")


def test_if_not_exists_propagates():
    stmts = emit_meta_ddl(if_not_exists=True)
    create_tables = [s for s in stmts if s.startswith("CREATE TABLE")]
    assert all("CREATE TABLE IF NOT EXISTS" in s for s in create_tables)


def test_schema_kwarg_rewrites_qualifications():
    stmts = emit_meta_ddl(schema="alt")
    assert stmts[0].startswith("CREATE SCHEMA IF NOT EXISTS alt")
    # Every CREATE TABLE references the alt schema, never knot_meta.
    for s in stmts[1:]:
        assert "alt." in s
        assert "knot_meta." not in s


def test_slot_type_is_jsonb():
    stmts = emit_meta_ddl()
    slots = next(s for s in stmts if "slots (" in s)
    assert "type jsonb NOT NULL" in slots


def test_source_bindings_carries_accuracy():
    stmts = emit_meta_ddl()
    sb = next(s for s in stmts if "source_bindings (" in s)
    assert "accuracy double precision" in sb


def test_constraint_table_has_severity_with_default():
    stmts = emit_meta_ddl()
    cs = next(s for s in stmts if "constraints (" in s)
    assert "severity text NOT NULL DEFAULT 'error'" in cs


def test_spec_root_has_content_hash_column():
    stmts = emit_meta_ddl()
    spec = next(s for s in stmts if "{}.spec (".format("knot_meta") in s)
    assert "content_hash text" in spec
