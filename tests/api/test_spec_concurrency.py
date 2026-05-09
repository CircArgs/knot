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
from knot.spec import Spec, TypeDefinition
from knot.spec.errors import DraftAlreadyPublishedError, DraftNotFoundError


def _empty_spec() -> Spec:
    return Spec(id="conc", version="1.0.0")


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

    started: list[float] = []
    finished: list[float] = []
    errors: list[BaseException] = []

    async def worker(type_name: str) -> None:
        try:
            conn = await _new_conn()
            try:
                async with edit_draft(conn, rev) as spec:
                    spec.types.append(TypeDefinition(name=type_name, base="str"))
                    # Small yield to allow the other coroutine to attempt the lock.
                    await asyncio.sleep(0.1)
            finally:
                await conn.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    await asyncio.gather(worker("Type_A"), worker("Type_B"))

    assert errors == [], errors

    # Both writes must survive — this is the property a non-locking
    # implementation would violate.
    final = await spec_store.get_revision(pg_conn, rev)
    names = {t.name for t in final.types}
    assert names == {"Type_A", "Type_B"}, names


# ---------------------------------------------------------------------------
# 2. Exception inside the with-block aborts the writeback
# ---------------------------------------------------------------------------


async def test_edit_draft_rolls_back_on_exception(pg_conn):
    """An exception inside the with-block leaves the draft unchanged."""
    await _reset(pg_conn)
    rev = await create_draft(pg_conn)
    await spec_store.update_draft(pg_conn, rev, _empty_spec())

    pre = await spec_store.get_revision(pg_conn, rev)
    pre_count = len(pre.types)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with edit_draft(pg_conn, rev) as spec:
            spec.types.append(TypeDefinition(name="should_not_persist", base="str"))
            raise Boom()

    after = await spec_store.get_revision(pg_conn, rev)
    assert len(after.types) == pre_count
    assert all(t.name != "should_not_persist" for t in after.types)


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
