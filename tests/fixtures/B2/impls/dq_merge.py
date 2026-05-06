"""B2 DqMergeRunner impl — cross-source agreement on UNIQUE_OR_FAIL slots.

Built-in bundle check at the merge stage (per staging/dq-design.md).

DqMergeRunner stance is RESOLVED, meaning resolved_facts/<Class> tables have
already had the trust-CTE applied — one winner per (canonical_id, slot).  That
collapsed view cannot show disagreement by construction.  The check therefore
reads per_source_facts/<Class> directly (via ctx.lake_dir), which retains the
full source bag, and flags any canonical_id where UNIQUE_OR_FAIL slots carry
more than one distinct non-null value across sources.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, ClassVar

import pyarrow as pa
import pyarrow.parquet as pq

from knot.metaschema import OntologyClass, ResolutionPolicy
from knot.protocols import DataContext, DqColumnMap, DqMergeRunner, DqResult



class CrossSourceAgreementCheckConfig:
    """Config for the cross-source agreement check.

    severity: "error" | "warning" — severity of emitted DQ offenders.
    classes: list of OntologyClass to check; empty list means all classes
             passed to check() at runtime.
    """

    severity: str = "error"
    classes: list[OntologyClass] = []


class CrossSourceAgreementCheck(DqMergeRunner):
    """Built-in DQ check: flag canonical_ids where UNIQUE_OR_FAIL slots disagree.

    For each OntologyClass and each slot with resolution_policy==UNIQUE_OR_FAIL,
    reads per_source_facts/<class> from ctx.lake_dir, groups by canonical_id,
    and emits an offender row whenever COUNT(DISTINCT value) > 1.
    """

    Config: ClassVar[type] = CrossSourceAgreementCheckConfig

    resolved: DataContext = DataContext(primary=[])  # populated at runtime via ctx

    _COLUMN_MAP = DqColumnMap(
        rule_id="rule_id",
        class_name="class_name",
        slot_name="slot_name",
        offending_pk="offending_pk",
        severity="severity",
        detail="detail",
    )

    def check(self, ctx: Any, **datacontexts: Any) -> DqResult:
        cfg = getattr(ctx, "config", None) or CrossSourceAgreementCheckConfig()
        severity = getattr(cfg, "severity", "error")

        # Determine which classes to inspect.
        cfg_classes: list[OntologyClass] = getattr(cfg, "classes", []) or []
        run_id: str = getattr(ctx, "run_id", None) or uuid.uuid4().hex

        # query_reader for ad-hoc reads; materializer for writing offender output.
        query_reader = getattr(ctx, "query_reader", None)
        materializer = getattr(ctx, "materializer", None)

        # Collect offender rows across all classes.
        offender_rows: list[dict] = []

        for cls in cfg_classes:
            cls_name: str = cls.name
            uof_slots = [
                s for s in (cls.slots or [])
                if getattr(s, "resolution_policy", None) == ResolutionPolicy.UNIQUE_OR_FAIL
            ]
            if not uof_slots:
                continue

            # Load the per-source facts table for this class.
            # Tests may supply a pre-built pa.Table via datacontexts[cls_name].
            tbl: pa.Table | None = datacontexts.get(cls_name)
            if tbl is None:
                if query_reader is not None:
                    # Production path: read via the bound QueryReader.
                    view_name = f"psf_{cls_name.lower()}"
                    try:
                        tbl = query_reader.read(
                            ctx=ctx,
                            sql=f"SELECT * FROM {view_name}",
                        )
                    except Exception:
                        continue
                else:
                    # Fallback: read from lake_dir when no query_reader available.
                    lake_dir = Path(getattr(ctx, "lake_dir", "/tmp"))
                    facts_path = lake_dir / "per_source_facts" / f"{cls_name.lower()}.parquet"
                    if not facts_path.exists():
                        continue
                    tbl = pq.read_table(facts_path)

            schema_names = set(tbl.schema.names)
            if "canonical_id" not in schema_names:
                continue

            for slot in uof_slots:
                col = slot.name
                if col not in schema_names:
                    continue

                # Group by canonical_id, collect distinct non-null values.
                cid_col = tbl.column("canonical_id")
                val_col = tbl.column(col)

                groups: dict[str, set] = {}
                for i in range(tbl.num_rows):
                    cid = cid_col[i].as_py()
                    val = val_col[i].as_py()
                    if cid is None or val is None:
                        continue
                    groups.setdefault(cid, set()).add(val)

                for cid, vals in groups.items():
                    if len(vals) > 1:
                        sorted_vals = sorted(str(v) for v in vals)
                        offender_rows.append({
                            "rule_id": "cross_source_agreement",
                            "class_name": cls_name,
                            "slot_name": col,
                            "offending_pk": cid,
                            "severity": severity,
                            "detail": f"{len(vals)} sources disagree: {', '.join(sorted_vals)}",
                        })

        # Write offender table.
        out_schema = pa.schema([
            ("rule_id", pa.string()),
            ("class_name", pa.string()),
            ("slot_name", pa.string()),
            ("offending_pk", pa.string()),
            ("severity", pa.string()),
            ("detail", pa.string()),
        ])

        offenders_uri: str | None = None
        if offender_rows:
            out_table = pa.table(
                {k: [r[k] for r in offender_rows] for k in out_schema.names},
                schema=out_schema,
            )
            target_rel = f"dq_outputs/merge_{run_id}.parquet"
            if materializer is not None:
                materializer._conn.register("_dq_merge_offenders_tmp", out_table)
                mat_result = materializer.materialize(
                    ctx=ctx,
                    query_sql="SELECT * FROM _dq_merge_offenders_tmp",
                    target_path=Path(target_rel),
                )
                offenders_uri = mat_result.target
            else:
                lake_dir = Path(getattr(ctx, "lake_dir", "/tmp"))
                out_path = lake_dir / target_rel
                out_path.parent.mkdir(parents=True, exist_ok=True)
                pq.write_table(out_table, out_path)
                offenders_uri = str(out_path)

        return DqResult(
            offenders_uri=offenders_uri,
            column_map=self._COLUMN_MAP,
            passed=len(offender_rows) == 0,
            summary={"violations": len(offender_rows)},
        )
