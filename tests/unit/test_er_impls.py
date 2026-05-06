"""Unit tests for B2 ER fixture implementations.

Tests use synthetic pyarrow Tables — no orchestrator, no lake.
ctx is a plain config object with a lake_dir pointing to tmp_path.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from tests.fixtures.B2.impls.er_movie import ERMovie, ERMovieConfig
from tests.fixtures.B2.impls.er_person import ERPerson, ERPersonConfig
from tests.fixtures.B2.impls.er_credit import ERCredit, ERCreditConfig
from knot.protocols import ERResult, ScoreColumnMap


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _movie_table(rows: list[dict]) -> pa.Table:
    """Build a minimal Movie Arrow table from dicts with title/year/canonical_id."""
    return pa.table({
        "canonical_id": pa.array([r["canonical_id"] for r in rows], type=pa.string()),
        "title": pa.array([r.get("title", "") for r in rows], type=pa.string()),
        "year": pa.array([r.get("year") for r in rows], type=pa.int64()),
    })


def _person_table(rows: list[dict]) -> pa.Table:
    return pa.table({
        "canonical_id": pa.array([r["canonical_id"] for r in rows], type=pa.string()),
        "name": pa.array([r.get("name", "") for r in rows], type=pa.string()),
        "birthdate": pa.array([r.get("birthdate") for r in rows], type=pa.string()),
        "wikidata_id": pa.array([r.get("wikidata_id") for r in rows], type=pa.string()),
    })


def _credit_table(rows: list[dict]) -> pa.Table:
    return pa.table({
        "canonical_id": pa.array([r["canonical_id"] for r in rows], type=pa.string()),
        "person": pa.array([r.get("person", "") for r in rows], type=pa.string()),
        "work": pa.array([r.get("work", "") for r in rows], type=pa.string()),
        "role": pa.array([r.get("role", "") for r in rows], type=pa.string()),
    })


class _Ctx:
    """Minimal stand-in for a per-run context object."""
    def __init__(self, tmp_path: Path, config):
        self.lake_dir = tmp_path
        self._config = config

    def __getattr__(self, name):
        return getattr(self._config, name)


# ---------------------------------------------------------------------------
# ERMovie
# ---------------------------------------------------------------------------

class TestERMovie:

    def test_returns_typed_result(self, tmp_path):
        imdb = _movie_table([
            {"canonical_id": "imdb:tt001", "title": "Inception", "year": 2010},
            {"canonical_id": "imdb:tt002", "title": "The Matrix", "year": 1999},
        ])
        tmdb = _movie_table([
            {"canonical_id": "tmdb:tt001", "title": "Inception", "year": 2010},
            {"canonical_id": "tmdb:tt003", "title": "Interstellar", "year": 2014},
        ])
        wikidata = _movie_table([
            {"canonical_id": "wiki:tt001", "title": "Inception", "year": 2010},
        ])
        ctx = _Ctx(tmp_path, ERMovieConfig())
        result = ERMovie().score(ctx, imdb, tmdb, wikidata)

        assert isinstance(result, ERResult)
        assert isinstance(result.column_map, ScoreColumnMap)
        assert result.column_map.a_canonical == "a_canonical_id"
        assert result.column_map.b_canonical == "b_canonical_id"
        assert result.column_map.score == "score"
        assert result.output_uri.endswith("movie_scores.parquet")
        assert Path(result.output_uri).exists()

    def test_parquet_on_disk_has_correct_columns(self, tmp_path):
        import pyarrow.parquet as pq
        imdb = _movie_table([{"canonical_id": "imdb:tt001", "title": "Inception", "year": 2010}])
        tmdb = _movie_table([{"canonical_id": "tmdb:tt001", "title": "Inception", "year": 2010}])
        ctx = _Ctx(tmp_path, ERMovieConfig())
        result = ERMovie().score(ctx, imdb, tmdb, None)
        tbl = pq.read_table(result.output_uri)
        assert "a_canonical_id" in tbl.schema.names
        assert "b_canonical_id" in tbl.schema.names
        assert "score" in tbl.schema.names

    def test_blocks_on_year_window(self, tmp_path):
        """Pairs outside year_tolerance must not appear in output."""
        imdb = _movie_table([{"canonical_id": "imdb:tt001", "title": "Inception", "year": 2010}])
        tmdb = _movie_table([{"canonical_id": "tmdb:tt001", "title": "Inception", "year": 2015}])
        cfg = ERMovieConfig()
        cfg.year_tolerance = 1
        ctx = _Ctx(tmp_path, cfg)
        result = ERMovie().score(ctx, imdb, tmdb, None)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows == 0

    def test_known_dupes_get_high_score(self, tmp_path):
        """Same title + same year from different sources => score >= 0.9."""
        imdb = _movie_table([{"canonical_id": "imdb:tt001", "title": "Inception", "year": 2010}])
        tmdb = _movie_table([{"canonical_id": "tmdb:tt001", "title": "Inception", "year": 2010}])
        ctx = _Ctx(tmp_path, ERMovieConfig())
        result = ERMovie().score(ctx, imdb, tmdb, None)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows >= 1
        scores = tbl.column("score").to_pylist()
        assert max(scores) >= 0.9

    def test_distinct_titles_blocked(self, tmp_path):
        """Titles with no matching 5-char prefix are blocked and never appear in output."""
        imdb = _movie_table([{"canonical_id": "imdb:tt001", "title": "Inception", "year": 2010}])
        tmdb = _movie_table([{"canonical_id": "tmdb:tt001", "title": "Parasite", "year": 2010}])
        ctx = _Ctx(tmp_path, ERMovieConfig())
        result = ERMovie().score(ctx, imdb, tmdb, None)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows == 0


# ---------------------------------------------------------------------------
# ERPerson
# ---------------------------------------------------------------------------

class TestERPerson:

    def test_returns_typed_result(self, tmp_path):
        imdb = _person_table([
            {"canonical_id": "imdb:nm001", "name": "Christopher Nolan", "birthdate": "1970-07-30", "wikidata_id": None},
        ])
        tmdb = _person_table([
            {"canonical_id": "tmdb:nm001", "name": "Christopher Nolan", "birthdate": "1970-07-30", "wikidata_id": None},
        ])
        ctx = _Ctx(tmp_path, ERPersonConfig())
        result = ERPerson().score(ctx, imdb, tmdb)
        assert isinstance(result, ERResult)
        assert isinstance(result.column_map, ScoreColumnMap)
        assert Path(result.output_uri).exists()

    def test_wikidata_id_match_forces_score_one(self, tmp_path):
        """wikidata_id exact match => score == 1.0."""
        imdb = _person_table([
            {"canonical_id": "imdb:nm001", "name": "Akira Kurosawa", "birthdate": "1910-03-23", "wikidata_id": "Q7798"},
        ])
        tmdb = _person_table([
            {"canonical_id": "tmdb:nm001", "name": "Kurosawa Akira", "birthdate": "1910-03-23", "wikidata_id": "Q7798"},
        ])
        ctx = _Ctx(tmp_path, ERPersonConfig())
        result = ERPerson().score(ctx, imdb, tmdb)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows >= 1
        assert 1.0 in tbl.column("score").to_pylist()

    def test_same_name_same_birthyear_matches(self, tmp_path):
        imdb = _person_table([
            {"canonical_id": "imdb:nm002", "name": "Martin Scorsese", "birthdate": "1942-11-17", "wikidata_id": None},
        ])
        tmdb = _person_table([
            {"canonical_id": "tmdb:nm002", "name": "Martin Scorsese", "birthdate": "1942-05-01", "wikidata_id": None},
        ])
        ctx = _Ctx(tmp_path, ERPersonConfig())
        result = ERPerson().score(ctx, imdb, tmdb)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows >= 1

    def test_different_name_no_match(self, tmp_path):
        """Prefix blocker: very different names produce no pairs."""
        imdb = _person_table([
            {"canonical_id": "imdb:nm003", "name": "Andrei Tarkovsky", "birthdate": "1932-04-04", "wikidata_id": None},
        ])
        tmdb = _person_table([
            {"canonical_id": "tmdb:nm004", "name": "Federico Fellini", "birthdate": "1920-01-20", "wikidata_id": None},
        ])
        ctx = _Ctx(tmp_path, ERPersonConfig())
        result = ERPerson().score(ctx, imdb, tmdb)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows == 0


# ---------------------------------------------------------------------------
# ERCredit
# ---------------------------------------------------------------------------

class TestERCredit:

    def test_returns_typed_result(self, tmp_path):
        imdb = _credit_table([
            {"canonical_id": "imdb:cr001", "person": "nm001", "work": "tt001", "role": "director"},
        ])
        tmdb = _credit_table([
            {"canonical_id": "tmdb:cr001", "person": "nm001", "work": "tt001", "role": "director"},
        ])
        ctx = _Ctx(tmp_path, ERCreditConfig())
        result = ERCredit().score(ctx, imdb, tmdb, None)
        assert isinstance(result, ERResult)
        assert isinstance(result.column_map, ScoreColumnMap)
        assert Path(result.output_uri).exists()

    def test_exact_tuple_match_scores_one(self, tmp_path):
        """Same (person, work, role) across sources => score == 1.0."""
        imdb = _credit_table([
            {"canonical_id": "imdb:cr001", "person": "nm001", "work": "tt001", "role": "director"},
        ])
        tmdb = _credit_table([
            {"canonical_id": "tmdb:cr001", "person": "nm001", "work": "tt001", "role": "director"},
        ])
        ctx = _Ctx(tmp_path, ERCreditConfig())
        result = ERCredit().score(ctx, imdb, tmdb, None)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows == 1
        assert tbl.column("score")[0].as_py() == 1.0

    def test_mixed_case_role_matches(self, tmp_path):
        """Role comparison is case-insensitive (cat 1.5)."""
        imdb = _credit_table([
            {"canonical_id": "imdb:cr002", "person": "nm002", "work": "tt002", "role": "DIRECTOR"},
        ])
        tmdb = _credit_table([
            {"canonical_id": "tmdb:cr002", "person": "nm002", "work": "tt002", "role": "director"},
        ])
        ctx = _Ctx(tmp_path, ERCreditConfig())
        result = ERCredit().score(ctx, imdb, tmdb, None)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows == 1

    def test_different_work_no_match(self, tmp_path):
        """Different work => no match."""
        imdb = _credit_table([
            {"canonical_id": "imdb:cr003", "person": "nm001", "work": "tt001", "role": "actor"},
        ])
        tmdb = _credit_table([
            {"canonical_id": "tmdb:cr003", "person": "nm001", "work": "tt999", "role": "actor"},
        ])
        ctx = _Ctx(tmp_path, ERCreditConfig())
        result = ERCredit().score(ctx, imdb, tmdb, None)
        import pyarrow.parquet as pq
        tbl = pq.read_table(result.output_uri)
        assert tbl.num_rows == 0
