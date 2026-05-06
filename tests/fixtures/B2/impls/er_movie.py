"""B2 Movie ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 2.x (ER merge/non-merge), 3.x (trust resolution), 4.2
(cross-source merged identifiers), 4.6 (cross-class pinning).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from knot.metaschema import ResolutionPolicy  # noqa: F401
from tests.fixtures.B2.spec import Movie, imdb_movies, tmdb_movies, wikidata_movies


class ERMovieConfig:
    """Config for the Movie ER impl.

    blocking_threshold: float — Jaccard similarity floor for blocking candidate pairs.
    title_weight: float — weight applied to title similarity in the scoring function.
    year_tolerance: int — max year difference to still score as a candidate pair.
    trust_imdb: float — trust score for imdb_movies contributions.
    trust_tmdb: float — trust score for tmdb_movies contributions.
    trust_wikidata: float — trust score for wikidata_movies contributions.
    """

    blocking_threshold: float = 0.6
    title_weight: float = 0.7
    year_tolerance: int = 1
    trust_imdb: float = 0.91
    trust_tmdb: float = 0.62
    trust_wikidata: float = 0.48


class ERMovie(ERProtocol):
    """ER impl for the Movie class.

    Reads per-source Movie views (DISAGREEMENT_AWARE stance) and produces
    canonical_id assignments. Trust scores drive trust-resolution CTEs
    (ARGMAX_TRUST on title/year; MEDIAN_NUMERIC on runtime_minutes/rating).

    Designed-in cases:
    - ~20 known dupes across IMDB+TMDB+Wikidata (cat 2.1, 2.3)
    - ~10 near-misses that must NOT merge (cat 2.2)
    - Asymmetric coverage: some movies in IMDB+TMDB but absent from Wikidata (cat 2.4)
    - Surfaced conflicts on year/title disagreement (cat 2.5)
    - _user_er_decisions forced merges (cat 2.6) and forced non-merges (cat 2.7)
    - UNIQUE_OR_FAIL violation on imdb_id disagreement (cat 3.6)
    - Cross-source merged identifier lineage (cat 4.2)
    """

    Config: ClassVar[type] = ERMovieConfig

    # DISAGREEMENT_AWARE: source bag is the input — bare Movie.title is MultiValued[str]
    imdb: DataContext = DataContext(
        primary=Movie,
        where=Movie.imdb_id.from_source(imdb_movies).is_not_null(),
    )
    tmdb: DataContext = DataContext(
        primary=Movie,
        where=Movie.imdb_id.from_source(tmdb_movies).is_not_null(),
    )
    wikidata: DataContext = DataContext(
        primary=Movie,
        where=Movie.imdb_id.from_source(wikidata_movies).is_not_null(),
    )

    def score(self, ctx: ERMovieConfig, imdb, tmdb, wikidata) -> ERResult:
        """Score candidate pairs for ER merging.

        Uses title similarity (weighted by Config.title_weight) and year
        proximity (within Config.year_tolerance) for blocking + scoring.
        Trust weights: imdb=0.91, tmdb=0.62, wikidata=0.48.
        """
        import itertools

        import pyarrow as pa
        import pyarrow.parquet as pq
        from rapidfuzz import fuzz

        cfg = ctx if isinstance(ctx, ERMovieConfig) else ERMovieConfig()

        # Collect rows from all source tables into a unified list.
        def _rows(table: pa.Table, source: str) -> list[dict]:
            out = []
            cols = table.schema.names
            for i in range(table.num_rows):
                row = {c: table.column(c)[i].as_py() for c in cols}
                row["_source"] = source
                # canonical_id is the row's own identifier (first column treated as id)
                if "canonical_id" not in row:
                    id_col = cols[0]
                    row["canonical_id"] = f"{source}:{row[id_col]}"
                out.append(row)
            return out

        all_rows: list[dict] = []
        for tbl, src in ((imdb, "imdb"), (tmdb, "tmdb"), (wikidata, "wikidata")):
            if tbl is not None and tbl.num_rows > 0:
                all_rows.extend(_rows(tbl, src))

        # BLOCK + SCORE: compare rows from different sources only.
        a_ids, b_ids, scores = [], [], []
        for r_a, r_b in itertools.combinations(all_rows, 2):
            if r_a["_source"] == r_b["_source"]:
                continue
            title_a = str(r_a.get("title") or "")
            title_b = str(r_b.get("title") or "")
            year_a = r_a.get("year")
            year_b = r_b.get("year")
            # Cheap prefix blocker: first 5 chars must match.
            if title_a[:5].lower() != title_b[:5].lower():
                continue
            # Year window blocker.
            if year_a is not None and year_b is not None:
                if abs(int(year_a) - int(year_b)) > cfg.year_tolerance:
                    continue
            # Score: title similarity + year proximity bonus.
            title_score = fuzz.token_sort_ratio(title_a, title_b) / 100.0
            year_bonus = 0.0
            if year_a is not None and year_b is not None:
                diff = abs(int(year_a) - int(year_b))
                year_bonus = (1.0 - diff / max(cfg.year_tolerance, 1)) * (1.0 - cfg.title_weight)
            score = title_score * cfg.title_weight + year_bonus
            a_ids.append(r_a["canonical_id"])
            b_ids.append(r_b["canonical_id"])
            scores.append(float(score))

        out_table = pa.table({
            "a_canonical_id": pa.array(a_ids, type=pa.string()),
            "b_canonical_id": pa.array(b_ids, type=pa.string()),
            "score": pa.array(scores, type=pa.float64()),
        })

        target_rel = "er_outputs/movie_scores.parquet"
        materializer = getattr(ctx, "materializer", None)
        if materializer is not None:
            # Production path: write through the bound Materializer.
            # Register the in-memory table as a view, then materialize via SQL.
            materializer._conn.register("_er_movie_scores_tmp", out_table)
            mat_result = materializer.materialize(
                ctx=ctx,
                query_sql="SELECT * FROM _er_movie_scores_tmp",
                target_path=__import__("pathlib").Path(target_rel),
            )
            output_uri = mat_result.target
        else:
            # Fallback for tests that pass a plain ctx without materializer.
            from pathlib import Path as _Path
            lake_dir = getattr(ctx, "lake_dir", _Path("/tmp"))
            out_path = _Path(lake_dir) / target_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(out_table, out_path)
            output_uri = str(out_path)

        return ERResult(
            output_uri=output_uri,
            column_map=ScoreColumnMap(
                a_canonical="a_canonical_id",
                b_canonical="b_canonical_id",
                score="score",
            ),
        )
