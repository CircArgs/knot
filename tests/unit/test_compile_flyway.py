"""knot.compile.flyway — render MigrationOp lists as Flyway-shaped files."""

import sqlglot

from knot import Spec, types
from knot.compile import MigrationOp, diff_against_db, emit_flyway_files


def _ops_for_empty_db() -> list[MigrationOp]:
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    imdb = spec.add_source("imdb")
    spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    return diff_against_db(spec, lambda sql, params: [])


# ---------------------------------------------------------------------------
# File layout
# ---------------------------------------------------------------------------


def test_emits_v_and_r_files():
    files = emit_flyway_files(_ops_for_empty_db(), version="20260514_001", slug="initial")
    filenames = set(files.keys())
    assert "V20260514_001__initial.sql" in filenames
    assert "R__001_trust_seed.sql" in filenames
    assert "R__002_resolved_views.sql" in filenames


def test_no_ops_emits_no_files():
    assert emit_flyway_files([], version="20260514_001") == {}


def test_only_structural_ops_no_r_files():
    ops = [
        MigrationOp(
            description="create_schema_knot_data",
            sql="CREATE SCHEMA IF NOT EXISTS knot_data;",
            target="schema",
        )
    ]
    files = emit_flyway_files(ops, version="1", slug="schema_only")
    assert set(files.keys()) == {"V1__schema_only.sql"}


def test_only_view_ops_no_v_file():
    ops = [
        MigrationOp(
            description="replace_view_movie_resolved",
            sql="CREATE OR REPLACE VIEW knot_data.movie_resolved AS SELECT 1;",
            target="resolved_view",
        )
    ]
    files = emit_flyway_files(ops, version="1", slug="view_only")
    assert "V1__view_only.sql" not in files
    assert "R__002_resolved_views.sql" in files


def test_only_trust_seed_ops_no_v_file():
    ops = [
        MigrationOp(
            description="upsert_trust_imdb_Movie",
            sql=(
                "INSERT INTO knot_data.source_accuracy "
                "(source_name, class_name, accuracy) "
                "VALUES ('imdb', 'Movie', 0.85) "
                "ON CONFLICT (source_name, class_name) "
                "DO UPDATE SET accuracy = EXCLUDED.accuracy;"
            ),
            target="trust_seed",
        )
    ]
    files = emit_flyway_files(ops, version="1", slug="accuracy_tune")
    assert "V1__accuracy_tune.sql" not in files
    assert "R__001_trust_seed.sql" in files
    assert "R__002_resolved_views.sql" not in files


# ---------------------------------------------------------------------------
# File body shape
# ---------------------------------------------------------------------------


def test_v_file_contains_structural_ops_only():
    files = emit_flyway_files(_ops_for_empty_db(), version="20260514_001", slug="initial")
    v_body = files["V20260514_001__initial.sql"]
    # Structural ops landed in V.
    assert "CREATE SCHEMA" in v_body
    assert "CREATE TABLE" in v_body  # canonical / bindings / trust
    assert "CREATE INDEX" in v_body
    # Trust seed and views are elsewhere.
    assert "INSERT INTO knot_data.source_accuracy" not in v_body
    assert "CREATE OR REPLACE VIEW" not in v_body


def test_r_trust_seed_file_contains_only_upserts():
    files = emit_flyway_files(_ops_for_empty_db(), version="1", slug="initial")
    body = files["R__001_trust_seed.sql"]
    assert "INSERT INTO knot_data.source_accuracy" in body
    assert "ON CONFLICT" in body
    assert "CREATE TABLE" not in body
    assert "CREATE OR REPLACE VIEW" not in body


def test_r_views_file_contains_only_views():
    files = emit_flyway_files(_ops_for_empty_db(), version="1", slug="initial")
    body = files["R__002_resolved_views.sql"]
    assert "CREATE OR REPLACE VIEW" in body
    assert "INSERT INTO knot_data.source_accuracy" not in body


def test_header_summarizes_ops_per_file():
    files = emit_flyway_files(_ops_for_empty_db(), version="1", slug="initial")
    v_body = files["V1__initial.sql"]
    assert "V1__initial" in v_body
    assert "ops:" in v_body  # summary line


def test_per_op_description_comment():
    files = emit_flyway_files(_ops_for_empty_db(), version="1", slug="initial")
    v_body = files["V1__initial.sql"]
    assert "-- create_schema_knot_data" in v_body
    assert "-- create_table_movie" in v_body


def test_destructive_ops_get_tag():
    ops = [
        MigrationOp(
            description="drop_table_legacy",
            sql="DROP TABLE IF EXISTS knot_data.legacy CASCADE;",
            destructive=True,
            target="canonical",
        ),
        MigrationOp(
            description="create_table_movie",
            sql="CREATE TABLE IF NOT EXISTS knot_data.movie (canonical_id text NOT NULL, PRIMARY KEY (canonical_id));",
            target="canonical",
        ),
    ]
    files = emit_flyway_files(ops, version="1", slug="refactor")
    body = files["V1__refactor.sql"]
    assert "-- drop_table_legacy  [DESTRUCTIVE]" in body
    assert "-- create_table_movie" in body
    assert "[DESTRUCTIVE]" not in body.split("create_table_movie")[1]


def test_v_file_preserves_op_order():
    """Order matters: drops first, then adds. The V file should
    concatenate in the order ops were provided."""
    ops = [
        MigrationOp(
            description="drop_view_old_resolved",
            sql="DROP VIEW IF EXISTS knot_data.old_resolved;",
            target="resolved_view",
        ),
        MigrationOp(
            description="drop_column_movie_legacy",
            sql="ALTER TABLE knot_data.movie DROP COLUMN IF EXISTS legacy;",
            destructive=True,
            target="canonical",
        ),
        MigrationOp(
            description="add_column_movie_year",
            sql="ALTER TABLE knot_data.movie ADD COLUMN IF NOT EXISTS year integer;",
            target="canonical",
        ),
    ]
    files = emit_flyway_files(ops, version="1", slug="rebuild")
    body = files["V1__rebuild.sql"]
    drop_col_idx = body.index("drop_column_movie_legacy")
    add_col_idx = body.index("add_column_movie_year")
    assert drop_col_idx < add_col_idx  # drop precedes add in the V file


# ---------------------------------------------------------------------------
# SQL parseability
# ---------------------------------------------------------------------------


def test_all_files_parse_postgres():
    files = emit_flyway_files(_ops_for_empty_db(), version="1", slug="initial")
    for filename, body in files.items():
        # Split on the blank-line separator we use between ops; parse
        # each statement individually. Skip pure-comment chunks.
        for stmt in body.split("\n\n"):
            stripped = "\n".join(
                line for line in stmt.splitlines() if not line.strip().startswith("--")
            ).strip()
            if not stripped:
                continue
            sqlglot.parse_one(stripped, dialect="postgres")
