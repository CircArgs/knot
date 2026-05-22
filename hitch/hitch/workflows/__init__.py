"""hitch workflows + activities — Temporal layer.

Three workflows (Ingest, EmbeddingBackfill, ER) + a validation sweep.
Each shape is a templated rendition of the
``docs/temporal-adapter.md`` design from the knot tree.
"""

from hitch.workflows.embed import EmbedWorkflow
from hitch.workflows.er import ERWorkflow
from hitch.workflows.ingest import IngestWorkflow
from hitch.workflows.validation import ValidationSweepWorkflow

__all__ = [
    "EmbedWorkflow",
    "ERWorkflow",
    "IngestWorkflow",
    "ValidationSweepWorkflow",
]
