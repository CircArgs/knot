"""Unit tests for B2 CrossSourceAgreementCheck (DqMergeRunner)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pyarrow as pa
import pytest

from knot.metaschema import OntologyClass, ResolutionPolicy, Slot, TypeDefinition
from knot.protocols import DqColumnMap, DqMergeRunner, DqResult
from tests.fixtures.B2.impls.dq_merge import CrossSourceAgreementCheck
from tests.fixtures.B2.spec import (
    Movie,
    Person,
    movie_imdb_id,
    movie_title,
    movie_runtime_minutes,
    person_imdb_id,
    person_wikidata_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ctx(tmp_path: Path, classes: list[OntologyClass]) -> SimpleNamespace:
    return SimpleNamespace(
        lake_dir=tmp_path,
        run_id="testrun01",
        config=SimpleNamespace(severity="error", classes=classes),
    )


def _movie_table(rows: list[dict]) -> pa.Table:
    """Build a minimal per_source_facts table for Movie with imdb_id + title + runtime_minutes."""
    return pa.table({
        "canonical_id": pa.array([r["canonical_id"] for r in rows], type=pa.string()),
        "source":        pa.array([r.get("source", "s1") for r in rows], type=pa.string()),
        "imdb_id":       pa.array([r.get("imdb_id") for r in rows], type=pa.string()),
        "title":         pa.array([r.get("title") for r in rows], type=pa.string()),
        "runtime_minutes": pa.array([r.get("runtime_minutes") for r in rows], type=pa.int64()),
    })


def _person_table(rows: list[dict]) -> pa.Table:
    return pa.table({
        "canonical_id": pa.array([r["canonical_id"] for r in rows], type=pa.string()),
        "source":       pa.array([r.get("source", "s1") for r in rows], type=pa.string()),
        "imdb_id":      pa.array([r.get("imdb_id") for r in rows], type=pa.string()),
        "wikidata_id":  pa.array([r.get("wikidata_id") for r in rows], type=pa.string()),
    })


# ---------------------------------------------------------------------------
# test_check_passes_when_sources_agree
# ---------------------------------------------------------------------------

def test_check_passes_when_sources_agree(tmp_path: Path) -> None:
    """All UNIQUE_OR_FAIL slots agree across sources → passed=True, no offenders."""
    rows = [
        {"canonical_id": "mov_1", "source": "imdb", "imdb_id": "tt0111161", "title": "The Shawshank Redemption", "runtime_minutes": 142},
        {"canonical_id": "mov_1", "source": "tmdb", "imdb_id": "tt0111161", "title": "The Shawshank Redemption", "runtime_minutes": 144},
    ]
    tbl = _movie_table(rows)
    ctx = _make_ctx(tmp_path, [Movie])

    check = CrossSourceAgreementCheck()
    result = check.check(ctx, Movie=tbl)

    assert result.passed is True
    assert result.summary["violations"] == 0
    assert result.offenders_uri is None


# ---------------------------------------------------------------------------
# test_check_fails_on_disagreement
# ---------------------------------------------------------------------------

def test_check_fails_on_disagreement(tmp_path: Path) -> None:
    """Two sources give different imdb_id for the same canonical_id → offender row."""
    rows = [
        {"canonical_id": "mov_1", "source": "imdb", "imdb_id": "tt0111161", "title": "Shawshank", "runtime_minutes": 142},
        {"canonical_id": "mov_1", "source": "tmdb", "imdb_id": "tt9999999", "title": "Shawshank", "runtime_minutes": 144},
    ]
    tbl = _movie_table(rows)
    ctx = _make_ctx(tmp_path, [Movie])

    check = CrossSourceAgreementCheck()
    result = check.check(ctx, Movie=tbl)

    assert result.passed is False
    assert result.summary["violations"] == 1
    assert result.offenders_uri is not None

    import pyarrow.parquet as pq
    offenders = pq.read_table(result.offenders_uri)
    assert offenders.num_rows == 1
    row = {c: offenders.column(c)[0].as_py() for c in offenders.schema.names}
    assert row["rule_id"] == "cross_source_agreement"
    assert row["class_name"] == "Movie"
    assert row["slot_name"] == "imdb_id"
    assert row["offending_pk"] == "mov_1"
    assert "tt0111161" in row["detail"]
    assert "tt9999999" in row["detail"]


# ---------------------------------------------------------------------------
# test_check_returns_typed_dq_result
# ---------------------------------------------------------------------------

def test_check_returns_typed_dq_result(tmp_path: Path) -> None:
    """Return value is a DqResult instance, not a plain dict."""
    tbl = _movie_table([
        {"canonical_id": "mov_1", "source": "imdb", "imdb_id": "tt1", "title": "X", "runtime_minutes": 90},
    ])
    ctx = _make_ctx(tmp_path, [Movie])

    check = CrossSourceAgreementCheck()
    result = check.check(ctx, Movie=tbl)

    assert isinstance(result, DqResult)


# ---------------------------------------------------------------------------
# test_check_column_map_shape
# ---------------------------------------------------------------------------

def test_check_column_map_shape(tmp_path: Path) -> None:
    """DqColumnMap field names match the actual offender table columns."""
    rows = [
        {"canonical_id": "mov_1", "source": "imdb", "imdb_id": "tt0000001", "title": "A", "runtime_minutes": 90},
        {"canonical_id": "mov_1", "source": "tmdb", "imdb_id": "tt0000002", "title": "A", "runtime_minutes": 95},
    ]
    tbl = _movie_table(rows)
    ctx = _make_ctx(tmp_path, [Movie])

    check = CrossSourceAgreementCheck()
    result = check.check(ctx, Movie=tbl)

    assert isinstance(result.column_map, DqColumnMap)
    cm = result.column_map

    # Every non-None field in the column_map must name a column in the offender table.
    import pyarrow.parquet as pq
    offenders = pq.read_table(result.offenders_uri)
    col_names = set(offenders.schema.names)

    for field_value in [cm.rule_id, cm.class_name, cm.offending_pk, cm.severity]:
        assert field_value in col_names, f"{field_value!r} not in offender columns {col_names}"
    # slot_name and detail are nullable in DqColumnMap; verify they map if set
    if cm.slot_name is not None:
        assert cm.slot_name in col_names
    if cm.detail is not None:
        assert cm.detail in col_names


# ---------------------------------------------------------------------------
# test_check_only_flags_unique_or_fail_slots
# ---------------------------------------------------------------------------

def test_check_only_flags_unique_or_fail_slots(tmp_path: Path) -> None:
    """Slots with ARGMAX_TRUST / MEDIAN_NUMERIC must not produce offenders even on disagreement."""
    rows = [
        # imdb_id agrees (UNIQUE_OR_FAIL) — should be clean
        # title disagrees (ARGMAX_TRUST) — must be ignored
        # runtime_minutes disagrees (MEDIAN_NUMERIC) — must be ignored
        {"canonical_id": "mov_1", "source": "imdb", "imdb_id": "tt0111161", "title": "The Shawshank Redemption", "runtime_minutes": 142},
        {"canonical_id": "mov_1", "source": "tmdb", "imdb_id": "tt0111161", "title": "Shawshank Redemption",     "runtime_minutes": 144},
        {"canonical_id": "mov_1", "source": "wiki", "imdb_id": "tt0111161", "title": "Shawshank",                "runtime_minutes": 140},
    ]
    tbl = _movie_table(rows)
    ctx = _make_ctx(tmp_path, [Movie])

    check = CrossSourceAgreementCheck()
    result = check.check(ctx, Movie=tbl)

    # imdb_id agrees across all three → no violations
    assert result.passed is True
    assert result.summary["violations"] == 0


def test_check_person_wikidata_id_disagreement(tmp_path: Path) -> None:
    """Person.wikidata_id is UNIQUE_OR_FAIL — disagreement should surface."""
    rows = [
        {"canonical_id": "per_1", "source": "imdb",     "imdb_id": "nm0001", "wikidata_id": "Q123"},
        {"canonical_id": "per_1", "source": "wikidata",  "imdb_id": "nm0001", "wikidata_id": "Q456"},
    ]
    tbl = _person_table(rows)
    ctx = _make_ctx(tmp_path, [Person])

    check = CrossSourceAgreementCheck()
    result = check.check(ctx, Person=tbl)

    assert result.passed is False
    assert result.summary["violations"] == 1
