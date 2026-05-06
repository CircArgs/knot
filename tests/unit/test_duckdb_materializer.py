"""Unit tests for DuckDBMaterializer.

These tests are pure in-process: no docker, no postgres, no external services.
They are placed under tests/unit/ so they run with the standard unit suite.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from knot.lake.duckdb_materializer import DuckDBMaterializer, DuckDBMaterializerConfig
from knot.protocols import MaterializeResult


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def lake_dir(tmp_path: Path) -> Path:
    d = tmp_path / "lake"
    d.mkdir()
    return d


@pytest.fixture()
def materializer(lake_dir: Path) -> DuckDBMaterializer:
    cfg = DuckDBMaterializerConfig(lake_dir=lake_dir)
    return DuckDBMaterializer(config=cfg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_materialize_simple_select_to_parquet(materializer: DuckDBMaterializer, lake_dir: Path) -> None:
    """SELECT 1 AS x writes a parquet file readable as Arrow with x=[1]."""
    out = materializer.materialize(ctx=None, query_sql="SELECT 1 AS x", target_path=Path("out.parquet"))

    target = Path(out.target)
    assert target.exists(), "parquet file must be created"

    table = pq.read_table(target)
    assert table.schema.get_field_index("x") >= 0
    assert table.column("x")[0].as_py() == 1


def test_materialize_b2_movies_to_parquet(materializer: DuckDBMaterializer, tmp_path: Path, lake_dir: Path) -> None:
    """Register imdb_movies view from CSV, run filtered SELECT, verify rows."""
    csv_content = textwrap.dedent("""\
        title,year
        Inception,2010
        Oldboy,2003
        Metropolis,1927
        The Matrix,1999
    """)
    csv_file = tmp_path / "imdb_movies.csv"
    csv_file.write_text(csv_content)

    materializer.register_csv_view("imdb_movies", csv_file)

    materializer.materialize(
        ctx=None,
        query_sql="SELECT title, year FROM imdb_movies WHERE year > 1990",
        target_path=Path("movies_post_1990.parquet"),
    )

    target = lake_dir / "movies_post_1990.parquet"
    table = pq.read_table(target)

    titles = {row.as_py() for row in table.column("title")}
    assert "Inception" in titles
    assert "The Matrix" in titles
    assert "Metropolis" not in titles
    assert len(table) == 3


def test_materialize_idempotent(materializer: DuckDBMaterializer, lake_dir: Path) -> None:
    """Writing the same query to the same path twice overwrites cleanly."""
    path = Path("idempotent.parquet")

    materializer.materialize(ctx=None, query_sql="SELECT 42 AS n", target_path=path)
    materializer.materialize(ctx=None, query_sql="SELECT 99 AS n", target_path=path)

    target = lake_dir / path
    table = pq.read_table(target)
    assert table.column("n")[0].as_py() == 99


def test_materialize_creates_parent_dirs(materializer: DuckDBMaterializer, lake_dir: Path) -> None:
    """target_path with nested dirs auto-created."""
    nested = Path("a/b/c/deep.parquet")
    materializer.materialize(ctx=None, query_sql="SELECT 7 AS v", target_path=nested)

    target = lake_dir / nested
    assert target.exists()
    table = pq.read_table(target)
    assert table.column("v")[0].as_py() == 7


def test_materialize_returns_materialize_result(materializer: DuckDBMaterializer, lake_dir: Path) -> None:
    """Return value is MaterializeResult with status='succeeded' and target set."""
    result = materializer.materialize(
        ctx=None,
        query_sql="SELECT 'hello' AS msg",
        target_path=Path("result_check.parquet"),
    )

    assert isinstance(result, MaterializeResult)
    assert result.status == "succeeded"
    assert result.target == str((lake_dir / "result_check.parquet").resolve())
    assert result.watermark is None


def test_materialize_no_partial_file_on_failure(materializer: DuckDBMaterializer, lake_dir: Path) -> None:
    """A query failure leaves no .tmp or target file behind."""
    path = Path("should_not_exist.parquet")
    absolute = lake_dir / path

    with pytest.raises(Exception):
        materializer.materialize(
            ctx=None,
            query_sql="SELECT * FROM nonexistent_table_xyz",
            target_path=path,
        )

    assert not absolute.exists()
    tmp = absolute.with_suffix(".parquet.tmp")
    assert not tmp.exists()
