"""knot FastAPI app — mounts spec (modelling) and graph (data) routers."""

from __future__ import annotations

from fastapi import FastAPI

from knot.api import graph as graph_router_mod
from knot.api import spec as spec_router_mod
from knot import db


db.apply_schema()

app = FastAPI(
    title="knot",
    description="Knowledge graph + ontology platform — spec (modelling) + graph (data).",
)

app.include_router(spec_router_mod.router)
app.include_router(graph_router_mod.router)
