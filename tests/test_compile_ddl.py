"""knot.compile.ddl.emit_ddl — data-plane DDL emission."""

import sqlglot

from knot.compile import emit_ddl


def _all_parse(stmts: list[str]) -> bool:
    return all(sqlglot.parse_one(s, dialect="postgres") for s in stmts)


def test_default_emits_canonical_bindings_resolved_per_concrete(movie_spec):
    stmts = emit_ddl(movie_spec)
    assert stmts[0].startswith("CREATE SCHEMA IF NOT EXISTS knot_data")
    canonical = [s for s in stmts if s.startswith("CREATE TABLE") and "_bindings" not in s]
    bindings = [s for s in stmts if s.startswith("CREATE TABLE") and "_bindings" in s]
    resolved_views = [s for s in stmts if "_resolved AS" in s]
    virtual_views = [
        s for s in stmts
        if (s.startswith("CREATE VIEW") or s.startswith("CREATE OR REPLACE VIEW"))
        and "_resolved AS" not in s
    ]
    assert len(canonical) == 3       # Movie, Person, Credit canonical tables
    assert len(bindings) == 3        # Movie, Person, Credit bindings tables
    assert len(resolved_views) == 3  # Movie, Person, Credit resolved views
    assert len(virtual_views) == 1   # DirectedMovie (the virtual class)
    assert _all_parse(stmts)


def test_abstract_class_has_no_table(movie_spec):
    stmts = emit_ddl(movie_spec)
    assert not any("knot_data.title (" in s for s in stmts)


def test_concrete_inherits_slots_from_abstract(movie_spec):
    stmts = emit_ddl(movie_spec)
    movie_table = next(s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s)
    # year is Movie's own; name + canonical_id come from Title
    assert "year integer" in movie_table
    assert "name text" in movie_table
    assert "canonical_id text NOT NULL" in movie_table


def test_array_renders_as_postgres_array(movie_spec):
    stmts = emit_ddl(movie_spec)
    movie_table = next(s for s in stmts if "knot_data.movie (" in s and "_bindings" not in s)
    assert "genres text[]" in movie_table


def test_classref_renders_as_text(movie_spec):
    stmts = emit_ddl(movie_spec)
    credit_table = next(s for s in stmts if "knot_data.credit (" in s and "_bindings" not in s)
    assert "movie text" in credit_table
    assert "person text" in credit_table


def test_if_not_exists_kwarg(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    create_tables = [s for s in stmts if "CREATE TABLE" in s]
    assert all("CREATE TABLE IF NOT EXISTS" in s for s in create_tables)
    views = [s for s in stmts if "VIEW" in s]
    assert all("CREATE OR REPLACE VIEW" in s for s in views)


def test_emit_bindings_false_skips_scd2(movie_spec):
    stmts = emit_ddl(movie_spec, emit_bindings=False)
    assert not any("_bindings" in s for s in stmts)


def test_schema_and_suffix_kwargs(movie_spec):
    stmts = emit_ddl(movie_spec, schema="foo", bindings_suffix="__src")
    assert any("foo.movie__src" in s for s in stmts)
    assert not any("knot_data." in s for s in stmts)


def test_emit_descriptions_opt_in(movie_spec):
    no_desc = emit_ddl(movie_spec, emit_descriptions=False)
    with_desc = emit_ddl(movie_spec, emit_descriptions=True)
    assert not any("COMMENT ON" in s for s in no_desc)
    assert any("COMMENT ON TABLE knot_data.movie" in s for s in with_desc)


def test_bindings_table_identifier_not_null_others_nullable(movie_spec):
    stmts = emit_ddl(movie_spec)
    bindings = next(s for s in stmts if "movie_bindings" in s)
    # canonical_id is the identifier — must be NOT NULL
    assert "canonical_id text NOT NULL" in bindings
    # source_name, source_identifier always NOT NULL
    assert "source_name text NOT NULL" in bindings
    assert "source_identifier text NOT NULL" in bindings
    # year is a regular slot — NULLABLE in bindings (partial claim allowed)
    assert "year integer," in bindings or "year integer\n" in bindings
    # Composite PK with valid_from
    assert "PRIMARY KEY (canonical_id, source_name, source_identifier, valid_from)" in bindings
