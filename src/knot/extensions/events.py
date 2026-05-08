"""Typed event models emitted by routes to the master dispatcher."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from knot.ontology import OntologyClass, Source


class IngestResolveCanonical(BaseModel):
    """Emitted by the ingest route after validation; extensions set canonical_ids."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    cls: OntologyClass
    source: Source
    incoming: list[dict[str, Any]]
    canonical_ids: list[str] | None = None
