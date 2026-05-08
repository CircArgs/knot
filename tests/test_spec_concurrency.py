"""Spec edit concurrency tests.

``edit_draft`` holds a row lock for the full read-modify-write window, so two
parallel mutations on the same draft serialize and both changes survive. Plain
``get_revision`` + ``update_draft`` would have a lost-update race here.
"""

from __future__ import annotations

import threading
import time

import psycopg
import pytest

from knot import db
from knot.config import get_dsn
from knot.db import spec_store
from knot.db.spec_store import (
    DraftAlreadyPublishedError,
    DraftNotFoundError,
    create_draft,
    edit_draft,
)
from knot.ontology import Spec, TypeDefinition


def _empty_spec() -> Spec:
    return Spec(id="conc", version="1.0.0")


def _new_conn() -> psycopg.Connection:
    return psycopg.connect(get_dsn(), autocommit=True)


def _reset(conn):
    conn.execute("DROP SCHEMA IF EXISTS knot_data CASCADE")
    conn.execute("TRUNCATE TABLE canonical_id_lineage CASCADE")
    conn.execute("TRUNCATE TABLE _user_corrections CASCADE")
    conn.execute("TRUNCATE TABLE trust_posteriors CASCADE")
    conn.execute("TRUNCATE TABLE trust_config CASCADE")
    conn.execute("TRUNCATE TABLE users CASCADE")
    conn.execute("TRUNCATE TABLE spec_revisions CASCADE")
    db.apply_schema()


# ---------------------------------------------------------------------------
# 1. Lost-update race is prevented
# ---------------------------------------------------------------------------

def test_concurrent_edit_draft_serializes(pg_conn):
    """Two threads mutating the same draft in parallel: both writes survive.

    Without row-level locking, this race would lose one of the type additions
    (both threads read the empty types list, both append their type, both
    write back — last writer wins).
    """
    _reset(pg_conn)
    rev = create_draft(pg_conn)
    spec_store.update_draft(pg_conn, rev, _empty_spec())

    barrier = threading.Barrier(2)
    started: list[float] = []
    finished: list[float] = []
    errors: list[BaseException] = []

    def worker(type_name: str, hold_ms: int) -> None:
        try:
            with _new_conn() as conn:
                # Sync both threads to the same start point so they race
                # for the lock.
                barrier.wait(timeout=5)
                started.append(time.perf_counter())
                with edit_draft(conn, rev) as spec:
                    spec.types.append(TypeDefinition(name=type_name, base="str"))
                    # Hold the lock long enough that the other worker is
                    # demonstrably blocked.
                    time.sleep(hold_ms / 1000)
                finished.append(time.perf_counter())
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("Type_A", 200))
    t2 = threading.Thread(target=worker, args=("Type_B", 200))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert errors == [], errors

    # Both writes must survive — this is the property a non-locking
    # implementation would violate.
    final = spec_store.get_revision(pg_conn, rev)
    names = {t.name for t in final.types}
    assert names == {"Type_A", "Type_B"}, names

    # And the second writer must have started before the first one finished
    # (overlap in their elapsed windows), proving they actually raced — but
    # one finished after the other (serialization, not parallelism).
    assert len(started) == 2 and len(finished) == 2
    # Whichever finished second held the lock waiting; their start time was
    # before the other's finish, but their finish time was after.
    s_min, s_max = sorted(started)
    f_min, f_max = sorted(finished)
    assert s_max < f_min + 0.05, (
        "Workers did not overlap — barrier sync may have failed."
    )
    assert f_max > f_min, "Both finished simultaneously — locking absent."


# ---------------------------------------------------------------------------
# 2. Exception inside the with-block aborts the writeback
# ---------------------------------------------------------------------------

def test_edit_draft_rolls_back_on_exception(pg_conn):
    """An exception inside the with-block leaves the draft unchanged."""
    _reset(pg_conn)
    rev = create_draft(pg_conn)
    spec_store.update_draft(pg_conn, rev, _empty_spec())

    pre = spec_store.get_revision(pg_conn, rev)
    pre_count = len(pre.types)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with edit_draft(pg_conn, rev) as spec:
            spec.types.append(TypeDefinition(name="should_not_persist", base="str"))
            raise Boom()

    after = spec_store.get_revision(pg_conn, rev)
    assert len(after.types) == pre_count
    assert all(t.name != "should_not_persist" for t in after.types)


# ---------------------------------------------------------------------------
# 3. edit_draft on an unknown revision
# ---------------------------------------------------------------------------

def test_edit_draft_not_found(pg_conn):
    _reset(pg_conn)
    with pytest.raises(DraftNotFoundError):
        with edit_draft(pg_conn, 9_999_999) as _spec:
            pass


# ---------------------------------------------------------------------------
# 4. edit_draft on an already-published revision
# ---------------------------------------------------------------------------

def test_edit_draft_rejects_published(pg_conn):
    """Once a revision is published, edit_draft refuses to mutate it."""
    _reset(pg_conn)
    rev = create_draft(pg_conn)
    spec_store.update_draft(pg_conn, rev, _empty_spec())
    spec_store.publish_draft(pg_conn, rev)

    with pytest.raises(DraftAlreadyPublishedError):
        with edit_draft(pg_conn, rev) as _spec:
            pass
