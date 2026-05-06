"""B2 Person ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 2.x (ER merge), 1.6 (Unicode diacritics), 1.7 (non-Latin scripts),
1.8 (date-format drift), 4.2 (cross-source merged identifiers).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from tests.fixtures.B2.spec import Person, imdb_persons, tmdb_persons


class ERPersonConfig:
    """Config for the Person ER impl.

    name_similarity_threshold: float — minimum fuzzy-name similarity to block.
    use_wikidata_id: bool — if True, wikidata_id exact-match is a forced merge signal.
    trust_imdb: float — trust score for imdb_persons contributions.
    trust_tmdb: float — trust score for tmdb_persons contributions.
    """

    name_similarity_threshold: float = 0.85
    use_wikidata_id: bool = True
    trust_imdb: float = 0.88
    trust_tmdb: float = 0.65


class ERPerson(ERProtocol):
    """ER impl for the Person class.

    Reads per-source Person views (DISAGREEMENT_AWARE) and produces canonical_id
    assignments. wikidata_id exact-match is the primary deduplication signal when
    available; name+birthdate similarity is the fallback.

    Designed-in cases:
    - Unicode name matching: Søren Kierkegaard, Alejandro González Iñárritu (cat 1.6)
    - Non-Latin names: Kurosawa Akira (CJK), Andrei Tarkovsky (Cyrillic) (cat 1.7)
    - Birthdate format drift: IMDB='1943-07-14', TMDB='Jul 14 1943' (cat 1.8)
    - ~20 known dupes across IMDB+TMDB (cat 2.1, 2.3)
    - ~5 near-misses (same name, different entity) (cat 2.2)
    - Cross-source merged identifiers via wikidata_id (cat 4.2)
    """

    Config: ClassVar[type] = ERPersonConfig

    imdb: DataContext = DataContext(
        primary=Person,
        where=Person.imdb_id.from_source(imdb_persons).is_not_null(),
    )
    tmdb: DataContext = DataContext(
        primary=Person,
        where=Person.imdb_id.from_source(tmdb_persons).is_not_null(),
    )

    def score(self, ctx: ERPersonConfig, imdb, tmdb) -> ERResult:
        """Score Person candidate pairs.

        Primary signal: wikidata_id exact match (forced merge when
        Config.use_wikidata_id=True). Fallback: fuzzy name similarity
        above Config.name_similarity_threshold + birthdate within 1 year.
        """
        import itertools

        import pyarrow as pa
        import pyarrow.parquet as pq
        from rapidfuzz import fuzz

        cfg = ctx if isinstance(ctx, ERPersonConfig) else ERPersonConfig()

        def _rows(table: pa.Table, source: str) -> list[dict]:
            out = []
            cols = table.schema.names
            for i in range(table.num_rows):
                row = {c: table.column(c)[i].as_py() for c in cols}
                row["_source"] = source
                if "canonical_id" not in row:
                    id_col = cols[0]
                    row["canonical_id"] = f"{source}:{row[id_col]}"
                out.append(row)
            return out

        all_rows: list[dict] = []
        for tbl, src in ((imdb, "imdb"), (tmdb, "tmdb")):
            if tbl is not None and tbl.num_rows > 0:
                all_rows.extend(_rows(tbl, src))

        a_ids, b_ids, scores = [], [], []
        for r_a, r_b in itertools.combinations(all_rows, 2):
            if r_a["_source"] == r_b["_source"]:
                continue

            # Primary: wikidata_id exact match forces score=1.0.
            wid_a = r_a.get("wikidata_id")
            wid_b = r_b.get("wikidata_id")
            if cfg.use_wikidata_id and wid_a and wid_b and wid_a == wid_b:
                a_ids.append(r_a["canonical_id"])
                b_ids.append(r_b["canonical_id"])
                scores.append(1.0)
                continue

            name_a = str(r_a.get("name") or "")
            name_b = str(r_b.get("name") or "")
            # Cheap prefix blocker: first 3 chars must match.
            if name_a[:3].lower() != name_b[:3].lower():
                continue

            name_score = fuzz.token_sort_ratio(name_a, name_b) / 100.0
            if name_score < cfg.name_similarity_threshold:
                continue

            # Birthdate year window ±1.
            bd_a = r_a.get("birthdate")
            bd_b = r_b.get("birthdate")
            if bd_a is not None and bd_b is not None:
                try:
                    yr_a = int(str(bd_a)[:4])
                    yr_b = int(str(bd_b)[:4])
                    if abs(yr_a - yr_b) > 1:
                        continue
                except (ValueError, TypeError):
                    pass

            a_ids.append(r_a["canonical_id"])
            b_ids.append(r_b["canonical_id"])
            scores.append(float(name_score))

        out_table = pa.table({
            "a_canonical_id": pa.array(a_ids, type=pa.string()),
            "b_canonical_id": pa.array(b_ids, type=pa.string()),
            "score": pa.array(scores, type=pa.float64()),
        })

        target_rel = "er_outputs/person_scores.parquet"
        materializer = getattr(ctx, "materializer", None)
        if materializer is not None:
            materializer._conn.register("_er_person_scores_tmp", out_table)
            mat_result = materializer.materialize(
                ctx=ctx,
                query_sql="SELECT * FROM _er_person_scores_tmp",
                target_path=__import__("pathlib").Path(target_rel),
            )
            output_uri = mat_result.target
        else:
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
