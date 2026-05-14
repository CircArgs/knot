"""knot.compile.migrate — diff_against_db (Phase 1: additive only)."""

from typing import Any

import pytest
import sqlglot

from knot import CORRECTIONS_SOURCE_NAME, Primitive, Spec
from knot.compile import MigrationOp, diff_against_db


# ---------------------------------------------------------------------------
# Mock DB — a callable that returns rows from a hardcoded state map.
# ---------------------------------------------------------------------------


def _split_pg_type(pg_type: str) -> tuple[str, str]:
    """Reverse of _normalize_pg_type for MockDB convenience: emit the
    information_schema.columns shape (data_type, udt_name) from a
    normalized type string. Tests pin pg_type like 'integer' or
    'text[]'; we deconstruct that into the shape postgres returns."""
    if pg_type.endswith("[]"):
        inner = pg_type[:-2]
        return "ARRAY", "_" + inner
    if pg_type == "timestamptz":
        return "timestamp with time zone", "timestamptz"
    if pg_type == "timestamp":
        return "timestamp without time zone", "timestamp"
    return pg_type, pg_type


class MockDB:
    """Stand-in for a postgres connection's query callable. The state
    is a dict of (probe_kind, …) → list of result tuples. Unmocked
    queries return empty rows (= "nothing exists")."""

    def __init__(
        self,
        *,
        schemas: set[str] | None = None,
        tables: dict[str, set[str]] | None = None,        # schema → table names
        views: dict[str, set[str]] | None = None,         # schema → view names
        columns: dict[tuple[str, str], set[str]] | None = None,  # (schema, table) → cols
        indexes: dict[tuple[str, str], set[str]] | None = None,  # (schema, table) → indexes
        fks: dict[tuple[str, str], set[str]] | None = None,      # (schema, table) → fk names
        trust_rows: list[tuple[str, str, float]] | None = None,
    ):
        self.schemas = schemas or set()
        self.tables = tables or {}
        self.views = views or {}
        self.columns = columns or {}
        self.indexes = indexes or {}
        self.fks = fks or {}
        self.trust_rows = trust_rows or []

    def __call__(self, sql: str, params: tuple[Any, ...]) -> list[tuple[Any, ...]]:
        sql_lc = sql.lower()
        if "from information_schema.schemata" in sql_lc:
            return [(s,) for s in sorted(self.schemas)]
        if "from information_schema.tables" in sql_lc:
            schema = params[0]
            return [(t,) for t in sorted(self.tables.get(schema, set()))]
        if "from information_schema.views" in sql_lc:
            schema = params[0]
            return [(v,) for v in sorted(self.views.get(schema, set()))]
        if "from information_schema.columns" in sql_lc:
            schema, table = params
            entry = self.columns.get((schema, table), set())
            # Two supported shapes:
            #   set[str]                            (name-only; defaults to text+nullable)
            #   dict[str, tuple[pg_type, nullable]] (full detail)
            if isinstance(entry, dict):
                items = entry.items()
                rows: list[tuple[Any, ...]] = []
                for name, (pg_type, nullable) in sorted(items):
                    data_type, udt = _split_pg_type(pg_type)
                    rows.append(
                        (name, data_type, "YES" if nullable else "NO", udt)
                    )
                return rows
            # set-only shape — defaults.
            return [
                (c, "text", "YES", "text") for c in sorted(entry)
            ]
        if "from pg_indexes" in sql_lc:
            schema, table = params
            return [(i,) for i in sorted(self.indexes.get((schema, table), set()))]
        if "from information_schema.table_constraints" in sql_lc:
            schema, table = params
            return [(f,) for f in sorted(self.fks.get((schema, table), set()))]
        if ".source_accuracy" in sql_lc and "select" in sql_lc:
            return [(s, c, a) for s, c, a in self.trust_rows]
        return []


def _basic_spec() -> Spec:
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)
    imdb = spec.add_source("imdb")
    spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    return spec


# ---------------------------------------------------------------------------
# Empty-DB case
# ---------------------------------------------------------------------------


def test_empty_db_emits_full_create_sequence():
    spec = _basic_spec()
    ops = diff_against_db(spec, MockDB())
    targets = [op.target for op in ops]
    assert "schema" in targets
    assert "trust_table" in targets
    assert "canonical" in targets
    assert "bindings" in targets
    assert "index" in targets
    assert "resolved_view" in targets
    assert "trust_seed" in targets


def test_empty_db_op_ordering():
    spec = _basic_spec()
    ops = diff_against_db(spec, MockDB())
    targets = [op.target for op in ops]
    # Schema must come first
    assert targets[0] == "schema"
    # Trust table must come before canonical (resolver views depend on it)
    assert targets.index("trust_table") < targets.index("canonical")
    # Canonical before bindings
    assert targets.index("canonical") < targets.index("bindings")
    # Bindings before its indexes
    assert targets.index("bindings") < targets.index("index")


def test_empty_db_all_sql_parses_postgres():
    spec = _basic_spec()
    spec.enable_corrections()
    for op in diff_against_db(spec, MockDB()):
        sqlglot.parse_one(op.sql, dialect="postgres")


def test_no_ops_are_destructive_in_phase_1():
    spec = _basic_spec()
    spec.enable_corrections()
    for op in diff_against_db(spec, MockDB()):
        assert op.destructive is False


# ---------------------------------------------------------------------------
# Schema exists, no tables yet
# ---------------------------------------------------------------------------


def test_schema_exists_no_create_schema_op():
    spec = _basic_spec()
    db = MockDB(schemas={"knot_data"})
    ops = diff_against_db(spec, db)
    assert not any(op.target == "schema" for op in ops)
    # But other CREATE ops should still appear.
    assert any(op.target == "canonical" for op in ops)


# ---------------------------------------------------------------------------
# Canonical exists, bindings/indexes missing
# ---------------------------------------------------------------------------


def test_canonical_present_bindings_missing():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie"}},
        columns={("knot_data", "movie"): {"canonical_id", "year"}},
    )
    ops = diff_against_db(spec, db)
    canonical_ops = [op for op in ops if op.target == "canonical"]
    bindings_ops = [op for op in ops if op.target == "bindings"]
    assert canonical_ops == []  # canonical fully present
    # The CREATE TABLE for movie_bindings should still appear
    assert any(
        "create_table_movie_bindings" in op.description for op in bindings_ops
    )


# ---------------------------------------------------------------------------
# Missing column on existing table
# ---------------------------------------------------------------------------


def test_missing_column_emits_add_column():
    spec = _basic_spec()
    # canonical exists but only has canonical_id — `year` is missing
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id"},  # missing `year`
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
    )
    ops = diff_against_db(spec, db)
    add_col_ops = [op for op in ops if "add_column_movie_year" in op.description]
    assert len(add_col_ops) == 1
    assert "ALTER TABLE knot_data.movie" in add_col_ops[0].sql
    assert "ADD COLUMN IF NOT EXISTS year integer" in add_col_ops[0].sql


def test_missing_raw_payload_column_in_bindings():
    spec = _basic_spec()
    # Old-style bindings table from before the raw_payload pass —
    # the migration should detect and ALTER ADD it.
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "valid_from", "valid_to",  # no raw_payload
            },
        },
    )
    ops = diff_against_db(spec, db)
    raw_ops = [op for op in ops if "raw_payload" in op.description]
    assert len(raw_ops) == 1
    assert "ADD COLUMN IF NOT EXISTS raw_payload jsonb" in raw_ops[0].sql


# ---------------------------------------------------------------------------
# Trust-seed diff
# ---------------------------------------------------------------------------


def test_trust_row_already_matches_spec_no_upsert():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {
                "movie_bindings_current_idx", "movie_bindings_source_idx",
            }
        },
        trust_rows=[("imdb", "Movie", 0.85)],
    )
    ops = diff_against_db(spec, db)
    trust_ops = [op for op in ops if op.target == "trust_seed"]
    assert trust_ops == []


def test_trust_row_mismatch_emits_upsert():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {
                "movie_bindings_current_idx", "movie_bindings_source_idx",
            }
        },
        # DB has the OLD value 0.7; spec wants 0.85
        trust_rows=[("imdb", "Movie", 0.7)],
    )
    ops = diff_against_db(spec, db)
    trust_ops = [op for op in ops if op.target == "trust_seed"]
    assert len(trust_ops) == 1
    assert "0.85" in trust_ops[0].sql


def test_trust_row_missing_emits_upsert():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {
                "movie_bindings_current_idx", "movie_bindings_source_idx",
            }
        },
        trust_rows=[],  # nothing in source_accuracy yet
    )
    ops = diff_against_db(spec, db)
    trust_ops = [op for op in ops if op.target == "trust_seed"]
    assert len(trust_ops) == 1
    assert "imdb" in trust_ops[0].sql


# ---------------------------------------------------------------------------
# Resolved view is always replaced (idempotent)
# ---------------------------------------------------------------------------


def test_resolved_view_always_replaced():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        views={"knot_data": {"movie_resolved"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {
                "movie_bindings_current_idx", "movie_bindings_source_idx",
            }
        },
        trust_rows=[("imdb", "Movie", 0.85)],
    )
    ops = diff_against_db(spec, db)
    view_ops = [op for op in ops if op.target == "resolved_view"]
    # Always emit OR REPLACE so view body stays in sync with the spec.
    assert len(view_ops) == 1
    assert "CREATE OR REPLACE VIEW" in view_ops[0].sql


# ---------------------------------------------------------------------------
# Index detection
# ---------------------------------------------------------------------------


def test_missing_index_on_existing_bindings():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {"movie_bindings_current_idx"},
            # source_idx missing
        },
    )
    ops = diff_against_db(spec, db)
    idx_ops = [op for op in ops if op.target == "index"]
    names = " ".join(op.description for op in idx_ops)
    assert "movie_bindings_source_idx" in names
    assert "movie_bindings_current_idx" not in names


# ---------------------------------------------------------------------------
# Virtual-class views
# ---------------------------------------------------------------------------


def test_virtual_class_view_always_replaced(movie_spec):
    ops = diff_against_db(movie_spec, MockDB())
    virtual_ops = [op for op in ops if op.target == "virtual_view"]
    # movie_spec has one VirtualClass: DirectedMovie.
    assert len(virtual_ops) == 1
    assert "CREATE OR REPLACE VIEW" in virtual_ops[0].sql
    assert "directedmovie" in virtual_ops[0].sql.lower()


# ---------------------------------------------------------------------------
# MigrationOp shape
# ---------------------------------------------------------------------------


def test_migration_op_carries_target_and_description():
    spec = _basic_spec()
    ops = diff_against_db(spec, MockDB())
    for op in ops:
        assert isinstance(op, MigrationOp)
        assert op.description
        assert op.sql
        assert op.target in {
            "schema", "trust_table", "canonical", "bindings",
            "index", "fk", "resolved_view", "virtual_view",
            "trust_seed",
        }


# ---------------------------------------------------------------------------
# Phase 2 — destructive ops + non-destructive cleanup
# ---------------------------------------------------------------------------


def _spec_with_movie_only() -> Spec:
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    return spec


def test_extra_table_in_db_emits_drop_when_allowed():
    spec = _spec_with_movie_only()
    # Old class (Show) lingers in the database.
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings",
                              "show", "show_bindings"}},
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    drops = [op for op in ops if op.description.startswith("drop_table_")]
    assert any("drop_table_show" == op.description for op in drops)
    assert any("drop_table_show_bindings" == op.description for op in drops)
    for op in drops:
        assert op.destructive is True
        assert "DROP TABLE IF EXISTS" in op.sql
        assert "CASCADE" in op.sql


def test_extra_table_filtered_out_when_destructive_disabled():
    spec = _spec_with_movie_only()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings", "show"}},
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    assert not any(op.description.startswith("drop_table_") for op in ops)


def test_extra_column_emits_drop_when_allowed():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year", "deprecated_col"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
                "deprecated_bindings_col",
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    drops = [op for op in ops if op.description.startswith("drop_column_")]
    canonical_drop = next(
        op for op in drops if op.description == "drop_column_movie_deprecated_col"
    )
    bindings_drop = next(
        op for op in drops
        if op.description == "drop_column_movie_bindings_deprecated_bindings_col"
    )
    assert canonical_drop.destructive is True
    assert bindings_drop.destructive is True
    assert "DROP COLUMN IF EXISTS deprecated_col" in canonical_drop.sql
    assert "DROP COLUMN IF EXISTS deprecated_bindings_col" in bindings_drop.sql


def test_bindings_framework_columns_never_dropped():
    """source_name, source_identifier, raw_payload, valid_from, valid_to
    are SCD2 framework columns — never proposed for removal even if
    not in the slot list."""
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    framework = {"source_name", "source_identifier", "raw_payload",
                 "valid_from", "valid_to"}
    for fc in framework:
        assert not any(
            f"drop_column_movie_bindings_{fc}" == op.description for op in ops
        )


def test_extra_view_drop_is_not_destructive():
    spec = _spec_with_movie_only()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        views={"knot_data": {"movie_resolved", "show_resolved", "old_virtual"}},
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    drops = [op for op in ops if op.description.startswith("drop_view_")]
    drop_names = {op.description for op in drops}
    assert "drop_view_show_resolved" in drop_names
    assert "drop_view_old_virtual" in drop_names
    # Always-emitted (non-destructive) → present regardless of opt-in flag.
    for op in drops:
        assert op.destructive is False


def test_extra_fk_drop_is_not_destructive():
    spec = _spec_with_movie_only()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "raw_payload", "valid_from", "valid_to",
            },
        },
        fks={("knot_data", "movie"): {"fk_movie_studio"}},
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    fk_drops = [op for op in ops if op.description.startswith("drop_fk_")]
    assert any(op.description == "drop_fk_fk_movie_studio" for op in fk_drops)
    for op in fk_drops:
        assert op.destructive is False
        assert "DROP CONSTRAINT IF EXISTS fk_movie_studio" in op.sql


def test_extra_index_drop_is_not_destructive_and_preserves_pkey():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {
                "movie_bindings_current_idx",
                "movie_bindings_source_idx",
                "movie_bindings_pkey",      # postgres PK auto-index — leave alone
                "movie_bindings_legacy_idx",
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    drop_idx = [op for op in ops if op.description.startswith("drop_index_")]
    names = {op.description for op in drop_idx}
    assert "drop_index_movie_bindings_legacy_idx" in names
    assert "drop_index_movie_bindings_pkey" not in names  # never drop pkey
    for op in drop_idx:
        assert op.destructive is False


def test_extra_trust_row_emits_delete():
    spec = _basic_spec()
    # DB has trust rows for sources not in the spec.
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "year"},
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "year", "raw_payload", "valid_from", "valid_to",
            },
        },
        indexes={
            ("knot_data", "movie_bindings"): {
                "movie_bindings_current_idx", "movie_bindings_source_idx",
            }
        },
        trust_rows=[
            ("imdb", "Movie", 0.85),       # in spec → keep
            ("rottentomatoes", "Movie", 0.6),  # not in spec → drop
        ],
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    drop_trust = [op for op in ops if op.description.startswith("drop_trust_")]
    assert len(drop_trust) == 1
    assert "rottentomatoes" in drop_trust[0].sql
    assert "DELETE FROM" in drop_trust[0].sql
    assert drop_trust[0].destructive is False


def test_drops_precede_adds():
    """Order matters: drops first, so any subsequent adds don't
    collide with stale objects."""
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings", "ghost"}},
        views={"knot_data": {"ghost_resolved"}},
        columns={
            ("knot_data", "movie"): {"canonical_id"},  # missing `year`
            ("knot_data", "movie_bindings"): {
                "canonical_id", "source_name", "source_identifier",
                "raw_payload", "valid_from", "valid_to",
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    descriptions = [op.description for op in ops]
    # Find the first non-drop op
    first_add_idx = next(
        i for i, d in enumerate(descriptions)
        if not d.startswith("drop_") and d != "create_schema_knot_data"
    )
    # All drops should appear before the first add.
    for op in ops[:first_add_idx]:
        assert op.description.startswith("drop_") or op.description.startswith("create_schema_")


def test_drop_all_sql_parses_postgres():
    spec = _spec_with_movie_only()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings", "show"}},
        views={"knot_data": {"show_resolved"}},
        columns={
            ("knot_data", "movie"): {"canonical_id", "stale_col"},
        },
        indexes={
            ("knot_data", "movie_bindings"): {"old_idx", "movie_bindings_pkey"},
        },
        fks={("knot_data", "movie"): {"fk_movie_nobody"}},
        trust_rows=[("orphan", "Movie", 0.5)],
    )
    for op in diff_against_db(spec, db, allow_destructive=True):
        sqlglot.parse_one(op.sql, dialect="postgres")


# ---------------------------------------------------------------------------
# Phase 3 — type-change, nullability, rename
# ---------------------------------------------------------------------------


def test_type_mismatch_emits_alter_column_type():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            # DB has `year` as text; spec says integer.
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "year": ("text", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "year": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    alter_type = [
        op for op in ops
        if op.description == "alter_column_type_movie_year"
    ]
    assert len(alter_type) == 1
    assert alter_type[0].destructive is True
    assert "TYPE integer" in alter_type[0].sql
    assert "USING year::integer" in alter_type[0].sql


def test_type_mismatch_filtered_out_when_destructive_disabled():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "year": ("text", True),  # spec says integer
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "year": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    assert not any("alter_column_type" in op.description for op in ops)


def test_nullable_to_not_null_is_destructive():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            # canonical_id is nullable in DB but spec says identifier (NOT NULL)
            ("knot_data", "movie"): {
                "canonical_id": ("text", True),
                "year": ("integer", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "year": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    set_nn = [op for op in ops if op.description == "set_not_null_movie_canonical_id"]
    assert len(set_nn) == 1
    assert set_nn[0].destructive is True
    assert "SET NOT NULL" in set_nn[0].sql


def test_not_null_to_nullable_is_safe():
    """Widening (NOT NULL → nullable) doesn't lose data; never destructive."""
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("year", Primitive.INTEGER)  # not required → nullable
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "year": ("integer", False),  # NOT NULL in DB, spec wants nullable
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "year": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=False)
    drop_nn = [op for op in ops if op.description == "drop_not_null_movie_year"]
    assert len(drop_nn) == 1
    assert drop_nn[0].destructive is False
    assert "DROP NOT NULL" in drop_nn[0].sql


def test_array_type_matches_when_canonicalized():
    """Postgres returns ARRAY + udt_name='_text' for text[]; the
    normalizer must produce 'text[]' for the comparison."""
    spec = Spec(id="m", version="0.1")
    from knot import Array
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("genres", Array(of=Primitive.TEXT))
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "genres": ("text[]", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "genres": ("text[]", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    # No type change should be emitted — text[] matches text[].
    assert not any("alter_column_type" in op.description for op in ops)


def test_timestamptz_normalization():
    """Postgres data_type='timestamp with time zone' must match
    knot's 'timestamptz' output."""
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("released_at", Primitive.TIMESTAMP)
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "released_at": ("timestamptz", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "released_at": ("timestamptz", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(spec, db, allow_destructive=True)
    assert not any("alter_column_type" in op.description for op in ops)


# ---------------------------------------------------------------------------
# Renames
# ---------------------------------------------------------------------------


def test_rename_emits_alter_table_rename_column_on_both_tables():
    spec = _basic_spec()
    # DB has `yr` from a prior version; spec calls it `year`.
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "yr": ("integer", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "yr": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(
        spec,
        db,
        allow_destructive=True,
        renames={"Movie": {"yr": "year"}},
    )
    rename_ops = [op for op in ops if op.description.startswith("rename_column_")]
    descriptions = {op.description for op in rename_ops}
    assert "rename_column_movie_yr_to_year" in descriptions
    assert "rename_column_movie_bindings_yr_to_year" in descriptions
    for op in rename_ops:
        assert "ALTER TABLE" in op.sql and "RENAME COLUMN" in op.sql
        assert op.destructive is False


def test_rename_runs_before_drops_and_adds():
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "yr": ("integer", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "yr": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(
        spec,
        db,
        allow_destructive=True,
        renames={"Movie": {"yr": "year"}},
    )
    descriptions = [op.description for op in ops]
    rename_idx = next(
        i for i, d in enumerate(descriptions)
        if d.startswith("rename_column_")
    )
    # No drops or adds before the first rename.
    for d in descriptions[:rename_idx]:
        assert not d.startswith("drop_")
        assert not d.startswith("add_column_")


def test_rename_suppresses_drop_and_add():
    """After renaming yr→year, the column is now `year` from the
    diff's POV — no drop_column_yr and no add_column_year."""
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "yr": ("integer", True),
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "yr": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(
        spec,
        db,
        allow_destructive=True,
        renames={"Movie": {"yr": "year"}},
    )
    descriptions = {op.description for op in ops}
    assert "drop_column_movie_yr" not in descriptions
    assert "add_column_movie_year" not in descriptions


def test_rename_skipped_when_old_column_missing():
    """If the old name isn't in the DB, the rename is a no-op (probably
    already applied or never created)."""
    spec = _basic_spec()
    db = MockDB(
        schemas={"knot_data"},
        tables={"knot_data": {"source_accuracy", "movie", "movie_bindings"}},
        columns={
            ("knot_data", "movie"): {
                "canonical_id": ("text", False),
                "year": ("integer", True),  # already named "year"
            },
            ("knot_data", "movie_bindings"): {
                "canonical_id": ("text", False),
                "source_name": ("text", False),
                "source_identifier": ("text", False),
                "year": ("integer", True),
                "raw_payload": ("jsonb", False),
                "valid_from": ("timestamptz", False),
                "valid_to": ("timestamptz", True),
            },
        },
    )
    ops = diff_against_db(
        spec,
        db,
        renames={"Movie": {"yr": "year"}},
    )
    rename_ops = [op for op in ops if op.description.startswith("rename_")]
    assert rename_ops == []
