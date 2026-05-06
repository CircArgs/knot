"""Unit tests for DuckDBReader — no postgres or Neo4j dependency.

All tests use :memory: DuckDB with registered CSV views from B2 fixtures.
SQL for knot expression trees is always emitted via sql_gen.emit_sql — no
raw SQL construction outside that entry point (except SELECT 1 AS x which
is a literal smoke test, not a knot expression).
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pytest

from knot.lake.duckdb_reader import DuckDBReader, DuckDBReaderConfig

# B2 fixture sources directory
B2_SOURCES = Path(__file__).parent.parent / "fixtures" / "B2" / "sources"


@pytest.fixture
def reader() -> DuckDBReader:
    config = DuckDBReaderConfig(lake_dir=B2_SOURCES)
    return DuckDBReader(config)


@pytest.fixture
def reader_with_b2(reader: DuckDBReader) -> DuckDBReader:
    """Reader with all B2 CSV sources registered as views."""
    reader.register_views_from_dir(B2_SOURCES, extension="csv")
    return reader


# ---------------------------------------------------------------------------
# test_read_simple_query
# ---------------------------------------------------------------------------

def test_read_simple_query(reader: DuckDBReader) -> None:
    """SELECT 1 AS x returns Arrow Table with column x = [1]."""
    result = reader.read(ctx=None, sql="SELECT 1 AS x")
    assert isinstance(result, pa.Table)
    assert result.schema.names == ["x"]
    assert result.num_rows == 1
    assert result.column("x")[0].as_py() == 1


# ---------------------------------------------------------------------------
# test_read_b2_imdb_movies
# ---------------------------------------------------------------------------

def test_read_b2_imdb_movies(reader: DuckDBReader) -> None:
    """Register imdb_movies view; SELECT title, year LIMIT 10 returns rows."""
    reader.register_csv_view("imdb_movies", B2_SOURCES / "imdb_movies.csv")
    result = reader.read(
        ctx=None,
        sql="SELECT title, year FROM imdb_movies LIMIT 10",
    )
    assert isinstance(result, pa.Table)
    assert result.num_rows > 0
    assert "title" in result.schema.names
    assert "year" in result.schema.names
    # Spot-check: all titles are non-null strings
    for val in result.column("title"):
        assert val.as_py() is not None


# ---------------------------------------------------------------------------
# test_read_with_sql_gen
# ---------------------------------------------------------------------------

def test_read_with_sql_gen(reader: DuckDBReader) -> None:
    """Emit SQL via sql_gen for Movie.year > 1990; run against B2 imdb_movies; count > 0."""
    from knot.sql_gen import emit_sql
    from tests.fixtures.B2.spec import Movie, movie_year, credit_work
    from knot.metaschema import Compare, CompareOp, FilteredRelation, Literal_, RelationRef, SlotPath

    # RelationRef requires a slot (the traversal slot); sql_gen uses from_class.name.lower()
    # as the table name regardless of the slot value. credit_work has range=Movie.
    expr = FilteredRelation(
        relation=RelationRef(from_class=Movie, slot=credit_work),
        filter=Compare(
            op=CompareOp.GT,
            left=SlotPath(from_class=Movie, slots=[movie_year]),
            right=Literal_(value=1990),
        ),
    )
    sql = emit_sql(expr, dialect="duckdb")
    # sql_gen emits lowercase class name as table ref: "movie"
    reader.register_csv_view("movie", B2_SOURCES / "imdb_movies.csv")
    result = reader.read(ctx=None, sql=sql)
    assert isinstance(result, pa.Table)
    assert result.num_rows > 0


# ---------------------------------------------------------------------------
# test_read_with_join
# ---------------------------------------------------------------------------

def test_read_with_join(reader: DuckDBReader) -> None:
    """Multi-table query joining imdb_movies + imdb_credits returns Arrow result."""
    reader.register_csv_view("imdb_movies", B2_SOURCES / "imdb_movies.csv")
    reader.register_csv_view("imdb_credits", B2_SOURCES / "imdb_credits.csv")
    sql = (
        "SELECT m.title, c.role "
        "FROM imdb_movies AS m "
        "JOIN imdb_credits AS c ON m.imdb_id = c.movie_imdb_id "
        "LIMIT 20"
    )
    result = reader.read(ctx=None, sql=sql)
    assert isinstance(result, pa.Table)
    assert result.num_rows > 0
    assert "title" in result.schema.names
    assert "role" in result.schema.names


# ---------------------------------------------------------------------------
# test_read_with_predicate_pushdown
# ---------------------------------------------------------------------------

def test_read_with_predicate_pushdown(reader: DuckDBReader) -> None:
    """DuckDB applies WHERE predicate: filtered count < total count."""
    reader.register_csv_view("imdb_movies", B2_SOURCES / "imdb_movies.csv")

    total_result = reader.read(
        ctx=None,
        sql="SELECT COUNT(*) AS n FROM imdb_movies",
    )
    total = total_result.column("n")[0].as_py()

    filtered_result = reader.read(
        ctx=None,
        sql="SELECT COUNT(*) AS n FROM imdb_movies WHERE year > 1990",
    )
    filtered = filtered_result.column("n")[0].as_py()

    # There must be at least one row with year <= 1990 in B2 fixtures
    assert filtered < total
    assert filtered >= 0


# ---------------------------------------------------------------------------
# test_register_views_from_dir
# ---------------------------------------------------------------------------

def test_register_views_from_dir(reader: DuckDBReader) -> None:
    """Bulk registration mounts all CSVs; all view names returned."""
    registered = reader.register_views_from_dir(B2_SOURCES, extension="csv")
    assert len(registered) > 0
    views = reader.list_views()
    for name in registered:
        assert name in views


# ---------------------------------------------------------------------------
# test_reader_disagreement_stance
# ---------------------------------------------------------------------------

def test_reader_disagreement_stance() -> None:
    """DuckDBReader carries RESOLVED disagreement_stance (QueryReader contract)."""
    from knot.protocols import ProtocolKind
    assert DuckDBReader.disagreement_stance == ProtocolKind.RESOLVED


# ---------------------------------------------------------------------------
# test_reader_config_defaults
# ---------------------------------------------------------------------------

def test_reader_config_defaults() -> None:
    """DuckDBReaderConfig defaults extra_extensions to empty list."""
    config = DuckDBReaderConfig(lake_dir=Path("/tmp"))
    assert config.extra_extensions == []
    assert config.lake_dir == Path("/tmp")
