"""Shared connection + schema helpers for the demo notebooks.

Single source of truth for host/port/user/password/dbname — every
notebook in the arc uses the same throwaway schema and the same
postgres on the same port. Edit here, every notebook follows.

Module surface
--------------
``SCHEMA``           the fixed throwaway schema name every notebook
                     deploys + reads from (01 drops + recreates it;
                     02..05 chain through it)
``connect()``        returns ``(pg, engine)`` — psycopg connection
                     for write paths (named-placeholder ``cur.execute``)
                     and SQLAlchemy engine for pandas reads
                     (silences the SQLAlchemy-required warning)
``reset_atlas_dev()`` drops + recreates the dev database Atlas uses
                     to render the desired-state SQL; only 03_migrate
                     needs this
"""

from __future__ import annotations

import psycopg
from sqlalchemy import Engine, create_engine

# Edit these and every notebook follows.
HOST = "localhost"
PORT = 5433
USER = "knot"
PASSWORD = "knot"  # noqa: S105 — local dev fixture, not a secret
DBNAME = "knot"
SCHEMA = "knot_demo"

PG_URL = f"postgresql+psycopg://{USER}:{PASSWORD}@{HOST}:{PORT}/{DBNAME}"


def connect() -> tuple[psycopg.Connection, Engine]:
    """Open one psycopg connection + one SQLAlchemy engine pointing at
    the demo database. psycopg gets used for writes (named-placeholder
    ``cur.execute(sql, {"rows": …})``); engine gets used for pandas
    ``read_sql_query`` reads."""
    pg = psycopg.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        dbname=DBNAME,
        autocommit=True,
    )
    engine = create_engine(PG_URL)
    return pg, engine


def reset_atlas_dev() -> None:
    """Drop + recreate Atlas's dev database (``atlas_dev``). Atlas
    needs a clean throwaway DB to render the desired schema into;
    03_migrate calls this once at the top so each notebook run starts
    clean."""
    with psycopg.connect(
        host=HOST,
        port=PORT,
        user=USER,
        password=PASSWORD,
        dbname="postgres",
        autocommit=True,
    ) as admin:
        admin.execute("DROP DATABASE IF EXISTS atlas_dev")
        admin.execute("CREATE DATABASE atlas_dev")
