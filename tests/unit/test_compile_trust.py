"""knot.compile.trust — source_accuracy DDL + seed UPSERTs."""

import sqlglot

from knot import Primitive, Spec
from knot.compile import emit_ddl, emit_trust_seed


def test_trust_table_emitted_by_default(movie_spec):
    stmts = emit_ddl(movie_spec)
    trust = next(
        s for s in stmts
        if s.startswith("CREATE TABLE") and "source_accuracy" in s
    )
    assert "source_name text NOT NULL" in trust
    assert "class_name  text NOT NULL" in trust
    assert "accuracy    double precision NOT NULL" in trust
    assert "CHECK (accuracy >= 0 AND accuracy <= 1)" in trust
    assert "PRIMARY KEY (source_name, class_name)" in trust
    sqlglot.parse_one(trust, dialect="postgres")


def test_trust_table_can_be_disabled(movie_spec):
    stmts = emit_ddl(movie_spec, emit_trust_table=False)
    assert not any("source_accuracy" in s and s.startswith("CREATE TABLE") for s in stmts)


def test_trust_table_idempotent_with_if_not_exists(movie_spec):
    stmts = emit_ddl(movie_spec, if_not_exists=True)
    trust = next(s for s in stmts if "source_accuracy" in s and s.startswith("CREATE TABLE"))
    assert trust.startswith("CREATE TABLE IF NOT EXISTS")


def test_trust_table_name_kwarg(movie_spec):
    stmts = emit_ddl(movie_spec, trust_table_name="custom_trust")
    trust = next(s for s in stmts if "custom_trust" in s and s.startswith("CREATE TABLE"))
    assert "knot_data.custom_trust" in trust
    # Resolver views should also reference the renamed table.
    view = next(s for s in stmts if "_resolved AS" in s)
    assert "knot_data.custom_trust" in view


def test_seed_emits_one_upsert_per_binding(movie_spec):
    seeds = emit_trust_seed(movie_spec)
    # movie_spec has one binding: imdb → Movie.
    assert len(seeds) == 1
    sql, params = seeds[0]
    assert "INSERT INTO knot_data.source_accuracy" in sql
    assert "ON CONFLICT (source_name, class_name)" in sql
    assert "DO UPDATE SET accuracy = EXCLUDED.accuracy" in sql
    assert params == ["imdb", "Movie", 0.85]


def test_seed_multi_source_one_class():
    spec = Spec(id="m", version="0.1")
    movie = spec.add_class("Movie")
    movie.slot("canonical_id", Primitive.TEXT, identifier=True)
    imdb = spec.add_source("imdb")
    tmdb = spec.add_source("tmdb")
    spec.bind(imdb, movie, identifier=movie["canonical_id"], accuracy=0.85)
    spec.bind(tmdb, movie, identifier=movie["canonical_id"], accuracy=0.7)
    seeds = emit_trust_seed(spec)
    rows = sorted([(p[0], p[1], p[2]) for _, p in seeds])
    assert rows == [("imdb", "Movie", 0.85), ("tmdb", "Movie", 0.7)]


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
