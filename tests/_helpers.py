"""Test-only helpers.

Wraps the create_draft + update_draft + publish_draft trio that ~15 test
files repeated verbatim. Tests still reach into knot.db.spec_store when
they need raw lifecycle access (concurrency / rollback / lifecycle tests
themselves); everywhere else, this is the fast path.
"""

from __future__ import annotations

import psycopg

from knot.db.spec_store import create_draft, publish_draft, update_draft
from knot.spec import Spec


async def publish_spec(
    conn: psycopg.AsyncConnection,
    spec: Spec,
    *,
    parent_revision: int | None = None,
    allow_destructive: bool = False,
) -> int:
    """Create a draft, bulk-write `spec`, publish, return the revision."""
    rev = await create_draft(conn, parent_revision=parent_revision)
    await update_draft(conn, rev, spec)
    await publish_draft(conn, rev, allow_destructive=allow_destructive)
    return rev
