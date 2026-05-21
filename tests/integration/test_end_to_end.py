"""End-to-end integration tests against live postgres.

Each test gets a fresh schema. We:
  1. Build a Spec
  2. Apply Spec.ddl() to the schema + upsert runtime weights
     via binding.upsert_weight_sql()
  3. Exercise the write path (binding.write_sql / binding.retract_sql)
  4. Query the resolved view + validation SELECTs and assert behavior

The whole point is to verify the SQL we emit is not just well-formed
but semantically correct against postgres 16.

Schema evolution against a live DB is delegated to external migration
tools (sqldef / Atlas / dbmate); see CLAUDE.md §"Schema deployment".
"""

from __future__ import annotations

from knot import Spec, types
from knot.compile import emit_ddl, emit_validation
from tests.integration.conftest import exec_many, exec_with_params

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Per-source runtime weight; upserted in _deploy after the schema
# lands. Same shape as the resolved view's per-source argmax —
# imdb beats tmdb when both report the same slot. The
# ``_user_corrections`` synthetic source gets a dominating weight so
# corrections beat any declared source.
_SOURCE_WEIGHTS = {"imdb": 0.85, "tmdb": 0.7, "_user_corrections": 1e6}


def _deploy(pg, spec: Spec, schema: str) -> None:
    """Apply emit_ddl to ``schema``, then upsert runtime weights for
    every (source, slot) pair on a binding into ``source_weight`` so
    the resolver's argmax has a defined ordering."""
    exec_many(pg, emit_ddl(spec, schema=schema))
    with pg.cursor() as cur:
        for binding in spec.source_bindings:
            weight = _SOURCE_WEIGHTS.get(binding.source.name, 0.0)
            upsert = binding.upsert_weight_sql()
            ident = binding.identifier_slot.name
            for slot in binding.class_.effective_slots():
                if slot.name == ident:
                    continue
                cur.execute(upsert, {"slot_name": slot.name, "weight": weight})


def _movies_only_spec(schema: str) -> Spec:
    """Single class Movie with year + runtime, IMDB + TMDB sources."""
    spec = Spec(identifier_slot_name="canonical_id", schema=schema)
    movie = spec.add_class("Movie")
    movie.slot("name", types.TEXT, required=True)
    movie.slot("year", types.INTEGER)
    movie.slot("runtime_minutes", types.INTEGER)

    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    imdb.bind(movie)
    tmdb.bind(movie)
    return spec


def _write_claim(
    pg,
    spec: Spec,
    binding,
    rows: list[dict],
    *,
    schema: str,
) -> None:
    sql = binding.write_sql()
    payload = _json(rows)
    with pg.cursor() as cur:
        cur.execute(sql, {"rows": payload})


def _json(value) -> str:
    """Serialize for psycopg's jsonb binding (host responsibility).
    Psycopg2 wants a string; psycopg3 auto-adapts dicts/lists."""
    import json as _json_mod

    return _json_mod.dumps(value)


def _assign(
    pg,
    binding,
    *,
    canonical_id: str,
    source_identifier: str,
    er_metadata: dict | None = None,
    schema: str,
) -> None:
    with pg.cursor() as cur:
        cur.execute(
            binding.assign_canonical_sql(),
            {
                "canonical_id": canonical_id,
                "source_identifier": source_identifier,
                "er_metadata": _json(er_metadata) if er_metadata is not None else None,
            },
        )


def _recan(
    pg,
    binding,
    *,
    new_canonical_id: str,
    source_identifier: str,
    er_metadata: dict | None = None,
    schema: str,
) -> None:
    with pg.cursor() as cur:
        cur.execute(
            binding.recanonicalize_sql(),
            {
                "new_canonical_id": new_canonical_id,
                "source_identifier": source_identifier,
                "er_metadata": _json(er_metadata) if er_metadata is not None else None,
            },
        )


# ---------------------------------------------------------------------------
# Initial deploy
# ---------------------------------------------------------------------------


def test_emit_ddl_creates_real_tables(pg, schema):
    """Per class: bindings table only (no canonical table).
    Plus the global source_weight table."""
    spec = _movies_only_spec(schema)
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s ORDER BY table_name",
            (schema,),
        )
        tables = [r[0] for r in cur.fetchall()]
    assert "movie_bindings" in tables
    assert "source_weight" in tables
    # No canonical table — bindings is the only per-class table.
    assert "movie" not in tables


def test_emit_ddl_creates_resolved_view(pg, schema):
    spec = _movies_only_spec(schema)
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            "SELECT viewname FROM pg_views WHERE schemaname = %s",
            (schema,),
        )
        views = [r[0] for r in cur.fetchall()]
    assert "movie_resolved" in views


def test_emit_ddl_creates_indexes(pg, schema):
    spec = _movies_only_spec(schema)
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'movie_bindings'",
            (schema,),
        )
        idxs = {r[0] for r in cur.fetchall()}
    # One btree on canonical_id; PK covers (source, source_id) lookups.
    assert "movie_bindings_canonical_idx" in idxs


def test_weight_seed_populates_source_weight(pg, schema):
    """One row per (source, class, non-identifier slot)."""
    spec = _movies_only_spec(schema)
    _deploy(pg, spec, schema)

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT source_name, class_name, slot_name, weight "
            f"FROM {schema}.source_weight ORDER BY source_name, slot_name"
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
    spec = _movies_only_spec(schema)
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
    slot keyed by source name, with {value, weight} payload — so both
    sources show up for ``year`` even though only one wins in the
    resolved view."""
    spec = _movies_only_spec(schema)
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
    assert year_jsonb["imdb"] == {"value": 1925, "weight": 0.85}
    assert year_jsonb["tmdb"] == {"value": 1924, "weight": 0.7}

    # Only IMDB present in runtime_minutes (TMDB contributed NULL → filtered).
    assert set(runtime_jsonb.keys()) == {"imdb"}
    assert runtime_jsonb["imdb"] == {"value": 75, "weight": 0.85}


def test_resolved_view_falls_back_per_slot(pg, schema):
    """If IMDB has a NULL for `runtime_minutes` but TMDB has a value,
    TMDB wins for that slot even though IMDB has higher overall
    accuracy. Per-slot argmax, not per-row."""
    spec = _movies_only_spec(schema)
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
    spec = _movies_only_spec(schema)
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


def test_upsert_in_place_on_repeated_write(pg, schema):
    """Writing the same (source, source_identifier) again upserts in
    place — one row per (source, source_id), new values overwrite,
    canonical_id is preserved across re-ingests (ER-owned)."""
    spec = _movies_only_spec(schema)
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
    # Re-ingest with updated year — should overwrite, not stack.
    # canonical_id in the payload is intentionally NULL (the source
    # doesn't know it); the upsert preserves the ER-stamped m1.
    _write_claim(
        pg,
        spec,
        imdb_b,
        [
            {
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
            f"SELECT canonical_id, year, runtime_minutes "
            f"FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt1'"
        )
        rows = cur.fetchall()
    assert len(rows) == 1, "upsert: one row per (source, source_id), not stacked"
    assert rows[0] == ("m1", 1926, 80), (
        "year/runtime overwritten; canonical_id preserved"
    )

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
    spec = _movies_only_spec(schema)
    spec.enable_corrections()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    movie = spec.classes["Movie"]
    corr_b = movie.corrections_binding()

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
    spec = _movies_only_spec(schema)
    spec.enable_corrections()
    _deploy(pg, spec, schema)

    imdb_b = next(b for b in spec.source_bindings if b.source.name == "imdb")
    movie = spec.classes["Movie"]
    corr_b = movie.corrections_binding()

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
    exec_with_params(
        pg,
        corr_b.retract_sql(),
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
    spec = _movies_only_spec(schema)
    movie = spec.classes["Movie"]
    movie.add_constraint("year_sane", body=movie.col.year >= 1888)
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

    # include_builtins=False so we only see the user-declared constraint
    # under test (otherwise built-in fk-orphan / required-null SELECTs
    # would also appear in the list).
    ((name, validation_sql),) = emit_validation(
        spec, schema=schema, include_builtins=False
    )
    assert name == "year_sane"

    with pg.cursor() as cur:
        cur.execute(validation_sql)
        violations = cur.fetchall()
    pks = {row[-1] for row in violations}  # last col is offending_pk
    assert pks == {"bad"}


# ---------------------------------------------------------------------------
# ER helpers — assign_canonical, recanonicalize, er_metadata
# ---------------------------------------------------------------------------


def test_unresolved_ingest_invisible_until_canonical_assigned(pg, schema):
    """Ingest a binding row with canonical_id=NULL; resolved view skips
    it; assign_canonical makes it visible."""
    spec = _movies_only_spec(schema)
    pg.execute(spec.ddl())

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
    _assign(pg, imdb_b, canonical_id="m_pulp", source_identifier="tt001", schema=schema)

    with pg.cursor() as cur:
        cur.execute(f"SELECT canonical_id, name FROM {schema}.movie_resolved")
        assert cur.fetchall() == [("m_pulp", "Pulp Fiction")]


def test_assign_canonical_does_not_clobber_existing_id(pg, schema):
    """Re-running assign_canonical on an already-assigned row is a no-op."""
    spec = _movies_only_spec(schema)
    pg.execute(spec.ddl())

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
    _assign(
        pg, imdb_b, canonical_id="m_first", source_identifier="tt001", schema=schema
    )

    # Try to assign a DIFFERENT id — the WHERE clause's IS NULL check
    # means nothing happens.
    _assign(
        pg, imdb_b, canonical_id="m_second", source_identifier="tt001", schema=schema
    )

    with pg.cursor() as cur:
        cur.execute(f"SELECT canonical_id FROM {schema}.movie_resolved")
        assert cur.fetchone()[0] == "m_first"  # untouched


def test_recanonicalize_replaces_canonical_in_place(pg, schema):
    """Reassigning canonical_id overwrites in place — no SCD2 history.
    One row per (source, source_id); old canonical is gone after the
    recanonicalize completes."""
    spec = _movies_only_spec(schema)
    pg.execute(spec.ddl())

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
    _recan(
        pg,
        imdb_b,
        new_canonical_id="m_correct",
        source_identifier="tt001",
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001'"
        )
        rows = cur.fetchall()
    assert rows == [("m_correct",)], "one row in place, replaced canonical_id"

    # Resolved view shows the corrected canonical_id only.
    with pg.cursor() as cur:
        cur.execute(f"SELECT canonical_id, name FROM {schema}.movie_resolved")
        assert cur.fetchall() == [("m_correct", "X")]


def test_assign_canonical_stamps_er_metadata(pg, schema):
    """assign_canonical with er_metadata writes the dict into the
    binding row's er_metadata jsonb column."""
    spec = _movies_only_spec(schema)
    pg.execute(spec.ddl())

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

    _assign(
        pg,
        imdb_b,
        canonical_id="m_reservoirdogs",
        source_identifier="tt001",
        er_metadata={
            "run_id": "r42",
            "method": "exact_title_year",
            "confidence": 0.93,
        },
        schema=schema,
    )

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


def test_recanonicalize_keeps_er_metadata_by_default(pg, schema):
    """When recanonicalize is called without er_metadata, the binding
    row keeps its existing er_metadata verbatim (COALESCE preserves
    the existing column value when the param is NULL)."""
    spec = _movies_only_spec(schema)
    pg.execute(spec.ddl())

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
    _assign(
        pg,
        imdb_b,
        canonical_id="m_wrong",
        source_identifier="tt001",
        er_metadata={"run_id": "r1", "method": "exact_title_year"},
        schema=schema,
    )

    # Recanonicalize without er_metadata kwarg — new row inherits.
    _recan(
        pg,
        imdb_b,
        new_canonical_id="m_correct",
        source_identifier="tt001",
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, er_metadata FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001'"
        )
        rows = cur.fetchall()
    assert rows == [("m_correct", {"run_id": "r1", "method": "exact_title_year"})]


def test_recanonicalize_overrides_er_metadata_when_provided(pg, schema):
    """Recanonicalize with er_metadata stamps the new row with a
    fresh payload; the closed row keeps the original."""
    spec = _movies_only_spec(schema)
    pg.execute(spec.ddl())

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
    _assign(
        pg,
        imdb_b,
        canonical_id="m_wrong",
        source_identifier="tt001",
        er_metadata={"run_id": "r1", "method": "exact"},
        schema=schema,
    )
    _recan(
        pg,
        imdb_b,
        new_canonical_id="m_correct",
        source_identifier="tt001",
        er_metadata={"run_id": "r2", "method": "human_review"},
        schema=schema,
    )

    with pg.cursor() as cur:
        cur.execute(
            f"SELECT canonical_id, er_metadata FROM {schema}.movie_bindings "
            f"WHERE source_name = 'imdb' AND source_identifier = 'tt001'"
        )
        rows = cur.fetchall()
    assert rows == [("m_correct", {"run_id": "r2", "method": "human_review"})]
