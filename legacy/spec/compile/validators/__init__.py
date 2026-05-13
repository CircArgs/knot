"""Pydantic row-model factories — pure spec → model emission, no I/O.

Lifted from the old ``core/knot/api/row_models.py`` so it lives next to
its sibling emitters under ``knot.spec.compile.*``. The factory functions
take a typed SourceBinding / OntologyClass / Slot and return a
``BaseModel`` subclass shaped to that spec entity — used by an ingest
runtime to validate row payloads before insertion.
"""

from knot.spec.compile.validators.row_models import (
    build_row_model,
    build_row_model_for_class,
    build_value_model_for_slot,
)

__all__ = (
    "build_row_model",
    "build_row_model_for_class",
    "build_value_model_for_slot",
)
