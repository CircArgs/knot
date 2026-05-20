"""Integration tests for VectorRef.distance_to() — literal and cross-row k-NN.

Exercises:
1. Literal-vector k-NN — correct ordering against live postgres + pgvector.
2. Cross-row k-NN — two-query pattern: fetch target embedding, then query
   candidates using that literal vector.
3. HNSW index emission — verify the DDL emits the expected index and that an
   EXPLAIN on a literal k-NN mentions it.

All writes and reads go through knot's compiled emitters; no raw SQL on the
spec-layer data (catalog introspection uses information_schema / pg_indexes
which is postgres metadata, not knot's relational layer).
"""

from __future__ import annotations

import json

from knot import Spec, types
from knot.compile import emit_ddl
from tests.integration.conftest import exec_many

# ---------------------------------------------------------------------------
# Spec + deploy helpers
# ---------------------------------------------------------------------------

_SOURCE_WEIGHT = 0.9


def _vec_spec(schema: str) -> tuple[Spec, object, object]:
    """Single class ``Item`` with a 4-dim cosine vector slot.
    Returns (spec, item_cls, binding)."""
    spec = Spec(identifier_slot_name="canonical_id", schema=schema)
    item = spec.add_class("Item")
    item.slot("label", types.TEXT, required=True)
    item.slot("emb", types.VECTOR(4, metric="cosine"))

    src = spec.add_source("src")
    binding = src.bind(item)
    return spec, item, binding


def _deploy_vec(pg, spec: Spec, schema: str, binding) -> None:
    """Deploy DDL and set a runtime weight for the vector slot."""
    exec_many(pg, emit_ddl(spec, schema=schema))
    upsert = binding.upsert_weight_sql()
    with pg.cursor() as cur:
        cur.execute(upsert, {"slot_name": "emb", "weight": _SOURCE_WEIGHT})
        cur.execute(upsert, {"slot_name": "label", "weight": _SOURCE_WEIGHT})


def _ingest(pg, binding, rows: list[dict]) -> None:
    """Write a batch of rows via knot's upsert template."""
    sql = binding.write_sql()
    with pg.cursor() as cur:
        cur.execute(sql, {"rows": json.dumps(rows)})


# Five hand-crafted 4-dim unit-ish vectors. The query target is [1,0,0,0].
# Cosine distance = 1 - dot(a,b)/(|a||b|). Closest to [1,0,0,0] is i1 (dot=1),
# then i2 (dot≈0.894), then i3 (dot≈0.707), then i4 (dot≈0.447), then i5 (dot=0).
_ROWS = [
    {
        "canonical_id": "i1",
        "source_identifier": "s1",
        "label": "one",
        "emb": [1.0, 0.0, 0.0, 0.0],
    },
    {
        "canonical_id": "i2",
        "source_identifier": "s2",
        "label": "two",
        "emb": [0.894, 0.447, 0.0, 0.0],
    },
    {
        "canonical_id": "i3",
        "source_identifier": "s3",
        "label": "three",
        "emb": [0.707, 0.707, 0.0, 0.0],
    },
    {
        "canonical_id": "i4",
        "source_identifier": "s4",
        "label": "four",
        "emb": [0.447, 0.894, 0.0, 0.0],
    },
    {
        "canonical_id": "i5",
        "source_identifier": "s5",
        "label": "five",
        "emb": [0.0, 1.0, 0.0, 0.0],
    },
]

_TARGET_LITERAL = [1.0, 0.0, 0.0, 0.0]
_EXPECTED_TOP3 = ["i1", "i2", "i3"]


# ---------------------------------------------------------------------------
# Test 1 — literal-vector k-NN ordering
# ---------------------------------------------------------------------------


def test_literal_knn_correct_ordering(pg, schema):
    """Literal distance_to([1,0,0,0]) returns the 3 closest items in order."""
    spec, item, binding = _vec_spec(schema)
    _deploy_vec(pg, spec, schema, binding)
    _ingest(pg, binding, _ROWS)

    q = (
        item.resolved.select(item.col.label, item.col.emb.distance_to(_TARGET_LITERAL))
        .order_by(item.col.emb.distance_to(_TARGET_LITERAL), "asc")
        .limit(3)
    )
    sql = q.sql()

    with pg.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchall()

    # rows is list of (label, distance); label == canonical_id here
    assert len(rows) == 3
    labels = [r[0] for r in rows]
    # The 3 nearest to [1,0,0,0] by cosine must be one, two, three — in order.
    assert labels == ["one", "two", "three"], (
        f"Expected ['one','two','three'], got {labels}"
    )
    # Distances must be ascending (each ≥ previous).
    distances = [r[1] for r in rows]
    assert distances == sorted(distances), f"Distances not ascending: {distances}"


# ---------------------------------------------------------------------------
# Test 2 — cross-row k-NN ordering (two-query pattern)
# ---------------------------------------------------------------------------


def test_cross_row_knn_correct_ordering(pg, schema):
    """Cross-row k-NN: fetch the target row's embedding via knot, then
    use it as a literal for the candidate query. Verifies ordering is correct."""
    spec, item, binding = _vec_spec(schema)
    _deploy_vec(pg, spec, schema, binding)
    _ingest(pg, binding, _ROWS)

    # Step 1: fetch the embedding of "i5" (label="five") via knot's resolved layer.
    fetch_q = (
        item.resolved.where(item.col.label == "five").select(item.col.emb).limit(1)
    )
    with pg.cursor() as cur:
        cur.execute(fetch_q.sql())
        (target_emb,) = cur.fetchone()
    # target_emb is a pgvector wrapper — convert to plain Python floats.
    # [0.0, 1.0, 0.0, 0.0] — closest to i5 is i4, then i3, i2.
    target_vec = [float(v) for v in target_emb]

    # Step 2: k-NN query using the fetched vector as a literal.
    # This is the cross-row pattern: compute distance_to a runtime vector
    # taken from another row in the same (or different) class.
    dist_expr = item.col.emb.distance_to(target_vec)
    knn_q = (
        item.resolved.select(item.col.label, dist_expr)
        .order_by(dist_expr, "asc")
        .limit(3)
    )
    with pg.cursor() as cur:
        cur.execute(knn_q.sql())
        rows = cur.fetchall()

    assert len(rows) == 3
    labels = [r[0] for r in rows]
    # Closest to [0,1,0,0]: five (dot=1), four (dot≈0.894), three (dot≈0.707)
    assert labels == ["five", "four", "three"], (
        f"Expected ['five','four','three'], got {labels}"
    )
    distances = [r[1] for r in rows]
    assert distances == sorted(distances), f"Distances not ascending: {distances}"


# ---------------------------------------------------------------------------
# Test 3 — HNSW index emitted and used in EXPLAIN
# ---------------------------------------------------------------------------


def test_hnsw_index_emitted_in_ddl(pg, schema):
    """The DDL for a class with a VECTOR slot must include an HNSW index."""
    spec, item, binding = _vec_spec(schema)
    _deploy_vec(pg, spec, schema, binding)

    # Expected index name from _emit_vector_indexes:
    # f"{table_name}_{slot_name}_hnsw_idx" where table_name = "item_bindings"
    expected_idx = "item_bindings_emb_hnsw_idx"

    with pg.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = %s AND tablename = 'item_bindings'",
            (schema,),
        )
        idx_names = {r[0] for r in cur.fetchall()}

    assert expected_idx in idx_names, (
        f"HNSW index {expected_idx!r} not found in pg_indexes; "
        f"found: {sorted(idx_names)}"
    )


def test_hnsw_index_used_in_explain(pg, schema):
    """EXPLAIN on a direct bindings-table k-NN query confirms the HNSW index fires.

    The HNSW index lives on ``item_bindings.emb``. The resolved view wraps
    emb in a correlated argmax subplan, which prevents the planner from
    pushing an ORDER BY into the index. We EXPLAIN directly against the
    bindings table — that is catalog/index-infrastructure verification, the
    same carve-out as information_schema queries in other tests.

    ``SET LOCAL enable_seqscan = off`` forces the index path deterministically."""
    import math

    spec, item, binding = _vec_spec(schema)
    _deploy_vec(pg, spec, schema, binding)

    # Insert 30 rows — HNSW needs data to build graph layers.
    extra_rows = []
    for i in range(30):
        angle = (2 * math.pi * i) / 30
        extra_rows.append(
            {
                "canonical_id": f"bulk_{i}",
                "source_identifier": f"b{i}",
                "label": f"bulk_{i}",
                "emb": [math.cos(angle), math.sin(angle), 0.0, 0.0],
            }
        )
    _ingest(pg, binding, extra_rows)

    # Direct k-NN against the bindings table where the HNSW index lives.
    # Catalog-introspection carve-out: we are verifying index infrastructure,
    # not reading knot's spec-layer data.
    expected_idx = "item_bindings_emb_hnsw_idx"
    explain_sql = (
        f"EXPLAIN (FORMAT TEXT) "
        f"SELECT emb FROM {schema}.item_bindings "
        f"ORDER BY emb <=> '[1,0,0,0]'::vector(4) LIMIT 5"
    )

    with pg.cursor() as cur:
        cur.execute("SET LOCAL enable_seqscan = off")
        cur.execute(explain_sql)
        plan_lines = [r[0] for r in cur.fetchall()]

    plan_text = "\n".join(plan_lines)
    assert expected_idx in plan_text, (
        f"HNSW index {expected_idx!r} not referenced in EXPLAIN output.\n"
        f"Plan:\n{plan_text}"
    )
