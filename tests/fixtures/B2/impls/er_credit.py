"""B2 Credit ER impl — structural scaffolding.

knot.protocols does not exist yet. This file establishes the correct shape
so it compiles once the core is built.

Covers: cat 4.1 (credit references existing Movie+Person), 4.2 (reference
through merged canonical_id), 4.3 (orphan reference), 4.6 (cross-class pinning
composes with ER post-merge state).

Credit ER is pinned downstream of Movie and Person: Credit.work references
Movie canonical_ids and Credit.person references Person canonical_ids. After
Movie and Person ER resolve, Credit rows are re-pinned to the merged
canonical_ids (cat 4.2, 4.6).
"""

from __future__ import annotations

from typing import ClassVar

from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap  # noqa: F401
from tests.fixtures.B2.spec import (
    Credit, Movie, Person,
    imdb_credits, tmdb_credits, wikidata_credits,
)


class ERCreditConfig:
    """Config for the Credit ER impl.

    cross_references: list — classes Credit's ER reads for FK resolution.
    trust_imdb: float — trust for imdb_credits contributions.
    trust_tmdb: float — trust for tmdb_credits contributions.
    trust_wikidata: float — trust for wikidata_credits contributions.
    """

    # Explicit cross-class dependency declarations (per spec-model.md §
    # "Polymorphic references and consumer-declared dependencies").
    cross_references: list = [Movie, Person]
    trust_imdb: float = 0.90
    trust_tmdb: float = 0.60
    trust_wikidata: float = 0.40


class ERCredit(ERProtocol):
    """ER impl for the Credit class.

    Credit deduplication is FK-driven: two Credit rows from different sources
    that reference the same (person_canonical_id, work_canonical_id, role) tuple
    are merged. ER runs after Movie and Person ER so canonical_id lineage is
    available for the FK lookup (cat 4.2, 4.6).

    Designed-in cases:
    - cat 4.1: credits correctly reference resolved Movie + Person canonical_ids
    - cat 4.2: credits referencing pre-merge source_id are re-routed through lineage
    - cat 4.3: wikidata_credits rows referencing Movie/Person that don't exist (orphan)
    - cat 4.6: cross-class pinning — Credit stage pins to Movie+Person run hashes
    - cat 1.5: mixed-case role values (DIRECTOR, Director, dIRECTOR) in tmdb_credits
    """

    Config: ClassVar[type] = ERCreditConfig

    imdb: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(imdb_credits).is_not_null(),
        # Resolves FK to Movie and Person post-merge canonical_ids:
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )
    tmdb: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(tmdb_credits).is_not_null(),
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )
    wikidata: DataContext = DataContext(
        primary=Credit,
        where=Credit.credit_id.from_source(wikidata_credits).is_not_null(),
        project=[Credit.credit_id, Credit.person, Credit.work, Credit.role],
    )

    def score(self, ctx: ERCreditConfig, imdb, tmdb, wikidata) -> ERResult:
        """Score Credit candidate pairs.

        Two credits merge when they share the same (person_canonical_id,
        work_canonical_id, role) tuple. role comparison is case-insensitive
        to handle mixed-case variants (cat 1.5).
        """
        import itertools

        import pyarrow as pa
        import pyarrow.parquet as pq

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
        for tbl, src in ((imdb, "imdb"), (tmdb, "tmdb"), (wikidata, "wikidata")):
            if tbl is not None and tbl.num_rows > 0:
                all_rows.extend(_rows(tbl, src))

        a_ids, b_ids, scores = [], [], []
        for r_a, r_b in itertools.combinations(all_rows, 2):
            if r_a["_source"] == r_b["_source"]:
                continue
            person_a = str(r_a.get("person") or "")
            person_b = str(r_b.get("person") or "")
            work_a = str(r_a.get("work") or "")
            work_b = str(r_b.get("work") or "")
            role_a = str(r_a.get("role") or "").lower().strip()
            role_b = str(r_b.get("role") or "").lower().strip()
            if person_a == person_b and work_a == work_b and role_a == role_b:
                a_ids.append(r_a["canonical_id"])
                b_ids.append(r_b["canonical_id"])
                scores.append(1.0)

        out_table = pa.table({
            "a_canonical_id": pa.array(a_ids, type=pa.string()),
            "b_canonical_id": pa.array(b_ids, type=pa.string()),
            "score": pa.array(scores, type=pa.float64()),
        })

        target_rel = "er_outputs/credit_scores.parquet"
        materializer = getattr(ctx, "materializer", None)
        if materializer is not None:
            materializer._conn.register("_er_credit_scores_tmp", out_table)
            mat_result = materializer.materialize(
                ctx=ctx,
                query_sql="SELECT * FROM _er_credit_scores_tmp",
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
