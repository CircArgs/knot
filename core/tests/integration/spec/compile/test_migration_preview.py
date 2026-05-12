"""Preview endpoint integration tests.

Tests call graph_spec.preview_publish() directly against real postgres.
HTTP routing is tested through FastAPI TestClient in the API tests.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.db.spec_store import (
    create_draft,
    publish_draft,
    update_draft,
)
from knot.graph.spec import preview_publish
from knot.spec import OntologyClass, Slot, Source, SourceBinding, Spec
from knot.spec.compile.postgres._naming import schema
from knot.spec.metaschema import Primitive

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def clean_db(pg_conn):
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
# Helpers
# ---------------------------------------------------------------------------


def _minimal_spec() -> Spec:
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    return Spec(
        id="t", version="1.0.0", classes=[movie],
        sources=[src], source_bindings=[binding],
    )


# ---------------------------------------------------------------------------
# Preview — no changes (publishable=True)
# ---------------------------------------------------------------------------


async def test_preview_no_changes_is_publishable(clean_db):
    """A draft identical to the published spec has no blockers."""
    spec = _minimal_spec()
    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec)
    await publish_draft(clean_db, rev1)

    # Draft branched from published with same content.
    rev2 = await create_draft(clean_db, parent_revision=rev1)
    result = await preview_publish(clean_db, rev2)

    assert result.draft_revision == rev2
    assert result.publishable is True
    assert result.blockers == []
    assert result.requires_allow_destructive is False
    assert result.changes == []


# ---------------------------------------------------------------------------
# Preview — DropClass → publishable=False, requires_allow_destructive=True
# ---------------------------------------------------------------------------


async def test_preview_drop_class_not_publishable(clean_db):
    """Dropping a class is Bucket A; preview shows it as a blocker."""
    imdb_id = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    extra_id = Slot(name="extra_id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie", slots=[imdb_id])
    series = OntologyClass(name="Series", slots=[extra_id])
    src_m = Source(name="imdb")
    src_s = Source(name="wiki")
    binding_m = SourceBinding(source=src_m, class_=movie, identifier_slot=imdb_id)  # type: ignore[call-arg]
    binding_s = SourceBinding(source=src_s, class_=series, identifier_slot=extra_id)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie, series],
        sources=[src_m, src_s],
        source_bindings=[binding_m, binding_s],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # v2 drops Series.
    imdb_id2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    movie2 = OntologyClass(name="Movie", slots=[imdb_id2])
    src_m2 = Source(name="imdb")
    binding_m2 = SourceBinding(source=src_m2, class_=movie2, identifier_slot=imdb_id2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t", version="1.0.0", classes=[movie2],
        sources=[src_m2], source_bindings=[binding_m2],
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    result = await preview_publish(clean_db, rev2)

    assert result.publishable is False
    assert result.requires_allow_destructive is True
    # DropClass and DropSource should both appear in Bucket A.
    assert "DropClass" in result.buckets["A"] or "DropSource" in result.buckets["A"]
    # The destructive_changes blocker must be present.
    kinds = {b["kind"] for b in result.blockers}
    assert "destructive_changes" in kinds


# ---------------------------------------------------------------------------
# Preview — bad cast → publishable=False, blocker kind=type_cast_failure
# ---------------------------------------------------------------------------


async def test_preview_bad_cast_shows_blocker(clean_db):
    """A TEXT→INTEGER cast on non-numeric data surfaces as a preflight blocker."""
    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_str = Slot(name="year", type=Primitive(name="string"))
    movie = OntologyClass(name="Movie", slots=[id_slot, year_str])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t", version="1.0.0", classes=[movie],
        sources=[src], source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert bad data.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, year) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'not-a-number')",
        (rev1,),
    )

    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_int = Slot(name="year", type=Primitive(name="integer"))
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, year_int])
    src2 = Source(name="imdb")
    binding2 = SourceBinding(source=src2, class_=movie2, identifier_slot=id_slot2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t", version="1.0.0", classes=[movie2],
        sources=[src2], source_bindings=[binding2],
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    result = await preview_publish(clean_db, rev2)

    assert result.publishable is False
    kinds = {b["kind"] for b in result.blockers}
    assert "type_cast_failure" in kinds
    # ChangeSlotTypeExpression is Bucket A.
    assert "ChangeSlotTypeExpression" in result.buckets["A"]


# ---------------------------------------------------------------------------
# Preview — constraint violation → publishable=False, blocker kind=constraint_violation
# ---------------------------------------------------------------------------


async def test_preview_constraint_violation_shows_blocker(clean_db):
    """A new ERROR-severity constraint that existing data violates surfaces as a blocker."""
    from knot.spec.expressions import Compare, CompareOp, Literal_, SlotPath
    from knot.spec.metaschema import Constraint, Severity

    id_slot = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_slot = Slot(name="year", type=Primitive(name="integer"))
    movie = OntologyClass(name="Movie", slots=[id_slot, year_slot])
    src = Source(name="imdb")
    binding = SourceBinding(source=src, class_=movie, identifier_slot=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t", version="1.0.0", classes=[movie],
        sources=[src], source_bindings=[binding],
    )

    rev1 = await create_draft(clean_db)
    await update_draft(clean_db, rev1, spec_v1)
    await publish_draft(clean_db, rev1)

    # Insert a row with year=1800 which will violate our new constraint.
    # Must also insert a binding row — the constraint query joins via bindings.
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, year) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 1800)",
        (rev1,),
    )
    knot_row_id = await (
        await clean_db.execute(
            f"SELECT _knot_row_id FROM {schema()}.movie WHERE _source_row_id = 'tt1'"
        )
    ).fetchone()
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie_bindings "
        "(knot_row_id, canonical_id, valid_from, valid_to, change_type, applied_revision) "
        "VALUES (%s, 'imdb:tt1', now(), NULL, 'ingest', %s)",
        (knot_row_id[0], rev1),
    )

    # v2 adds a constraint: year >= 1900.
    id_slot2 = Slot(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    year_slot2 = Slot(name="year", type=Primitive(name="integer"))
    movie2 = OntologyClass(name="Movie", slots=[id_slot2, year_slot2])
    src2 = Source(name="imdb")
    binding2 = SourceBinding(source=src2, class_=movie2, identifier_slot=id_slot2)  # type: ignore[call-arg]

    # Build constraint using expression node objects directly.
    path = SlotPath(from_class=movie2, slots=[year_slot2])
    body = Compare(op=CompareOp.GTE, left=path, right=Literal_(value=1900))
    con = Constraint(
        name="year_gte_1900",
        primary=movie2,
        body=body,
        severity=Severity.ERROR,
    )
    spec_v2 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie2],
        sources=[src2],
        source_bindings=[binding2],
        constraints=[con],
    )

    rev2 = await create_draft(clean_db)
    await update_draft(clean_db, rev2, spec_v2)

    result = await preview_publish(clean_db, rev2)

    assert result.publishable is False
    kinds = {b["kind"] for b in result.blockers}
    assert "constraint_violation" in kinds
