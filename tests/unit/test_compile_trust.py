"""knot.compile.trust — source_trust DDL + INSERT-only seed."""

import sqlglot

from knot import Spec, types
from knot.compile import emit_ddl, emit_trust_seed


def test_trust_table_emitted_by_default(movie_spec):
    stmts = emit_ddl(movie_spec)
    trust = next(
        s for s in stmts if s.startswith("CREATE TABLE") and "source_trust" in s
    )
    assert "source_name text NOT NULL" in trust
    assert "class_name" in trust
    assert "slot_name" in trust
    assert "trust" in trust and "double precision" in trust
    assert "CHECK (trust >= 0 AND trust <= 1)" in trust
    assert "PRIMARY KEY (source_name, class_name, slot_name)" in trust
    sqlglot.parse_one(trust, dialect="postgres")


def test_trust_table_can_be_disabled(movie_spec):
    stmts = emit_ddl(movie_spec, emit_trust_table=False)
    assert not any("source_trust" in s and s.startswith("CREATE TABLE") for s in stmts)


def test_trust_table_idempotent_with_if_not_exists(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    trust = next(
        s for s in stmts if "source_trust" in s and s.startswith("CREATE TABLE")
    )
    assert trust.startswith("CREATE TABLE IF NOT EXISTS")


def test_trust_table_name_kwarg(movie_spec):
    stmts = emit_ddl(movie_spec, trust_table_name="custom_trust")
    trust = next(
        s for s in stmts if "custom_trust" in s and s.startswith("CREATE TABLE")
    )
    assert "knot_data.custom_trust" in trust
    # Resolver views should also reference the renamed table.
    view = next(s for s in stmts if "_resolved AS" in s)
    assert "knot_data.custom_trust" in view


def test_seed_emits_one_row_per_non_identifier_slot(movie_spec):
    """movie_spec has one binding (imdb → Movie) and Movie has slots
    canonical_id (identifier — no row), year, runtime_minutes, genres,
    name (inherited from Title). The identifier slot is excluded.
    """
    seeds = emit_trust_seed(movie_spec)
    slots = sorted(p[2] for _, p in seeds)
    assert slots == ["genres", "name", "runtime_minutes", "year"]
    for sql, params in seeds:
        assert "INSERT INTO knot_data.source_trust" in sql
        assert "ON CONFLICT (source_name, class_name, slot_name) DO NOTHING" in sql
        assert params[0] == "imdb"
        assert params[1] == "Movie"


def test_seed_uses_default_trust_for_unmapped_slots(movie_spec):
    """Slots without an explicit per-slot trust inherit default_trust=0.85."""
    seeds = emit_trust_seed(movie_spec)
    # 'genres' isn't mapped explicitly → default_trust
    genres = next(p for _, p in seeds if p[2] == "genres")
    assert genres[3] == 0.85


def test_seed_multi_source_one_class():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    imdb.bind(movie).set_default_trust(0.85)
    tmdb.bind(movie).set_default_trust(0.7)
    seeds = emit_trust_seed(spec)
    rows = sorted([(p[0], p[1], p[2], p[3]) for _, p in seeds])
    assert rows == [("imdb", "Movie", "year", 0.85), ("tmdb", "Movie", "year", 0.7)]


def test_seed_empty_spec_no_seeds():
    spec = Spec(id="m", version="0.1")
    assert emit_trust_seed(spec) == []


def test_seed_sql_parses_postgres(movie_spec):
    for sql, _ in emit_trust_seed(movie_spec):
        sqlglot.parse_one(sql, dialect="postgres")


def test_seed_schema_kwarg(movie_spec):
    seeds = emit_trust_seed(movie_spec, schema="alt", trust_table_name="custom")
    sql = seeds[0][0]
    assert "INSERT INTO alt.custom" in sql


def test_explicit_per_slot_trust_overrides_default():
    """``binding.set_trust(slot, value)`` overrides ``default_trust``
    for that one slot at seed time."""
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", types.TEXT, identifier=True)
    movie.slot("year", types.INTEGER)
    movie.slot("title", types.TEXT)
    imdb = spec.add_source("imdb")
    binding = imdb.bind(movie).set_default_trust(0.5)
    binding.set_trust("year", 0.95)
    seeds = emit_trust_seed(spec)
    year_row = next(p for _, p in seeds if p[2] == "year")
    title_row = next(p for _, p in seeds if p[2] == "title")
    assert year_row[3] == 0.95  # explicit
    assert title_row[3] == 0.5  # default_trust fallback
