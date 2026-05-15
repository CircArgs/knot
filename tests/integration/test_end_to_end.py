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
    Spec,
    types,
)
from knot.compile import (
    ClassWrites,
    diff_against_db,
    emit_batch_write,
    emit_close_out,
    emit_ddl,
    emit_trust_seed,
    emit_validation,
)
from tests.integration.conftest import exec_many, exec_with_params

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
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("name", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)

    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    imdb.bind(movie, base_trust=0.85)
    tmdb.bind(movie, base_trust=0.7)
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
        [ClassWrites(binding=binding, rows=rows)],
        schema=schema,
        enforce=enforce,
    )
    with pg.cursor() as cur:
        for sql, params in bw.statements:
            cur.execute(sql, params)


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
    assert "source_trust" in tables


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


def test_trust_seed_populates_source_trust(pg, schema):
    """One row per (source, class, non-identifier slot)."""
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT source_name, class_name, slot_name, trust "
            f"FROM {schema}.source_trust ORDER BY source_name, slot_name"
        )
        rows = cur.fetchall()
    # Movie has 3 non-identifier slots: name, year, runtime_minutes.
    # Two sources (imdb, tmdb) × 3 slots = 6 rows.
    assert len(rows) == 6
    imdb_rows = [r for r in rows if r[0] == "imdb"]
    tmdb_rows = [r for r in rows if r[0] == "tmdb"]
    assert all(r[3] == 0.85 for r in imdb_rows)
    assert all(r[3] == 0.7 for r in tmdb_rows)


# ---------------------------------------------------------------------------
# Write + resolve
# ---------------------------------------------------------------------------


def test_resolved_view_picks_higher_accuracy_source(pg, schema):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

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


def test_all_sources_view_aggregates_per_source_jsonb(pg, schema):
    """The <class>_all_sources provenance view ships one jsonb per
    slot keyed by source name, with {value, trust} payload — so both
    sources show up for ``year`` even though only one wins in the
    resolved view."""
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
                "runtime_minutes": None,  # partial coverage → filtered out
            },
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year, runtime_minutes "
            f"FROM {schema}.movie_all_sources WHERE canonical_id = 'potemkin'"
        )
        year_jsonb, runtime_jsonb = cur.fetchone()

    # Both sources present in year (both contributed non-null).
    assert set(year_jsonb.keys()) == {"imdb", "tmdb"}
    assert year_jsonb["imdb"] == {"value": 1925, "trust": 0.85}
    assert year_jsonb["tmdb"] == {"value": 1924, "trust": 0.7}

    # Only IMDB present in runtime_minutes (TMDB contributed NULL → filtered).
    assert set(runtime_jsonb.keys()) == {"imdb"}
    assert runtime_jsonb["imdb"] == {"value": 75, "trust": 0.85}


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
        cur.execute(
            f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        assert cur.fetchone()[0] == 1926


# ---------------------------------------------------------------------------
# Corrections
# ---------------------------------------------------------------------------


def test_user_correction_wins_over_declared_sources(pg, schema):
    spec = _movies_only_spec()
    spec.enable_corrections()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    corr_b = spec.corrections_binding_for(
        next(c for c in spec.classes if c.name == "Movie")
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
        cur.execute(
            f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
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
    movie.slot("original_language", types.TEXT)

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


def test_operator_tunes_trust_changes_winner(pg, schema, query_fn):
    """Trust values live in postgres; operators tune them via plain
    UPDATE statements. INSERT-only seed semantics mean the spec is
    *not* the authoritative knob at runtime — once the table is
    seeded, the operator owns it."""
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
        cur.execute(
            f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        assert cur.fetchone()[0] == 1925

    # Operator decides TMDB's `year` is more reliable than IMDB's —
    # plain UPDATE against the runtime table. No redeploy.
    with pg.cursor() as cur:
        cur.execute(
            f"UPDATE {schema}.source_trust SET trust = 0.9 "
            f"WHERE source_name = 'tmdb' AND class_name = 'Movie' AND slot_name = 'year'"
        )

    # After: TMDB wins → year=1928.
    with pg.cursor() as cur:
        cur.execute(
            f"SELECT year FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        assert cur.fetchone()[0] == 1928


def test_trust_seed_does_not_clobber_operator_tuning(pg, schema, query_fn):
    """The migration emitter is INSERT-only: redeploying the spec must
    leave operator-tuned trust values alone."""
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    # Operator tunes a trust value at runtime.
    with pg.cursor() as cur:
        cur.execute(
            f"UPDATE {schema}.source_trust SET trust = 0.42 "
            f"WHERE source_name = 'imdb' AND class_name = 'Movie' AND slot_name = 'year'"
        )

    # Redeploy (spec unchanged from initial); seed must NOT overwrite.
    spec2 = _movies_only_spec()
    ops = diff_against_db(spec2, query_fn, schema=schema)
    trust_ops = [op for op in ops if op.target == "trust_seed"]
    # No (source, class, slot) rows are new → nothing to INSERT.
    assert trust_ops == []

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT trust FROM {schema}.source_trust "
            f"WHERE source_name = 'imdb' AND class_name = 'Movie' AND slot_name = 'year'"
        )
        assert cur.fetchone()[0] == 0.42  # operator's value preserved


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
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("name", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("length_min", types.INTEGER)  # was runtime_minutes
    imdb = spec2.add_source("imdb")
    tmdb = spec2.add_source("tmdb")
    imdb.bind(movie, base_trust=0.85)
    tmdb.bind(movie, base_trust=0.7)

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
        cur.execute(
            f"SELECT length_min FROM {schema}.movie_resolved WHERE canonical_id = 'm1'"
        )
        assert cur.fetchone()[0] == 75


def test_evolve_drop_slot_with_destructive_opt_in(pg, schema, query_fn):
    spec = _movies_only_spec()
    _deploy(pg, spec, schema)

    # Drop `runtime_minutes` from the spec.
    spec2 = Spec(id="movies", version="0.2")
    movie = spec2.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("name", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    imdb = spec2.add_source("imdb")
    imdb.bind(movie, base_trust=0.85)

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
# init_sql — unified one-string deploy / migrate
# ---------------------------------------------------------------------------


def test_init_sql_from_scratch_creates_everything(pg, schema):
    """Single-call deploy from an empty schema: ``spec.init_sql()`` with
    no query_fn emits one SQL script that creates everything."""
    spec = _movies_only_spec()
    spec.enable_corrections()

    sql = spec.init_sql(schema=schema)
    pg.execute(sql)

    # Trust seeded per (source, class, slot).
    # Movie has 3 non-identifier slots; 3 sources × 3 slots = 9 rows.
    with pg.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {schema}.source_trust")
        assert cur.fetchone()[0] == 9

        cur.execute(
            "SELECT viewname FROM pg_views WHERE schemaname = %s",
            (schema,),
        )
        views = {r[0] for r in cur.fetchall()}
    assert "movie_resolved" in views


def test_init_sql_diff_mode_emits_only_changes(pg, schema, query_fn):
    """``spec.init_sql(query_fn=…)`` introspects the live DB and emits
    just the migration delta — no churn for a fully-deployed spec."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    # Same spec again, this time diffed against the live DB.
    delta = spec.init_sql(query_fn=query_fn, schema=schema)
    # The only ops should be CREATE OR REPLACE VIEW for the resolved
    # views (the migration emitter always re-emits views idempotently
    # so they reflect the current spec body).
    assert "CREATE TABLE" not in delta
    assert "ALTER TABLE" not in delta


# ---------------------------------------------------------------------------
# ER helpers — assign_canonical, recanonicalize, er_metadata
# ---------------------------------------------------------------------------


def test_unresolved_ingest_invisible_until_canonical_assigned(pg, schema):
    """Ingest a binding row with canonical_id=NULL; resolved view skips
    it; assign_canonical makes it visible."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")

    # No canonical_id yet — ER hasn't claimed it.
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "source_identifier": "tt001",
                "canonical_id": None,
                "name": "Pulp Fiction",
                "year": 1994,
                "runtime_minutes": 154,
            }
        ],
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {schema}.movie_bindings")
        assert cur.fetchone()[0] == 1
        cur.execute(f"SELECT COUNT(*) FROM {schema}.movie_resolved")
        assert cur.fetchone()[0] == 0  # invisible until ER claims it

    # ER assigns canonical_id.
    sql, params = spec.assign_canonical(
        movie,
        "m_pulp",
        source_name="imdb",
        source_identifier="tt001",
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(f"SELECT canonical_id, name FROM {schema}.movie_resolved")
        assert cur.fetchall() == [("m_pulp", "Pulp Fiction")]


def test_assign_canonical_does_not_clobber_existing_id(pg, schema):
    """Re-running assign_canonical on an already-assigned row is a no-op."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "source_identifier": "tt001",
                "canonical_id": None,
                "name": "X",
                "year": 2000,
                "runtime_minutes": 90,
            }
        ],
        schema=schema,
    )
    sql, params = spec.assign_canonical(
        movie,
        "m_first",
        source_name="imdb",
        source_identifier="tt001",
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    # Try to assign a DIFFERENT id — the WHERE clause's IS NULL check
    # means nothing happens.
    sql, params = spec.assign_canonical(
        movie,
        "m_second",
        source_name="imdb",
        source_identifier="tt001",
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(f"SELECT canonical_id FROM {schema}.movie_resolved")
        assert cur.fetchone()[0] == "m_first"  # untouched


def test_recanonicalize_preserves_scd2_history(pg, schema):
    """Reassigning canonical_id keeps the old binding row (closed) plus
    a new open binding row with the corrected id."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "source_identifier": "tt001",
                "canonical_id": "m_wrong",
                "name": "X",
                "year": 2000,
                "runtime_minutes": 90,
            }
        ],
        schema=schema,
    )

    # ER decided m_wrong should actually be m_correct.
    sql, params = spec.recanonicalize(
        movie,
        "m_correct",
        source_name="imdb",
        source_identifier="tt001",
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, valid_to IS NULL FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001' "
            f"ORDER BY valid_from"
        )
        rows = cur.fetchall()
    assert rows == [("m_wrong", False), ("m_correct", True)]

    # Resolved view shows the corrected canonical_id only.
    with pg.cursor() as cur:
        cur.execute(f"SELECT canonical_id, name FROM {schema}.movie_resolved")
        assert cur.fetchall() == [("m_correct", "X")]


def test_assign_canonical_stamps_er_metadata(pg, schema):
    """assign_canonical with er_metadata writes the dict into the
    binding row's er_metadata jsonb column."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "source_identifier": "tt001",
                "canonical_id": None,
                "name": "Reservoir Dogs",
                "year": 1992,
                "runtime_minutes": 99,
            }
        ],
        schema=schema,
    )

    sql, params = spec.assign_canonical(
        movie,
        "m_reservoirdogs",
        source_name="imdb",
        source_identifier="tt001",
        er_metadata={
            "run_id": "r42",
            "method": "exact_title_year",
            "confidence": 0.93,
        },
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, er_metadata FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001'"
        )
        canonical_id, er_metadata = cur.fetchone()
    assert canonical_id == "m_reservoirdogs"
    assert er_metadata == {
        "run_id": "r42",
        "method": "exact_title_year",
        "confidence": 0.93,
    }


def test_recanonicalize_carries_er_metadata_forward_by_default(pg, schema):
    """When recanonicalize is called without er_metadata, the new row
    inherits the closed row's er_metadata verbatim."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "source_identifier": "tt001",
                "canonical_id": None,
                "name": "Reservoir Dogs",
                "year": 1992,
                "runtime_minutes": 99,
            }
        ],
        schema=schema,
    )

    # First stamp: ER assigns + writes metadata
    sql, params = spec.assign_canonical(
        movie,
        "m_wrong",
        source_name="imdb",
        source_identifier="tt001",
        er_metadata={"run_id": "r1", "method": "exact_title_year"},
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    # Recanonicalize without er_metadata kwarg — new row inherits.
    sql, params = spec.recanonicalize(
        movie,
        "m_correct",
        source_name="imdb",
        source_identifier="tt001",
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, er_metadata FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001' "
            f"ORDER BY valid_from"
        )
        rows = cur.fetchall()
    assert rows == [
        ("m_wrong", {"run_id": "r1", "method": "exact_title_year"}),
        ("m_correct", {"run_id": "r1", "method": "exact_title_year"}),
    ]


def test_recanonicalize_overrides_er_metadata_when_provided(pg, schema):
    """Recanonicalize with er_metadata stamps the new row with a
    fresh payload; the closed row keeps the original."""
    spec = _movies_only_spec()
    pg.execute(spec.init_sql(schema=schema))

    movie = next(c for c in spec.classes if c.name == "Movie")
    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")

    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
                "source_identifier": "tt001",
                "canonical_id": None,
                "name": "Reservoir Dogs",
                "year": 1992,
            }
        ],
        schema=schema,
    )
    sql, params = spec.assign_canonical(
        movie,
        "m_wrong",
        source_name="imdb",
        source_identifier="tt001",
        er_metadata={"run_id": "r1", "method": "exact"},
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    sql, params = spec.recanonicalize(
        movie,
        "m_correct",
        source_name="imdb",
        source_identifier="tt001",
        er_metadata={"run_id": "r2", "method": "human_review"},
        schema=schema,
    )
    with pg.cursor() as cur:
        cur.execute(sql, params)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, er_metadata FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001' "
            f"ORDER BY valid_from"
        )
        rows = cur.fetchall()
    assert rows == [
        ("m_wrong", {"run_id": "r1", "method": "exact"}),
        ("m_correct", {"run_id": "r2", "method": "human_review"}),
    ]
