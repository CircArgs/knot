"""Typed Pydantic shapes for the compiled WorkflowSpec.

Emitted by knot.compiler; dispatched to the external orchestrator.
Per core-design.md commitments 1, 3, 8 and staging/incremental-execution.md.
"""

from __future__ import annotations

from typing import Literal

from knot.metaschema import SpecBase


class StageSpec(SpecBase):
    """A single stage in a compiled workflow."""

    kind: Literal[
        "normalize",
        "resolve",
        "merge",
        "validate",
        "publish",
        "dq:normalize",
        "dq:resolve",
        "dq:merge",
        "dq:publish",
    ]
    class_name: str | None = None       # null for non-class-scoped stages (e.g. normalize)
    source_name: str | None = None      # for normalize stages: which Source this stage processes
    impl_name: str | None = None        # bound impl, if any
    impl_revision: int | None = None
    config_revision: int | None = None
    cache_key: str                      # sha256 of (stage inputs)
    cache_hit_artifact: str | None = None  # if cache hit, path to prior artifact
    pinned_parent_runs: dict[str, str] = {}   # for relation-class stages: class_name -> run_hash
    source_watermarks: dict[str, str] = {}    # for normalize stages: source_name -> watermark
    requires_classes: list[str] = []          # spec entities this stage compiles against


class WorkflowSpec(SpecBase):
    """Compiled workflow ready for orchestrator dispatch."""

    spec_revision_ids: dict[str, int] = {}   # spec entity name -> revision
    stages: list[StageSpec] = []              # toposorted
    runtime_image_identity: str | None = None  # opaque, populated by orchestrator at dispatch
