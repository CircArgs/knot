"""Per-Source Pydantic row models built from the typed spec.

The ingest endpoint (``POST /graph/ingest/{source_name}``) needs the same
validation rigor a statically-typed FastAPI app would get. Because the row
shape depends on which Source we're ingesting for, and the Source comes
from the currently-published spec, the row model has to be built at
request time.

This module is pure (no SQL, no postgres). Given a Source, returns a
``BaseModel`` subclass whose fields mirror the source class's *stored*
slots, with types and constraints derived from each Slot's metadata.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from knot.spec import OntologyClass, Slot, Source, TypeDefinition

_PY_TYPE_FOR_BASE: dict[str, type] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "bool": bool,
    "boolean": bool,
    "datetime": datetime,
    "date": date,
}


def _slot_python_type(slot: Slot) -> Any:
    if slot.permissible_values:
        # Literal[v1, v2, ...] from the permissible value texts.
        return Literal.__class_getitem__(tuple(pv.text for pv in slot.permissible_values))
    if isinstance(slot.range, OntologyClass):
        return str  # canonical_id reference, stored as text
    if isinstance(slot.range, TypeDefinition):
        return _PY_TYPE_FOR_BASE.get((slot.range.base or "str").lower(), str)
    return str


def _is_stored(slot: Slot) -> bool:
    return getattr(slot, "derivation", None) is None


def _field_spec(slot: Slot, *, force_optional: bool = False) -> tuple[Any, Any]:
    """Compute the (py_type, Field(...)) tuple for one slot.

    Shared between ``build_row_model`` (full row at ingest) and
    ``build_value_model_for_slot`` (single value at correction time).
    """
    py_type = _slot_python_type(slot)
    if slot.multivalued:
        py_type = list[py_type]

    kwargs: dict[str, Any] = {}
    if slot.pattern:
        kwargs["pattern"] = slot.pattern
    if slot.minimum_value is not None:
        kwargs["ge"] = slot.minimum_value
    if slot.maximum_value is not None:
        kwargs["le"] = slot.maximum_value

    if not force_optional and (slot.required or slot.identifier):
        default: Any = ...
    else:
        default = None
        py_type = py_type | None

    return py_type, Field(default, **kwargs)


def build_row_model(source: Source) -> type[BaseModel]:
    """Strict Pydantic model whose fields mirror the source's class slots.

    - Field type comes from ``slot.range`` (or ``permissible_values`` if set).
    - Multivalued slots become ``list[T]``.
    - Required (or identifier) slots have no default; others default to None.
    - ``pattern``, ``minimum_value``, ``maximum_value`` map to Pydantic
      ``Field(pattern=, ge=, le=)`` constraints.
    - ``extra="forbid"`` so unknown keys raise.
    """
    cls = source.entity_class
    fields: dict[str, Any] = {}
    for slot in cls.slots:
        if not _is_stored(slot):
            continue
        fields[slot.name] = _field_spec(slot)

    return create_model(
        f"{cls.name}IngestRow",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def build_row_model_for_class(cls: OntologyClass) -> type[BaseModel]:
    """Strict Pydantic model for a class whose fields are all stored slots,
    all optional (suitable for synthetic / user-correction rows where only
    a subset of slots may be supplied). Unlike ``build_row_model``, this is
    not tied to a specific Source and does not require identifier slots."""
    fields: dict[str, Any] = {}
    for slot in cls.slots:
        if not _is_stored(slot):
            continue
        fields[slot.name] = _field_spec(slot, force_optional=True)

    return create_model(
        f"{cls.name}SyntheticRow",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def build_value_model_for_slot(slot: Slot) -> type[BaseModel]:
    """Single-field Pydantic model for one slot, used to validate a
    PropertyCorrection's ``value`` against the same constraints ingest
    enforces (type, pattern, min/max, Literal-from-permissible-values,
    multivalued list-shape). The lone field is required; the constraint
    set is reused via ``_field_spec``."""
    py_type, field_info = _field_spec(slot, force_optional=False)
    return create_model(
        f"{slot.name}Value",
        __config__=ConfigDict(extra="forbid"),
        **{slot.name: (py_type, field_info)},
    )
