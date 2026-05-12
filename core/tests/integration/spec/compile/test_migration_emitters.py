"""End-to-end DDL-emitter tests for the destructive change records.

Each test sets up a postgres state matching ``prev``, runs
``apply_changes(conn, diff_specs(prev, candidate))`` and asserts the resulting
state matches what ``candidate`` describes. The ``clean_db`` fixture mirrors
the one used in ``test_migration.py``.

The records covered here are the bucket-A storage-shape rewrites whose
DDL emitters were blocking the publish gate's ``allow_destructive=true`` knob.
"""

from __future__ import annotations

import pytest

from knot import db
from knot.spec import (
    OntologyClass,
    Property,
    Source,
    SourceBinding,
    Spec,
)
from knot.spec.compile.postgres._dispatch import CompilerError
from knot.spec.compile.postgres._naming import schema
from knot.spec.compile.postgres.migration import (
    DropSourceBinding,
    apply_changes,
    diff_specs,
    is_destructive,
)
from knot.spec.metaschema import Array, Primitive

# ---------------------------------------------------------------------------
# Fixture (mirrors test_migration.py)
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


async def _column_type(conn, table_name: str, column_name: str) -> str:
    """Return ``data_type`` (e.g. ``text``, ``bigint``, ``ARRAY``) for a column."""
    row = await (
        await conn.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            (schema(), table_name, column_name),
        )
    ).fetchone()
    return row[0] if row else None


async def _udt_name(conn, table_name: str, column_name: str) -> str:
    """Return ``udt_name`` (postgres native type id, e.g. ``text``, ``int8``,
    ``_text`` for text arrays). Useful for telling array element types apart."""
    row = await (
        await conn.execute(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = %s",
            (schema(), table_name, column_name),
        )
    ).fetchone()
    return row[0] if row else None


async def _table_exists(conn, table_name: str) -> bool:
    row = await (
        await conn.execute(
            "SELECT 1 FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
            (schema(), table_name),
        )
    ).fetchone()
    return row is not None


async def _ensure_revision(conn) -> int:
    """Insert a stub spec_revisions row so per-class tables can satisfy the
    ``_spec_revision`` FK. The actual content doesn't matter for these
    tests — we're exercising emit_ddl directly, not the publish flow."""
    # content_hash is CHAR(64); fill with zeros to satisfy the constraint.
    row = await (
        await conn.execute(
            """
            INSERT INTO spec_revisions (spec, content_hash, published, created_at)
            VALUES ('{}'::jsonb, %s, FALSE, now())
            RETURNING revision
            """,
            ("0" * 64,),
        )
    ).fetchone()
    return row[0]


# ---------------------------------------------------------------------------
# ChangePropertyTypeExpression — scalar→array (Primitive→Array)
# ---------------------------------------------------------------------------


async def test_change_slot_type_scalar_to_array_promotes_column(clean_db):
    """Scalar TEXT column promoted to TEXT[] via ChangePropertyTypeExpression."""
    id_slot = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    tags_v1 = Property(name="tags", type=Primitive(name="string"))
    movie_v1 = OntologyClass(name="Movie", properties=[id_slot, tags_v1])
    src = Source(name="imdb")
    binding_v1 = SourceBinding(source=src, class_=movie_v1, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_v1],
        sources=[src],
        source_bindings=[binding_v1],
    )
    await apply_changes(clean_db, diff_specs(None, spec_v1))

    # Insert a row with a scalar 'tags' value.
    rev = await _ensure_revision(clean_db)
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, tags) "
        "VALUES ('imdb', '1', %s, 'tt1', 'foo')",
        (rev,),
    )

    id_slot2 = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    tags_v2 = Property(name="tags", type=Array(of=Primitive(name="string")))
    movie_v2 = OntologyClass(name="Movie", properties=[id_slot2, tags_v2])
    src_v2 = Source(name="imdb")
    binding_v2 = SourceBinding(source=src_v2, class_=movie_v2, identifier_property=id_slot2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_v2],
        sources=[src_v2],
        source_bindings=[binding_v2],
    )
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))

    assert await _udt_name(clean_db, "movie", "tags") == "_text"  # text array
    row = await (
        await clean_db.execute(f"SELECT tags FROM {schema()}.movie WHERE _source_row_id = '1'")
    ).fetchone()
    assert row[0] == ["foo"]


async def test_change_slot_type_array_to_scalar_refused(clean_db):
    """Array → scalar is lossy and refused."""
    id_slot = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    tags_v1 = Property(name="tags", type=Array(of=Primitive(name="string")))
    movie_v1 = OntologyClass(name="Movie", properties=[id_slot, tags_v1])
    src = Source(name="imdb")
    binding_v1 = SourceBinding(source=src, class_=movie_v1, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_v1],
        sources=[src],
        source_bindings=[binding_v1],
    )
    await apply_changes(clean_db, diff_specs(None, spec_v1))

    id_slot2 = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    tags_v2 = Property(name="tags", type=Primitive(name="string"))
    movie_v2 = OntologyClass(name="Movie", properties=[id_slot2, tags_v2])
    src_v2 = Source(name="imdb")
    binding_v2 = SourceBinding(source=src_v2, class_=movie_v2, identifier_property=id_slot2)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_v2],
        sources=[src_v2],
        source_bindings=[binding_v2],
    )
    with pytest.raises(CompilerError, match="lossy"):
        await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))


# ---------------------------------------------------------------------------
# ChangeClassAbstract
# ---------------------------------------------------------------------------


async def test_change_class_abstract_true_to_false_creates_table(clean_db):
    """Abstract → concrete: table + bindings are created."""
    id_slot = Property(name="id", type=Primitive(name="string"), identifier=True, required=True)
    movie_abs = OntologyClass(name="Movie", abstract=True)
    spec_v1 = Spec(id="t", version="1.0.0", classes=[movie_abs])
    await apply_changes(clean_db, diff_specs(None, spec_v1))
    assert not await _table_exists(clean_db, "movie")

    movie_concrete = OntologyClass(name="Movie", abstract=False)
    src = Source(name="imdb")
    binding_concrete = SourceBinding(source=src, class_=movie_concrete, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v2 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_concrete],
        sources=[src],
        source_bindings=[binding_concrete],
    )
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))
    assert await _table_exists(clean_db, "movie")
    assert await _table_exists(clean_db, "movie_bindings")


async def test_change_class_abstract_false_to_true_drops_empty_table(clean_db):
    """Concrete → abstract: empty table is dropped."""
    id_slot = Property(name="id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v1 = OntologyClass(name="Movie")
    src = Source(name="imdb")
    binding_v1 = SourceBinding(source=src, class_=movie_v1, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_v1],
        sources=[src],
        source_bindings=[binding_v1],
    )
    await apply_changes(clean_db, diff_specs(None, spec_v1))
    assert await _table_exists(clean_db, "movie")

    # Drop the source first (sources can't reference an abstract class).
    movie_abs = OntologyClass(name="Movie", abstract=True)
    spec_v2 = Spec(id="t", version="1.0.0", classes=[movie_abs])
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))
    assert not await _table_exists(clean_db, "movie")
    assert not await _table_exists(clean_db, "movie_bindings")


async def test_change_class_abstract_false_to_true_refused_if_rows(clean_db):
    """Concrete → abstract: refused when the table has rows."""
    id_slot = Property(name="id", type=Primitive(name="string"), identifier=True, required=True)
    movie_v1 = OntologyClass(name="Movie", properties=[id_slot])
    src = Source(name="imdb")
    binding_v1 = SourceBinding(source=src, class_=movie_v1, identifier_property=id_slot)  # type: ignore[call-arg]
    spec_v1 = Spec(
        id="t",
        version="1.0.0",
        classes=[movie_v1],
        sources=[src],
        source_bindings=[binding_v1],
    )
    await apply_changes(clean_db, diff_specs(None, spec_v1))
    rev = await _ensure_revision(clean_db)
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, id) "
        "VALUES ('imdb', '1', %s, 'tt1')",
        (rev,),
    )

    movie_abs = OntologyClass(name="Movie", abstract=True)
    spec_v2 = Spec(id="t", version="1.0.0", classes=[movie_abs])
    with pytest.raises(CompilerError, match="non-empty"):
        await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))


# ---------------------------------------------------------------------------
# ChangeClassIsA — concrete class (no-op DDL)
# ---------------------------------------------------------------------------


async def test_change_class_is_a_concrete_is_no_op(clean_db):
    """For concrete classes, is_a doesn't drive DDL — own table, own slots."""
    a = OntologyClass(name="A")
    b = OntologyClass(name="B")

    def _build(parent_obj):
        child_id = Property(name="id", type=Primitive(name="string"), identifier=True, required=True)
        child = OntologyClass(name="Child", is_a=parent_obj, properties=[child_id])
        src = Source(name="s")
        binding = SourceBinding(source=src, class_=child, identifier_property=child_id)  # type: ignore[call-arg]
        return Spec(
            id="t",
            version="1.0.0",
            classes=[a, b, child],
            sources=[src],
            source_bindings=[binding],
        )

    spec_v1 = _build(a)
    await apply_changes(clean_db, diff_specs(None, spec_v1))
    rev = await _ensure_revision(clean_db)
    await clean_db.execute(
        f"INSERT INTO {schema()}.child "
        "(_source, _source_row_id, _spec_revision, id) "
        "VALUES ('s', '1', %s, 'c1')",
        (rev,),
    )

    spec_v2 = _build(b)
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))

    # Row still there; no schema rewrite happened.
    row = await (
        await clean_db.execute(f"SELECT id FROM {schema()}.child WHERE _source_row_id = '1'")
    ).fetchone()
    assert row[0] == "c1"


# ---------------------------------------------------------------------------
# ChangeSourceEntityClass (refused)
# ---------------------------------------------------------------------------


async def test_change_source_entity_class_is_destructive(clean_db):
    """Rebinding a source to a different class produces DropSourceBinding (destructive).

    With the SourceBinding reification, there is no single ChangeSourceEntityClass
    record — the diff produces DropSourceBinding(imdb, Movie) + AddSourceBinding(imdb, Series).
    DropSourceBinding is a Bucket-A destructive change: the publish gate blocks it
    unless allow_destructive=True, and even then the data migration (DELETE orphaned
    rows) must be handled manually outside the automated migration path.
    """
    id_slot = Property(name="id", type=Primitive(name="string"), identifier=True, required=True)
    movie = OntologyClass(name="Movie")
    series = OntologyClass(name="Series")

    def _build(target_cls):
        src = Source(name="imdb")
        binding = SourceBinding(source=src, class_=target_cls, identifier_property=id_slot)  # type: ignore[call-arg]
        return Spec(
            id="t",
            version="1.0.0",
            classes=[movie, series],
            sources=[src],
            source_bindings=[binding],
        )

    spec_v1 = _build(movie)
    await apply_changes(clean_db, diff_specs(None, spec_v1))

    spec_v2 = _build(series)
    changes = diff_specs(spec_v1, spec_v2)

    # The diff must contain a DropSourceBinding for the Movie binding.
    drop = next((c for c in changes if isinstance(c, DropSourceBinding)), None)
    assert drop is not None, "expected DropSourceBinding in diff"
    assert drop.source_name == "imdb"
    assert drop.class_name == "Movie"

    # DropSourceBinding is flagged as destructive (publish gate blocks it).
    assert is_destructive(drop)

    # apply_changes succeeds (no DDL emitted for DropSourceBinding — the
    # publish gate is the enforcement point, not the emitter).
    await apply_changes(clean_db, changes)


# ---------------------------------------------------------------------------
# ChangeSourceIdentifierSlot
# ---------------------------------------------------------------------------


async def test_change_source_identifier_slot_rekeys_rows(clean_db):
    """Rows are rekeyed: ``_source_row_id`` shifts to the new slot's value."""
    imdb_id = Property(name="imdb_id", type=Primitive(name="string"), identifier=True, required=True)
    title = Property(name="title", type=Primitive(name="string"), identifier=True, required=True)

    def _build(identifier):
        imdb_id_ = Property(
            name="imdb_id", type=Primitive(name="string"), identifier=True, required=True
        )
        title_ = Property(name="title", type=Primitive(name="string"), identifier=True, required=True)
        movie = OntologyClass(name="Movie", properties=[imdb_id_, title_])
        # Use the correct slot object from this class for the binding
        id_slot = imdb_id_ if identifier.name == "imdb_id" else title_
        src = Source(name="imdb")
        binding = SourceBinding(source=src, class_=movie, identifier_property=id_slot)  # type: ignore[call-arg]
        return Spec(
            id="t",
            version="1.0.0",
            classes=[movie],
            sources=[src],
            source_bindings=[binding],
        )

    spec_v1 = _build(imdb_id)
    await apply_changes(clean_db, diff_specs(None, spec_v1))
    rev = await _ensure_revision(clean_db)
    await clean_db.execute(
        f"INSERT INTO {schema()}.movie "
        "(_source, _source_row_id, _spec_revision, imdb_id, title) "
        "VALUES ('imdb', 'tt1', %s, 'tt1', 'A'), ('imdb', 'tt2', %s, 'tt2', 'B')",
        (rev, rev),
    )

    spec_v2 = _build(title)
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))

    rows = await (
        await clean_db.execute(f"SELECT _source_row_id, title FROM {schema()}.movie ORDER BY title")
    ).fetchall()
    assert [(r[0], r[1]) for r in rows] == [("A", "A"), ("B", "B")]


# ---------------------------------------------------------------------------
# ChangeClassMixins is a no-op (slot records do the work)
# ---------------------------------------------------------------------------


async def test_change_class_mixins_no_op_without_slot_changes(clean_db):
    """Adding/removing a mixin with no shared slots is a no-op DDL-wise; the
    audit record alone is harmless."""
    body_v1 = Property(name="body_v1", type=Primitive(name="string"))
    body_v2 = Property(name="body_v2", type=Primitive(name="string"))
    mixin_a = OntologyClass(name="MixinA", properties=[body_v1])
    mixin_b = OntologyClass(name="MixinB", properties=[body_v2])

    def _build(mixins):
        cls_id = Property(name="id", type=Primitive(name="string"), identifier=True, required=True)
        cls = OntologyClass(name="Movie", mixins=mixins, properties=[cls_id])
        src = Source(name="imdb")
        binding = SourceBinding(source=src, class_=cls, identifier_property=cls_id)  # type: ignore[call-arg]
        return Spec(
            id="t",
            version="1.0.0",
            classes=[mixin_a, mixin_b, cls],
            sources=[src],
            source_bindings=[binding],
        )

    spec_v1 = _build([mixin_a])
    await apply_changes(clean_db, diff_specs(None, spec_v1))

    spec_v2 = _build([mixin_a, mixin_b])
    # Should not raise — slot-level AddProperty adds body_v2; mixin record is no-op.
    await apply_changes(clean_db, diff_specs(spec_v1, spec_v2))

    row = await (
        await clean_db.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = 'movie' AND column_name = 'body_v2'",
            (schema(),),
        )
    ).fetchone()
    assert row is not None
