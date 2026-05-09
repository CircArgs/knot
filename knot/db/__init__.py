"""Data plane — runtime persistence (hand-written SQL).

What's inside:
  - ``connect()`` / ``apply_schema()`` (this file)   — connection lifecycle
  - ``control_schema.sql``                           — control-plane DDL
  - ``spec_store``                                   — spec_revisions CRUD + drafts
  - ``graph_store``                                  — per-class table INSERT/SELECT
                                                       + user-correction row upsert
  - ``corrections``                                  — _user_corrections audit-log CRUD
  - ``trust_config``                                 — per-source scalar trust CRUD
  - ``trust_posteriors``                             — per-(source, slot) Beta posterior CRUD

Spec → SQL compilation (DDL emission, predicate compilation, lake SELECTs,
GraphQL schema generation) lives under ``knot.spec.compile`` — not here.

DSN is read from ``knot.config.config.get_dsn``; no DSN parameters thread
through the rest of the codebase.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg

from knot.config.config import get_dsn
from knot.db import (
    corrections,
    graph_store,
    spec_store,
    trust_config,
    trust_posteriors,
)

__all__ = [
    "connect",
    "apply_schema",
    "spec_store",
    "graph_store",
    "corrections",
    "trust_config",
    "trust_posteriors",
]


_CONTROL_SCHEMA_SQL = (Path(__file__).parent / "control_schema.sql").read_text()


@asynccontextmanager
async def connect(*, autocommit: bool = True) -> AsyncIterator[psycopg.AsyncConnection]:
    """Open a fresh async postgres connection at the configured DSN."""
    async with await psycopg.AsyncConnection.connect(get_dsn(), autocommit=autocommit) as conn:
        yield conn


async def apply_schema() -> None:
    """Apply the control-plane schema (``control_schema.sql``). Idempotent."""
    async with connect() as conn:
        await conn.execute(_CONTROL_SCHEMA_SQL)
