"""End-to-end integration tests against live postgres.

Each test gets a fresh schema. We:
  1. Build a Spec
  2. Apply emit_ddl + emit_trust_seed to the schema
  3. Exercise the write path (emit_batch_write / emit_close_out)
  4. Query the resolved view + validation SELECTs and assert behavior
  5. Evolve the spec, run diff_against_db, apply the ops, repeat

The whole point is to verify the SQL we emit is not just well-formed
but semantically correct against postgres 16.
"""

from __future__ import annotations

from knot import (
    CORRECTIONS_SOURCE_NAME,
    Primitive,
    Spec,
)
from knot.compile import (
    ClassWrites,
    diff_against_db,
    emit_batch_write,
    emit_close_out,
    emit_ddl,
    emit_flyway_files,
    emit_trust_seed,
    emit_validation,
)
from tests.integration.conftest import exec_many, exec_script, exec_with_params

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deploy(pg, spec: Spec, schema: str) -> None:
    """Apply emit_ddl + emit_trust_seed to ``schema`` so the DB
    matches ``spec``."""
    exec_many(pg, emit_ddl(spec, schema=schema))
    with pg.cursor() as cur:
        for sql, params in emit_trust_seed(spec, schema=schema):
            cur.execute(sql, params)


def _movies_only_spec() -> Spec:
    """Single class Movie with year + runtime, IMDB + TMDB sources."""
    spec = Spec(id="movies", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("name", Primitive.TEXT, required=True)
    movie.slot("year", Primitive.INTEGER)
    movie.slot("runtime_minutes", Primitive.INTEGER)

    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    spec.bind(tmdb, movie, identifier=movie["canonical_id"], accuracy=0.7)
    return spec


def _write_claim(
    pg,
    spec: Spec,
    binding,
    rows: list[dict],
    *,
    schema: str,
    enforce: bool = False,
) -> None:
    bw = emit_batch_write(
        spec,
        [ClassWrites(binding=binding, rows=rows, use_mappings=False)],
        schema=schema,
        enforce=enforce,
    )
    exec_script(pg, bw.sql, bw.params)


# ---------------------------------------------------------------------------
# Initial deploy
# ---------------------------------------------------------------------------


def test_emit_ddl_creates_real_tables(pg, schema):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name",
            (schema,),
        )
        tables = [r[0] for r in cur.fetchall()]
    assert "movie" in tables
    assert "movie_bindings" in tables
    assert "source_accuracy" in tables


def test_emit_ddl_creates_resolved_view(pg, schema):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            "SELECT viewname FROM pg_views WHERE schemaname = %s",
            (schema,),
        )
        views = [r[0] for r in cur.fetchall()]
    assert "movie_resolved" in views


def test_emit_ddl_creates_indexes(pg, schema):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'movie_bindings'",
            (schema,),
        )
        idxs = {r[0] for r in cur.fetchall()}
    assert "movie_bindings_current_idx" in idxs
    assert "movie_bindings_source_idx" in idxs


def test_trust_seed_populates_source_accuracy(pg, schema):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT source_name, class_name, accuracy "
            f"FROM {schema}.source_accuracy ORDER BY source_name"
        )
        rows = cur.fetchall()
    assert rows == [("imdb", "Movie", 0.85), ("tmdb", "Movie", 0.7)]


# ---------------------------------------------------------------------------
# Write + resolve
# ---------------------------------------------------------------------------


def test_resolved_view_picks_higher_accuracy_source(pg, schema):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    tmdb_b = next(b for b in spec.source_bindings if b.source.name == "tmdb")

    # IMDB says year=1925; TMDB says year=1924 (a curator-known mistake).
    # IMDB has higher accuracy (0.85 vs 0.7), so its value should win.
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "potemkin",
                "source_identifier": "tt001",
                "name": "Battleship Potemkin",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )
    _write_claim(
        pg,
        spec,
        tmdb_b,
        [
            {
                "canonical_id": "potemkin",
                "source_identifier": "tmdb-x",
                "name": "Battleship Potemkin",
                "year": 1924,
                "runtime_minutes": 73,
            },
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year, runtime_minutes "
            f"FROM {schema}.movie_resolved WHERE canonical_id = 'potemkin'"
        )
        year, runtime = cur.fetchone()
    assert year == 1925
    assert runtime == 75


def test_resolved_view_falls_back_per_slot(pg, schema):
    """If IMDB has a NULL for `runtime_minutes` but TMDB has a value,
    TMDB wins for that slot even though IMDB has higher overall
    accuracy. Per-slot argmax, not per-row."""
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    tmdb_b = next(b for b in spec.source_bindings if b.source.name == "tmdb")

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt001",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": None,
            },
        ],
        schema=schema,
    )
    _write_claim(
        pg,
        spec,
        tmdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tmdb-1",
                "name": "M1",
                "year": None,
                "runtime_minutes": 73,
            },
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year, runtime_minutes FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        year, runtime = cur.fetchone()
    assert year == 1925  # IMDB wins year
    assert runtime == 73  # TMDB wins runtime (IMDB null)


def test_raw_payload_preserves_unmapped_fields(pg, schema):
    """Fields not declared as slots ride along in raw_payload."""
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt001",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
                "box_office": 500000,  # not a slot
                "director_name": "Eisenstein",  # not a slot
            },
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT raw_payload->>'box_office', raw_payload->>'director_name' "
            f"FROM {schema}.movie_bindings WHERE canonical_id = 'm1'"
        )
        box, director = cur.fetchone()
    assert box == "500000"
    assert director == "Eisenstein"


def test_scd2_close_out_on_repeated_write(pg, schema):
    """Writing the same (canonical_id, source, source_identifier) again
    closes out the prior row and inserts a new one."""
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1926,
                "runtime_minutes": 80,
            },
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year, valid_to IS NULL AS is_current "
            f"FROM {schema}.movie_bindings "
            f"WHERE canonical_id = 'm1' AND source_name = 'imdb' "
            f"ORDER BY valid_from"
        )
        rows = cur.fetchall()
    assert len(rows) == 2
    assert rows[0] == (1925, False)  # closed out
    assert rows[1] == (1926, True)  # current

    # Resolved view sees only the current row.
    with pg.cursor() as cur:
        cur.execute(f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'")
        assert cur.fetchone()[0] == 1926


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


def test_user_correction_wins_over_declared_sources(pg, schema):
    spec = _movies_only_spec()
    spec.enable_corrections()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    corr_b = spec.corrections_binding_for(next(c for c in spec.classes if c.name == "Movie"))

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )
    # Curator says year should actually be 1928.
    _write_claim(
        pg,
        spec,
        corr_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "curator-42",
                "name": None,
                "year": 1928,
                "runtime_minutes": None,
            },
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year, runtime_minutes FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        year, runtime = cur.fetchone()
    assert year == 1928  # correction wins
    assert runtime == 75  # IMDB wins (correction null)


def test_correction_withdraw_falls_back_to_source(pg, schema):
    spec = _movies_only_spec()
    spec.enable_corrections()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    movie = next(c for c in spec.classes if c.name == "Movie")
    corr_b = spec.corrections_binding_for(movie)

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )
    _write_claim(
        pg,
        spec,
        corr_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "curator-42",
                "name": None,
                "year": 1928,
                "runtime_minutes": None,
            },
        ],
        schema=schema,
    )

    # Withdraw the correction.
    sql = emit_close_out(
        spec,
        class_name="Movie",
        source_name=CORRECTIONS_SOURCE_NAME,
        schema=schema,
    )
    exec_with_params(
        pg,
        sql,
        {
            "canonical_id": "m1",
            "source_identifier": "curator-42",
        },
    )

    with pg.cursor() as cur:
        cur.execute(f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'")
        assert cur.fetchone()[0] == 1925  # back to IMDB


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------


def test_constraint_validation_finds_violations(pg, schema):
    spec = _movies_only_spec()
    movie = next(c for c in spec.classes if c.name == "Movie")
    spec.add_constraint(
        "year_sane",
        primary=movie,
        body=movie.col.year >= 1888,
    )
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "ok",
                "source_identifier": "tt1",
                "name": "Real Movie",
                "year": 1925,
                "runtime_minutes": 75,
            },
            {
                "canonical_id": "bad",
                "source_identifier": "tt2",
                "name": "Anachronism",
                "year": 1700,
                "runtime_minutes": 60,
            },
        ],
        schema=schema,
    )

    ((name, validation_sql),) = emit_validation(spec, schema=schema)
    assert name == "year_sane"

    with pg.cursor() as cur:
        cur.execute(validation_sql)
        violations = cur.fetchall()
    pks = {row[-1] for row in violations}  # last col is offending_pk
    assert pks == {"bad"}


# ---------------------------------------------------------------------------
# Spec evolution
# ---------------------------------------------------------------------------


def test_evolve_add_slot_preserves_existing_data(pg, schema, query_fn):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )

    # Evolve: add `original_language` slot.
    spec2 = _movies_only_spec()
    movie = next(c for c in spec2.classes if c.name == "Movie")
    movie.slot("original_language", Primitive.TEXT)

    ops = diff_against_db(spec2, query_fn, schema=schema)
    assert any(op.description == "add_column_movie_original_language" for op in ops)
    exec_many(pg, [op.sql for op in ops])

    # Existing row preserved; new column is NULL.
    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year, original_language FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        year, lang = cur.fetchone()
    assert year == 1925
    assert lang is None


def test_evolve_change_accuracy_changes_winner(pg, schema, query_fn):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    tmdb_b = next(b for b in spec.source_bindings if b.source.name == "tmdb")
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )
    _write_claim(
        pg,
        spec,
        tmdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tmdb-1",
                "name": "M1",
                "year": 1928,
                "runtime_minutes": 73,
            },
        ],
        schema=schema,
    )

    # Before: IMDB wins (0.85 > 0.7) → year=1925.
    with pg.cursor() as cur:
        cur.execute(f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'")
        assert cur.fetchone()[0] == 1925

    # Operator decides TMDB is more reliable than IMDB.
    spec2 = _movies_only_spec()
    for b in spec2.source_bindings:
        if b.source.name == "imdb":
            b.accuracy = 0.6
        if b.source.name == "tmdb":
            b.accuracy = 0.9

    ops = diff_against_db(spec2, query_fn, schema=schema)
    upserts = [op for op in ops if op.target == "trust_seed"]
    assert len(upserts) == 2  # both bindings changed
    exec_many(pg, [op.sql for op in ops])

    # After: TMDB wins → year=1928.
    with pg.cursor() as cur:
        cur.execute(f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'")
        assert cur.fetchone()[0] == 1928


def test_evolve_rename_slot_preserves_data(pg, schema, query_fn):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "canonical_id": "m1",
                "source_identifier": "tt1",
                "name": "M1",
                "year": 1925,
                "runtime_minutes": 75,
            },
        ],
        schema=schema,
    )

    # Evolve: rename runtime_minutes → length_min.
    spec2 = Spec(id="movies", version="0.2")
    movie = spec2.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("name", Primitive.TEXT, required=True)
    movie.slot("year", Primitive.INTEGER)
    movie.slot("length_min", Primitive.INTEGER)  # was runtime_minutes
    imdb = spec2.add_source("imdb")
    tmdb = spec2.add_source("tmdb")
    spec2.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    spec2.bind(tmdb, movie, identifier=movie["canonical_id"], accuracy=0.7)

    ops = diff_against_db(
        spec2,
        query_fn,
        schema=schema,
        allow_destructive=True,
        renames={"Movie": {"runtime_minutes": "length_min"}},
    )
    rename_ops = [op for op in ops if op.description.startswith("rename_column_")]
    assert len(rename_ops) == 2  # canonical + bindings
    exec_many(pg, [op.sql for op in ops])

    # Data preserved under the new name.
    with pg.cursor() as cur:
        cur.execute(f"SELECT length_min FROM {schema}.movie_resolved WHERE canonical_id = 'm1'")
        assert cur.fetchone()[0] == 75


def test_evolve_drop_slot_with_destructive_opt_in(pg, schema, query_fn):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    # Drop `runtime_minutes` from the spec.
    spec2 = Spec(id="movies", version="0.2")
    movie = spec2.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    movie.slot("name", Primitive.TEXT, required=True)
    movie.slot("year", Primitive.INTEGER)
    imdb = spec2.add_source("imdb")
    spec2.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)

    # Without destructive opt-in: no drop emitted (filtered out).
    ops = diff_against_db(spec2, query_fn, schema=schema)
    assert not any(op.description.startswith("drop_column_movie_runtime") for op in ops)

    # With destructive opt-in: drop emitted.
    ops = diff_against_db(spec2, query_fn, schema=schema, allow_destructive=True)
    drops = [op for op in ops if op.description.startswith("drop_column_")]
    assert any("drop_column_movie_runtime_minutes" == op.description for op in drops)
    exec_many(pg, [op.sql for op in ops])

    # Column gone.
    with pg.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'movie' "
            "ORDER BY column_name",
            (schema,),
        )
        cols = {r[0] for r in cur.fetchall()}
    assert "runtime_minutes" not in cols
    assert "year" in cols


# ---------------------------------------------------------------------------
# Full Flyway shape applied in order
# ---------------------------------------------------------------------------


def test_flyway_files_apply_in_order(pg, schema):
    """Render the initial deploy as Flyway-shaped files, then apply
    each file in name-sorted order — same order Flyway would run them.
    """
    spec = _movies_only_spec()
    spec.enable_corrections()

    ops = diff_against_db(
        spec,
        lambda sql, params: [],
        schema=schema,
    )
    files = emit_flyway_files(ops, version="20260514_001", slug="initial")

    # Flyway runs V first, then R files in filename order.
    applied = sorted(files.keys(), key=lambda n: (not n.startswith("V"), n))
    for filename in applied:
        exec_script(pg, files[filename])

    # Smoke check: resolver view exists, trust seeded for every binding.
    with pg.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {schema}.source_accuracy")
        assert cur.fetchone()[0] == 3  # imdb + tmdb + _user_corrections

        cur.execute(
            "SELECT viewname FROM pg_views WHERE schemaname = %s",
            (schema,),
        )
        views = {r[0] for r in cur.fetchall()}
    assert "movie_resolved" in views
