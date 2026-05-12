"""knot FastAPI app — mounts spec (modelling) and graph (data) routers.

Startup sequence (via lifespan):
  1. configure_logging()  — sets up structured JSON/text logging.
  2. db.apply_schema()    — idempotent DDL; safe to run every boot.
  3. bootstrap_admin_from_env() — seeds admin user if none exist.

Environment variables
---------------------
KNOT_LOG_FORMAT   json | text   (default: text in dev, json otherwise)
KNOT_LOG_LEVEL    INFO | DEBUG | WARNING | ...  (default: INFO)
KNOT_DEV_MODE     1             enables text logging + dev DSN fallback

Metrics
-------
stdlib has no Prometheus exposition support.  When you're ready to add it,
the standard approach is:

    pip install prometheus-fastapi-instrumentator
    from prometheus_fastapi_instrumentator import Instrumentator
    Instrumentator().instrument(app).expose(app)

Until then, /metrics is not exposed.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from knot import __version__ as _VERSION
from knot import db
from knot.api import dq as dq_router_mod
from knot.api import graph as graph_router_mod
from knot.api import lake as lake_router_mod
from knot.api import spec as spec_router_mod
from knot.api.auth import auth as auth_router_mod
from knot.api.auth.security import bootstrap_admin_from_env
from knot.api.middleware import RequestIDMiddleware
from knot.logging_config import configure_logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # — startup —
    configure_logging()
    logger.info("knot starting up", extra={"version": _VERSION})
    await db.apply_schema()
    await bootstrap_admin_from_env()
    logger.info("knot ready")
    yield
    # — shutdown —
    logger.info("knot shutting down")


app = FastAPI(
    title="knot",
    description="Knowledge graph + ontology platform — spec (modelling) + graph (data).",
    lifespan=lifespan,
)

app.add_middleware(RequestIDMiddleware)

app.include_router(auth_router_mod.router)
app.include_router(spec_router_mod.router)
app.include_router(graph_router_mod.router)
app.include_router(dq_router_mod.router)
app.include_router(lake_router_mod.router)


# ── /healthz — liveness probe (no auth, no router prefix) ─────────────────────


@app.get("/healthz", include_in_schema=False)
def healthz() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "knot", "version": _VERSION})
