"""knot FastAPI app — mounts the spec router (modeling router lands later)."""

from __future__ import annotations

import os

from fastapi import FastAPI

from knot.api import spec as spec_router_mod
from knot.control_db import apply_schema
from knot import spec_store


DSN = os.environ.get(
    "KNOT_CONTROL_DSN",
    "postgresql://knot:knot@localhost:5432/knot_control",
)


def _bootstrap() -> None:
    """Apply schema and seed the B2 fixture if no spec is published yet."""
    apply_schema(DSN)
    import psycopg
    with psycopg.connect(DSN, autocommit=True) as conn:
        if spec_store.get_published(conn) is None:
            spec_store.seed_from_fixture(conn)


_bootstrap()

# Inject DSN before the router runs.
spec_router_mod.set_dsn(DSN)

app = FastAPI(
    title="knot",
    description="Knowledge graph + ontology compiler — spec-graph router scope.",
)

app.include_router(spec_router_mod.router)
