"""Typed event models emitted by the graph layer.

Events describe what's happening from the SOURCE side of the request
(rows arriving, etc.). Handlers self-select on event type via isinstance
and decide what to do. No event privileges any one handler.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from knot.spec import Source, Spec


class Event(BaseModel):
    """Base for all events. Subclasses describe specific source-side facts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RowEvent(Event):
    """A batch of typed rows from one source."""

    source: Source
    spec: Spec
    rows: list[BaseModel]  # typed via build_row_model(source); mutable for handlers


class RowsIngesting(RowEvent):
    """Pre-insert. Handlers can mutate rows in place (normalize, drop, augment)
    and populate canonical_ids. If no handler sets canonical_ids, the
    built-in identifier-slot passthrough in graph.ingest_rows is used.
    Fires inside any active transaction."""

    canonical_ids: list[str] | None = None


class RowsIngested(RowEvent):
    """Post-insert. Read-only; for side-effect handlers (audit, trust
    feedback, push-to-Kafka, etc.). Fires inside the route's transaction
    when one is active so side-effects roll back consistently with the
    INSERT."""

    inserted_count: int
    canonical_ids: list[str]
