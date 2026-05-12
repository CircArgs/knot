"""Spec edit concurrency tests.

``edit_draft`` holds a row lock for the full read-modify-write window, so two
parallel mutations on the same draft serialize and both changes survive. Plain
``get_revision`` + ``update_draft`` would have a lost-update race here.
"""

from __future__ import annotations

import asyncio

import psycopg
import pytest

from knot import db
from knot.config.config import get_dsn
from knot.db import spec_store
from knot.db.spec_store import (
    create_draft,
    edit_draft,
)
from knot.spec import OntologyClass, Spec
from knot.spec.errors import DraftAlreadyPublishedError, DraftNotFoundError
from knot.spec.metaschema import Primitive, Slot


def _empty_spec() -> Spec:
    # A spec with a single empty class so workers can append slots to it.
    cls = OntologyClass(name="Conc", slots=[])
    return Spec(id="conc", version="1.0.0", classes=[cls])


async def _new_conn() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(get_dsn(), autocommit=True)


async def _reset(conn):
    await conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    await conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    await conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    await conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    await conn.execute("TRUNCATE TABLE trust_config CASCADE")
    await conn.execute("TRUNCATE TABLE users CASCADE")
    await conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    await db.apply_schema()


# ---------------------------------------------------------------------------
# 1. Lost-update race is prevented
# ---------------------------------------------------------------------------


async def test_concurrent_edit_draft_serializes(pg_conn):
    """Two coroutines mutating the same draft in parallel: both writes survive.

    Without row-level locking, this race would lose one of the type additions
    (both readers read the empty types list, both append their type, both
    write back — last writer wins).
    """
    await _reset(pg_conn)
    rev = await create_draft(pg_conn)
    await spec_store.update_draft(pg_conn, rev, _empty_spec())

    errors: list[BaseException] = []

    async def worker(slot_name: str) -> None:
        try:
            conn = await _new_conn()
            try:
                async with edit_draft(conn, rev) as spec:
                    spec.classes[0].slots.append(
                        Slot(name=slot_name, type=Primitive(name="string"))
                    )
                    # Small yield to allow the other coroutine to attempt the lock.
                    await asyncio.sleep(0.1)
            finally:
                await conn.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    await asyncio.gather(worker("slot_a"), worker("slot_b"))

    assert errors == [], errors

    # Both writes must survive — this is the property a non-locking
    # implementation would violate.
    final = await spec_store.get_revision(pg_conn, rev)
    names = {s.name for s in final.classes[0].slots}
    assert names == {"slot_a", "slot_b"}, names


# ---------------------------------------------------------------------------
# 2. Exception inside the with-block aborts the writeback
# ---------------------------------------------------------------------------


async def test_edit_draft_rolls_back_on_exception(pg_conn):
    """An exception inside the with-block leaves the draft unchanged."""
    await _reset(pg_conn)
    rev = await create_draft(pg_conn)
    await spec_store.update_draft(pg_conn, rev, _empty_spec())

    pre = await spec_store.get_revision(pg_conn, rev)
    pre_count = len(pre.classes[0].slots)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with edit_draft(pg_conn, rev) as spec:
            spec.classes[0].slots.append(
                Slot(name="should_not_persist", type=Primitive(name="string"))
            )
            raise Boom()

    after = await spec_store.get_revision(pg_conn, rev)
    assert len(after.classes[0].slots) == pre_count
    assert all(s.name != "should_not_persist" for s in after.classes[0].slots)


# ---------------------------------------------------------------------------
# 3. edit_draft on an unknown revision
# ---------------------------------------------------------------------------


async def test_edit_draft_not_found(pg_conn):
    await _reset(pg_conn)
    with pytest.raises(DraftNotFoundError):
        async with edit_draft(pg_conn, 9_999_999) as _spec:
            pass


# ---------------------------------------------------------------------------
# 4. edit_draft on an already-published revision
# ---------------------------------------------------------------------------


async def test_edit_draft_rejects_published(pg_conn):
    """Once a revision is published, edit_draft refuses to mutate it."""
    await _reset(pg_conn)
    rev = await create_draft(pg_conn)
    await spec_store.update_draft(pg_conn, rev, _empty_spec())
    await spec_store.publish_draft(pg_conn, rev)

    with pytest.raises(DraftAlreadyPublishedError):
        async with edit_draft(pg_conn, rev) as _spec:
            pass
