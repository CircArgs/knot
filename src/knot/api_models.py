"""Pydantic request/response models for knot's FastAPI surface.

Separate from api.py to keep endpoint logic readable.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# POST /impls/{name}/revisions
# ---------------------------------------------------------------------------

class ImplRevisionRequest(BaseModel):
    source_bytes: str  # Python source as a string


class ImplRevisionResponse(BaseModel):
    revision: int
    content_hash: str
    pinned_spec_hash: str


# ---------------------------------------------------------------------------
# GET /impls/{name}/revisions
# ---------------------------------------------------------------------------

class ImplRevisionRow(BaseModel):
    name: str
    revision: int
    content_hash: str
    pinned_spec_hash: str
    created_at: str


# ---------------------------------------------------------------------------
# POST /configs/{impl_name}/revisions
# ---------------------------------------------------------------------------

class ConfigRevisionRequest(BaseModel):
    config: dict[str, Any]


class ConfigRevisionResponse(BaseModel):
    revision: int
    content_hash: str


# ---------------------------------------------------------------------------
# PUT /bound_impls/{stage}/{class_name}
# ---------------------------------------------------------------------------

class BoundImplRequest(BaseModel):
    impl_name: str
    revision: int
    config_revision: int | None = None


class BoundImplResponse(BaseModel):
    stage: str
    class_name: str
    impl_name: str
    current_revision: int
    current_config_revision: int | None
    updated_at: str


# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    scope: str  # "Movie" | "full" | "stage:resolve:Movie"
    parents_override: dict[str, str] | None = None
    runtime_image_identity: str | None = None


class RunResponse(BaseModel):
    run_id: int
    compile_hash: str
    workflow_spec: dict[str, Any]


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------

class RunStatusResponse(BaseModel):
    run_id: int
    compile_hash: str
    scope: str
    status: str
    started_at: str
    completed_at: str | None
    error: str | None
    workflow_spec: dict[str, Any]


# ---------------------------------------------------------------------------
# GET /compiled_workflows/{hash}
# ---------------------------------------------------------------------------

class CompiledWorkflowResponse(BaseModel):
    hash: str
    spec: dict[str, Any]
    created_at: str


# ---------------------------------------------------------------------------
# GET /compiled_workflows
# ---------------------------------------------------------------------------

class CompiledWorkflowListItem(BaseModel):
    hash: str
    canonicalizer_version: int
    created_at: str
    run_count: int
    stage_count: int


# ---------------------------------------------------------------------------
# GET /bound_impls
# ---------------------------------------------------------------------------

class BoundImplListItem(BaseModel):
    stage: str
    class_name: str
    impl_name: str
    current_revision: int
    current_config_revision: int | None
    updated_at: str


# ---------------------------------------------------------------------------
# POST /corrections
# ---------------------------------------------------------------------------

class CorrectionRequest(BaseModel):
    class_name: str
    canonical_id: str
    slot: str
    value: Any


class CorrectionResponse(BaseModel):
    id: int
    class_name: str
    canonical_id: str
    slot_name: str
    submitted_at: str


# ---------------------------------------------------------------------------
# POST /er_decisions
# ---------------------------------------------------------------------------

class ERDecisionRequest(BaseModel):
    class_name: str
    decision_type: str  # "force_merge" | "force_split"
    canonical_ids: list[str]
    reason: str | None = None


class ERDecisionResponse(BaseModel):
    id: int
    class_name: str
    decision_type: str
    canonical_ids: list[str]
    submitted_at: str


# ---------------------------------------------------------------------------
# GET /sources
# ---------------------------------------------------------------------------

class SourceListItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    entity_class: str
    identifier_slot: str
    description: str | None = None


# ---------------------------------------------------------------------------
# POST /sources
# ---------------------------------------------------------------------------

class SourceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    entity_class: str        # name of an existing OntologyClass on the spec
    identifier_slot: str     # name of an existing Slot on that class
    description: str | None = None


class SourceCreateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spec_revision: int
    spec_content_hash: str
    source: SourceListItem
