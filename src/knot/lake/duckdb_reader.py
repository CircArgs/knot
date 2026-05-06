"""DuckDB-backed QueryReader implementation.

Commitment 12: lake-side execution seam — QueryReader (sync SELECT → Arrow).
SQL is always knot-generated (sql_gen.emit_sql); this module only executes it.

For fixture use: point lake_dir at tests/fixtures/B2/sources/ and register
CSV views before running queries.  DuckDB reads CSV via read_csv_auto and
parquet via read_parquet natively — no schema declaration needed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

import duckdb
import pyarrow as pa

from knot.protocols import ProtocolKind


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class DuckDBReaderConfig:
    """Config for the local DuckDB QueryReader.

    Attributes:
        lake_dir: Root directory under which source parquet/CSV files live.
        extra_extensions: DuckDB extensions to install + load on connection
            startup (e.g. ["iceberg"] when Iceberg support is needed).
    """

    def __init__(
        self,
        lake_dir: Path,
        extra_extensions: list[str] | None = None,
    ) -> None:
        self.lake_dir: Path = Path(lake_dir)
        self.extra_extensions: list[str] = extra_extensions or []


# ---------------------------------------------------------------------------
# DuckDBReader
# ---------------------------------------------------------------------------

class DuckDBReader:
    """Implements QueryReader against a local in-process DuckDB instance.

    One connection per reader instance; connection is :memory: (no on-disk
    state).  Views registered via register_csv_view / register_parquet_view
    persist for the lifetime of the connection.

    Usage in tests:
        config = DuckDBReaderConfig(lake_dir=Path("tests/fixtures/B2/sources"))
        reader = DuckDBReader(config)
        reader.register_csv_view("imdb_movies", config.lake_dir / "imdb_movies.csv")
        table = reader.read(ctx=None, sql="SELECT title, year FROM imdb_movies LIMIT 10")
    """

    Config: ClassVar[type] = DuckDBReaderConfig
    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def __init__(self, config: DuckDBReaderConfig) -> None:
        self._config = config
        self._conn: duckdb.DuckDBPyConnection = duckdb.connect(":memory:")
        for ext in config.extra_extensions:
            self._conn.execute(f"INSTALL {ext}; LOAD {ext};")

    # ------------------------------------------------------------------
    # QueryReader protocol
    # ------------------------------------------------------------------

    def read(self, ctx: Any, sql: str) -> pa.Table:
        """Execute SQL and return result as an Arrow Table.

        Args:
            ctx: Pipeline run context (unused at execution time; passed for
                protocol conformance — knot threads it for logging/tracing
                at the dispatch boundary).
            sql: SQL string emitted by sql_gen.emit_sql().  Never construct
                SQL manually; the rule is sql_gen is the sole SQL source.

        Returns:
            pyarrow.Table with the full result set.

        Raises:
            duckdb.Error: on syntax errors, missing views, or type mismatches.
        """
        return self._conn.execute(sql).to_arrow_table()

    # ------------------------------------------------------------------
    # View registration helpers
    # ------------------------------------------------------------------

    def register_csv_view(self, view_name: str, csv_path: Path) -> None:
        """Register a CSV file as a DuckDB view.

        DuckDB's read_csv_auto infers column names and types from the header
        row and first data rows — no schema declaration needed.

        The view name should match the table name sql_gen emits for the
        corresponding ontology class (lower-cased class name by convention,
        e.g. "imdb_movies" for the imdb_movies source).

        Args:
            view_name: SQL identifier used in queries (e.g. "imdb_movies").
            csv_path: Absolute or relative path to the CSV file.
        """
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_csv_auto('{csv_path}')"
        )

    def register_parquet_view(self, view_name: str, parquet_path: Path) -> None:
        """Register a Parquet file or glob as a DuckDB view.

        Supports both single-file paths and glob patterns understood by
        DuckDB (e.g. ``/lake/movie/**/*.parquet`` for Hive-partitioned data).

        Args:
            view_name: SQL identifier used in queries.
            parquet_path: Path or glob to the parquet file(s).
        """
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_parquet('{parquet_path}')"
        )

    def register_views_from_dir(
        self,
        source_dir: Path,
        extension: str = "csv",
    ) -> list[str]:
        """Bulk-register all files with the given extension in source_dir as views.

        View names are the file stems (e.g. ``imdb_movies.csv`` → view
        ``imdb_movies``).  Useful in tests to mount an entire fixture
        directory in one call.

        Args:
            source_dir: Directory containing source files.
            extension: File extension to match (without leading dot).

        Returns:
            List of view names that were registered.
        """
        registered: list[str] = []
        for path in sorted(source_dir.glob(f"*.{extension}")):
            view_name = path.stem
            if extension == "parquet":
                self.register_parquet_view(view_name, path)
            else:
                self.register_csv_view(view_name, path)
            registered.append(view_name)
        return registered

    # ------------------------------------------------------------------
    # Introspection helpers (for tests / debugging)
    # ------------------------------------------------------------------

    def list_views(self) -> list[str]:
        """Return names of all views currently registered in the connection."""
        result = self._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_type = 'VIEW'"
        ).fetchall()
        return [row[0] for row in result]
