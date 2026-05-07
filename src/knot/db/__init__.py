"""Data plane — every SQL string and every postgres call lives under ``knot.db``.

What's inside:
  - ``connect()`` / ``apply_schema()`` (this file)   — connection lifecycle
  - ``control_schema.sql``                           — control-plane DDL
  - ``spec_store``                                   — spec_revisions CRUD
  - ``migration``                                    — spec → per-class table DDL
  - ``graph_store``                                  — per-class table INSERT/SELECT
  - ``trust_config``                                 — per-source scalar trust
  - ``trust_posteriors``                             — per-(source, slot) Beta posterior
  - ``resolve``                                      — trust-resolved record builder
                                                       (ARGMAX_TRUST / THOMPSON / UCB1)
  - ``sql_gen``                                      — expression tree → SQL strings

Centralization rule: only modules under ``knot/db/`` import ``psycopg`` and
only modules here contain SQL strings. Everything else imports ``db`` and
calls into named functions.

DSN is read from ``knot.config.get_dsn``; no DSN parameters thread through
the rest of the codebase.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from knot.config import get_dsn
from knot.db import (
    graph_store,
    migration,
    resolve,
    spec_store,
    sql_gen,
    trust_config,
    trust_posteriors,
)

__all__ = [
    "connect", "apply_schema",
    "spec_store", "migration", "graph_store",
    "trust_config", "trust_posteriors", "resolve", "sql_gen",
]


_CONTROL_SCHEMA_SQL = (Path(__file__).parent / "control_schema.sql").read_text()


def connect(*, autocommit: bool = True) -> psycopg.Connection:
    """Open a fresh postgres connection at the configured DSN."""
    return psycopg.connect(get_dsn(), autocommit=autocommit)


def apply_schema() -> None:
    """Apply the control-plane schema (``control_schema.sql``). Idempotent."""
    with connect() as conn:
        conn.execute(_CONTROL_SCHEMA_SQL)
