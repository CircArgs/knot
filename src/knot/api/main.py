"""knot FastAPI app — mounts spec (modelling) and graph (data) routers."""

from __future__ import annotations

from fastapi import FastAPI

from knot.api import auth as auth_router_mod
from knot.api import graph as graph_router_mod
from knot.api import spec as spec_router_mod
from knot import db
from knot.auth import bootstrap_admin_from_env


db.apply_schema()
bootstrap_admin_from_env()

app = FastAPI(
    title="knot",
    description="Knowledge graph + ontology platform — spec (modelling) + graph (data).",
)

app.include_router(auth_router_mod.router)
app.include_router(spec_router_mod.router)
app.include_router(graph_router_mod.router)
