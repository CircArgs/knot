"""Standalone FastAPI app exposing Maestro-mimic endpoints at /maestro/*.

Run with:
    uvicorn knot.orchestrator.toy_maestro.app:app --host 0.0.0.0 --port 8001

API surface (mirrors Maestro's /api/v3/workflows paths):

  GET  /maestro/health
    → {"status": "ok"}

  POST /maestro/api/v3/workflows
    Body: Workflow JSON (id, steps, params, ...)
    → {"workflow_id": "<id>"}

  GET  /maestro/api/v3/workflows/{workflow_id}
    → Workflow definition JSON

  POST /maestro/api/v3/workflows/{workflow_id}/instances
    Body: {"run_params": {...}}   (optional)
    → WorkflowStartResponse JSON: {workflow_id, workflow_instance_id,
                                   workflow_run_id, workflow_uuid, status}

  GET  /maestro/api/v3/workflows/{workflow_id}/instances/{run_id}
    → RunInstance JSON

  GET  /maestro/api/v3/workflows/{workflow_id}/instances/{run_id}/steps
    → list of step run rows

Environment:
  KNOT_CONTROL_DSN — Postgres DSN (default: postgresql://knot:knot@localhost:5432/knot_control)
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from knot.orchestrator.toy_maestro.dispatcher import (
    ToyMaestro,
    WorkflowNotFound,
    RunNotFound,
)
from knot.orchestrator.toy_maestro import storage
from knot.orchestrator.toy_maestro.workflow_models import Workflow

# ---------------------------------------------------------------------------
# App + dispatcher
# ---------------------------------------------------------------------------

app = FastAPI(title="toy-maestro", version="0.1.0")

_dsn: str | None = os.environ.get("KNOT_CONTROL_DSN")
_dispatcher = ToyMaestro(dsn=_dsn)


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    run_params: dict[str, Any] = {}


class WorkflowRegisterResponse(BaseModel):
    workflow_id: str


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get("/maestro/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Workflow registration
# ---------------------------------------------------------------------------


@app.post("/maestro/api/v3/workflows", status_code=201)
def register_workflow(workflow: Workflow) -> WorkflowRegisterResponse:
    """Register (or update) a workflow definition."""
    wf_id = _dispatcher.register_workflow(workflow)
    return WorkflowRegisterResponse(workflow_id=wf_id)


@app.get("/maestro/api/v3/workflows/{workflow_id}")
def get_workflow(workflow_id: str) -> dict[str, Any]:
    """Fetch a registered workflow definition."""
    wf = storage.load_workflow(workflow_id, _dsn)
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")
    return wf.model_dump()


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


@app.post("/maestro/api/v3/workflows/{workflow_id}/instances", status_code=201)
def start_run(workflow_id: str, body: StartRunRequest | None = None) -> dict[str, Any]:
    """Start a workflow run.  Executes synchronously before returning.

    Response mirrors Maestro's WorkflowStartResponse shape.
    """
    run_params = body.run_params if body else {}
    try:
        run_id = _dispatcher.start_run(workflow_id, run_params)
    except WorkflowNotFound:
        raise HTTPException(status_code=404, detail=f"Workflow '{workflow_id}' not found")

    instance = _dispatcher.get_run(run_id)
    return {
        "workflow_id": instance.workflow_id,
        "workflow_instance_id": instance.workflow_instance_id,
        "workflow_run_id": instance.workflow_run_id,
        "workflow_uuid": instance.workflow_uuid,
        "run_id": run_id,
        "status": instance.status.value,
    }


@app.get("/maestro/api/v3/workflows/{workflow_id}/instances/{run_id}")
def get_run(workflow_id: str, run_id: str) -> dict[str, Any]:
    """Fetch a run instance."""
    try:
        instance = _dispatcher.get_run(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if instance.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found under workflow '{workflow_id}'")
    return instance.model_dump()


@app.get("/maestro/api/v3/workflows/{workflow_id}/instances/{run_id}/steps")
def get_step_runs(workflow_id: str, run_id: str) -> list[dict[str, Any]]:
    """Fetch per-step status rows for a run."""
    try:
        instance = _dispatcher.get_run(run_id)
    except RunNotFound:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    if instance.workflow_id != workflow_id:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return storage.load_step_runs(run_id, _dsn)
