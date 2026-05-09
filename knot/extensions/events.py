"""Typed event models emitted by the graph layer.

Events describe what's happening from the SOURCE side of the request
(rows arriving, etc.). Handlers self-select on event type via isinstance
and decide what to do. No event privileges any one handler.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from knot.spec import Source


class Event(BaseModel):
    """Base for all events. Subclasses describe specific source-side facts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class RowEvent(Event):
    """A batch of typed rows from one source."""

    source: Source
    rows: list[BaseModel]  # typed via build_row_model(source); mutable for handlers


class RowsIngesting(RowEvent):
    """Pre-insert. Handlers can mutate rows in place (normalize, drop, augment)
    and populate canonical_ids. Default ER handler fills canonical_ids if no
    other handler did. Fires inside any active transaction."""

    canonical_ids: list[str] | None = None


class RowsIngested(RowEvent):
    """Post-insert. Read-only; for side-effect handlers (DQ, audit, trust
    feedback, etc.). Fires inside the route's transaction when one is active
    so side-effects roll back consistently with the INSERT."""

    inserted_count: int
    canonical_ids: list[str]
