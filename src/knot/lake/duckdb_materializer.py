"""DuckDB-backed lake-side Materializer.

Takes a SQL SELECT, writes the result as Parquet under lake_dir.
Implements the Materializer seam from design/staging/query-executor.md
(commitment 12 in core-design.md).

Atomic writes: COPY TO writes to a .tmp sibling first, then os.replace
renames it into place so a failed query never leaves a partial file.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, ClassVar

import duckdb
from pydantic import BaseModel, ConfigDict, field_validator

from knot.protocols import MaterializeResult, ProtocolKind


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class DuckDBMaterializerConfig(BaseModel):
    """Runtime-editable config for DuckDBMaterializer."""

    model_config = ConfigDict(extra="forbid")

    lake_dir: Path
    parquet_compression: str = "snappy"

    @field_validator("parquet_compression")
    @classmethod
    def _valid_compression(cls, v: str) -> str:
        allowed = {"snappy", "zstd", "gzip", "lz4", "uncompressed"}
        if v.lower() not in allowed:
            raise ValueError(f"parquet_compression must be one of {allowed}, got {v!r}")
        return v.lower()


# ---------------------------------------------------------------------------
# Materializer
# ---------------------------------------------------------------------------

class DuckDBMaterializer:
    """Lake-side Materializer: executes a SQL SELECT and writes Parquet.

    target_path is resolved relative to lake_dir.  Parent directories are
    created automatically.  Writes are atomic: COPY TO writes to a .tmp
    sibling and os.replace() commits it so partial files are never visible.
    """

    Config: ClassVar[type] = DuckDBMaterializerConfig
    disagreement_stance: ClassVar[ProtocolKind] = ProtocolKind.RESOLVED

    def __init__(self, config: DuckDBMaterializerConfig) -> None:
        self._config = config
        self._conn = duckdb.connect(":memory:")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def materialize(
        self,
        ctx: Any,
        query_sql: str,
        target_path: Path,
    ) -> MaterializeResult:
        """Execute query_sql and write the result as Parquet at target_path.

        target_path is joined under lake_dir.  Parent dirs are created as
        needed.  The write is atomic: a .tmp sibling is written first and
        renamed into place on success.

        Returns MaterializeResult with target=absolute path string,
        status="succeeded", watermark=None.

        Raises on query failure or I/O error (never swallows).
        """
        absolute = (self._config.lake_dir / target_path).resolve()
        absolute.parent.mkdir(parents=True, exist_ok=True)

        tmp = absolute.with_suffix(absolute.suffix + ".tmp")
        try:
            self._conn.execute(
                f"COPY ({query_sql}) TO '{tmp}' "
                f"(FORMAT PARQUET, COMPRESSION {self._config.parquet_compression.upper()})"
            )
            os.replace(tmp, absolute)
        except Exception:
            if tmp.exists():
                tmp.unlink(missing_ok=True)
            raise

        return MaterializeResult(
            target=str(absolute),
            status="succeeded",
            watermark=None,
        )

    def register_csv_view(self, view_name: str, csv_path: Path) -> None:
        """Register a CSV file as a named DuckDB view for use in queries."""
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_csv_auto('{csv_path}')"
        )

    def register_parquet_view(self, view_name: str, parquet_path: Path) -> None:
        """Register a Parquet file or glob as a named DuckDB view for use in queries."""
        self._conn.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT * FROM read_parquet('{parquet_path}')"
        )

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._conn.close()
