"""Execute-against-DB tests for compile_constraint + publish-gate constraint pass.

The compile-output assertions for each predicate handler live in
``tests/unit/spec/compile/test_sql_compiler.py``. This file covers
end-to-end flows that need a running postgres: compile a constraint,
ingest data, execute the compiled SQL, and assert the offending-row
output shape; plus the publish-gate's step-4 constraint check.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.spec.compile.postgres import compile_constraint
from knot.spec.errors import PublishGateError
from knot.spec.metaschema import (
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Constraint,
    Literal_,
    OntologyClass,
    Primitive,
    Severity,
    Slot,
    SlotPath,
    Source,
    SourceBinding,
    Spec,
)


@pytest_asyncio.fixture
async def clean_db(pg_conn):
    """Truncate all state, re-apply schema(), yield the connection."""
    await pg_conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await pg_conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await pg_conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await pg_conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await pg_conn.execute("TRUNCATE TABLE users CASCADE")
    await pg_conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()
    yield pg_conn


# ---------------------------------------------------------------------------
# compile_constraint end-to-end: offending rows returned
# ---------------------------------------------------------------------------


async def test_compile_constraint_catches_violating_rows(clean_db):
    """Publish a spec with a year-in-range constraint, ingest a violating row,
    then compile + execute the constraint and verify the offending row is returned
    in the uniform violation shape.
    """
    conn = clean_db

    # Build spec with constraint: year must be >= 1888 AND <= 2100
    imdb_id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_slot = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id_slot)  # type: ignore[call-arg]

    body = BoolExpr(
        op=BoolOpKind.AND,
        operands=[
            Compare(
                op=CompareOp.GTE,
                left=SlotPath(from_class=movie, slots=[year_slot]),
                right=Literal_(value=1888),
            ),
            Compare(
                op=CompareOp.LTE,
                left=SlotPath(from_class=movie, slots=[year_slot]),
                right=Literal_(value=2100),
            ),
        ],
    )
    constraint = Constraint(
        name="year_in_range",
        primary=movie,
        body=body,
        severity=Severity.ERROR,
    )
    spec = Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=[constraint],
    )

    # Publish the spec (constraint check skipped on first publish — no prev).
    rev = await create_draft(conn)
    await update_draft(conn, rev, spec)
    await publish_draft(conn, rev)

    # Ingest one valid and one violating row directly via the store.
    from knot.db.graph_store import insert_rows

    await insert_rows(
        conn,
        source=src,
        cls=movie,
        spec_revision=rev,
        rows=[
            {"imdb_id": "tt0000001", "year": 1972},  # valid
            {"imdb_id": "tt0000002", "year": 1800},  # violates year >= 1888
        ],
        canonical_ids=["tt0000001", "tt0000002"],
    )

    # Compile the constraint and execute it.
    stmt, params = compile_constraint(constraint, movie)
    cur = await conn.execute(stmt, params)
    rows = await cur.fetchall()

    assert len(rows) == 1
    rule_id, class_name, slot_name, offending_pk, detail = rows[0]
    assert rule_id == "year_in_range"
    assert class_name == "Movie"
    assert slot_name is None
    assert offending_pk == "tt0000002"


async def test_compile_constraint_no_violations(clean_db):
    """All rows valid → constraint returns zero offending rows."""
    conn = clean_db

    imdb_id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_slot = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id_slot)  # type: ignore[call-arg]

    body = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=movie, slots=[year_slot]),
        right=Literal_(value=1888),
    )
    constraint = Constraint(
        name="year_gte_1888",
        primary=movie,
        body=body,
        severity=Severity.ERROR,
    )
    spec = Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=[constraint],
    )

    rev = await create_draft(conn)
    await update_draft(conn, rev, spec)
    await publish_draft(conn, rev)

    from knot.db.graph_store import insert_rows

    await insert_rows(
        conn,
        source=src,
        cls=movie,
        spec_revision=rev,
        rows=[{"imdb_id": "tt0000001", "year": 2000}],
        canonical_ids=["tt0000001"],
    )

    stmt, params = compile_constraint(constraint, movie)
    cur = await conn.execute(stmt, params)
    rows = await cur.fetchall()
    assert rows == []


# ---------------------------------------------------------------------------
# Publish gate step 4: ERROR severity blocks publish; WARNING does not
# ---------------------------------------------------------------------------


async def test_publish_gate_blocks_error_constraint_on_existing_data(clean_db):
    """Publish v1 (no constraints), ingest violating row, publish v2 with an
    ERROR constraint → PublishGateError raised.
    """
    conn = clean_db

    imdb_id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_slot = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id_slot)  # type: ignore[call-arg]

    # v1: no constraints
    spec_v1 = Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )
    rev1 = await create_draft(conn)
    await update_draft(conn, rev1, spec_v1)
    await publish_draft(conn, rev1)

    # Ingest a row that will violate the upcoming constraint.
    from knot.db.graph_store import insert_rows

    await insert_rows(
        conn,
        source=src,
        cls=movie,
        spec_revision=rev1,
        rows=[{"imdb_id": "tt0000001", "year": 1800}],
        canonical_ids=["tt0000001"],
    )

    # v2: add ERROR constraint that the ingested row violates.
    body = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=movie, slots=[year_slot]),
        right=Literal_(value=1888),
    )
    constraint = Constraint(
        name="year_gte_1888",
        primary=movie,
        body=body,
        severity=Severity.ERROR,
    )
    spec_v2 = Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=[constraint],
    )
    rev2 = await create_draft(conn)
    await update_draft(conn, rev2, spec_v2)

    with pytest.raises(PublishGateError, match="year_gte_1888"):
        await publish_draft(conn, rev2)


async def test_publish_gate_warning_constraint_allows_publish(clean_db):
    """Same setup but constraint is WARNING → publish succeeds."""
    conn = clean_db

    imdb_id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_slot = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[imdb_id_slot, year_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id_slot)  # type: ignore[call-arg]

    spec_v1 = Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
    )
    rev1 = await create_draft(conn)
    await update_draft(conn, rev1, spec_v1)
    await publish_draft(conn, rev1)

    from knot.db.graph_store import insert_rows

    await insert_rows(
        conn,
        source=src,
        cls=movie,
        spec_revision=rev1,
        rows=[{"imdb_id": "tt0000001", "year": 1800}],
        canonical_ids=["tt0000001"],
    )

    body = Compare(
        op=CompareOp.GTE,
        left=SlotPath(from_class=movie, slots=[year_slot]),
        right=Literal_(value=1888),
    )
    constraint = Constraint(
        name="year_gte_1888",
        primary=movie,
        body=body,
        severity=Severity.WARNING,  # WARNING — should not block
    )
    spec_v2 = Spec(
        id="test",
        version="1.0.0",
        classes=[movie],
        sources=[src],
        source_bindings=[binding],
        constraints=[constraint],
    )
    rev2 = await create_draft(conn)
    await update_draft(conn, rev2, spec_v2)

    # Should not raise.
    result = await publish_draft(conn, rev2)
    assert result == rev2
