"""Per-Source Pydantic row models built from the typed spec.

The ingest endpoint (``POST /graph/ingest/{source_name}``) needs the same
validation rigor a statically-typed FastAPI app would get. Because the row
shape depends on which Source we're ingesting for, and the Source comes
from the currently-published spec, the row model has to be built at
request time.

This module is pure (no SQL, no postgres). Given a Source, returns a
``BaseModel`` subclass whose fields mirror the source class's *stored*
slots, with types and constraints derived from each Slot's TypeExpression.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from knot.spec import OntologyClass, Property, SourceBinding
from knot.spec.metaschema import Array, ClassRef, Primitive

_PY_TYPE_FOR_PRIMITIVE: dict[str, type] = {
    "string": str,
    "integer": int,
    "float": float,
    "boolean": bool,
    "datetime": datetime,
    "date": date,
}


def _type_expr_python(type_expr: Any) -> type:
    """Recursively map a TypeExpression to a Python type."""
    if isinstance(type_expr, Primitive):
        return _PY_TYPE_FOR_PRIMITIVE.get(type_expr.name, str)
    if isinstance(type_expr, ClassRef):
        return str  # canonical_id reference, stored as text
    if isinstance(type_expr, Array):
        inner = _type_expr_python(type_expr.of)
        return list[inner]  # type: ignore[return-value]
    return str


def _property_python_type(prop: Slot) -> Any:
    """Return the Python type for a prop, accounting for permissible_values."""
    # permissible_values constraint → Literal enum
    if (
        prop.constraints is not None
        and prop.constraints.permissible_values is not None
        and len(prop.constraints.permissible_values) > 0
    ):
        return Literal.__class_getitem__(tuple(prop.constraints.permissible_values))
    if prop.type is None:
        return str
    return _type_expr_python(prop.type)


def _is_stored(prop: Slot) -> bool:
    return getattr(prop, "derivation", None) is None


def _field_spec(prop: Slot, *, force_optional: bool = False) -> tuple[Any, Any]:
    """Compute the (py_type, Field(...)) tuple for one prop.

    Shared between ``build_row_model`` (full row at ingest) and
    ``build_value_model_for_property`` (single value at correction time).
    """
    py_type = _property_python_type(prop)
    # Array wrapping is already encoded in the TypeExpression; _type_expr_python
    # handles it. No secondary list[] wrapping needed.

    kwargs: dict[str, Any] = {}
    if prop.constraints is not None:
        if prop.constraints.pattern is not None:
            kwargs["pattern"] = prop.constraints.pattern
        if prop.constraints.min_value is not None:
            kwargs["ge"] = prop.constraints.min_value
        if prop.constraints.max_value is not None:
            kwargs["le"] = prop.constraints.max_value

    if not force_optional and (prop.required or prop.identifier):
        default: Any = ...
    else:
        default = None
        py_type = py_type | None

    return py_type, Field(default, **kwargs)


def build_row_model(binding: SourceBinding) -> type[BaseModel]:
    """Strict Pydantic model whose fields mirror the binding's class slots.

    - Field type comes from ``prop.type`` (TypeExpression).
    - Array slots have list[T] type (encoded in TypeExpression).
    - Required (or identifier) slots have no default; others default to None.
    - ``pattern``, ``min_value``, ``max_value`` from PropertyConstraints map to
      Pydantic ``Field(pattern=, ge=, le=)`` constraints.
    - ``extra="forbid"`` so unknown keys raise.

    Uses ``effective_properties`` so mixin-contributed slots are accepted
    (they live on the class's own table per the storage contract).

    Slot names are taken from the binding's class (after field-mapping by
    ``_apply_mappings`` in the ingest layer, so the row dict is already
    keyed by slot names by the time Pydantic validates it).
    """
    from knot.spec import effective_properties

    cls = binding.class_
    fields: dict[str, Any] = {}
    for prop in effective_properties(cls):
        if not _is_stored(prop):
            continue
        fields[prop.name] = _field_spec(prop)

    return create_model(
        f"{cls.name}IngestRow",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def build_row_model_for_class(cls: OntologyClass) -> type[BaseModel]:
    """Strict Pydantic model for a class whose fields are all stored properties,
    all optional (suitable for synthetic / user-correction rows where only
    a subset of slots may be supplied). Unlike ``build_row_model``, this is
    not tied to a specific Source and does not require identifier slots.

    Uses ``effective_properties`` so mixin-contributed slots are accepted (they
    live on the class's own table per the storage contract)."""
    from knot.spec import effective_properties

    fields: dict[str, Any] = {}
    for prop in effective_properties(cls):
        if not _is_stored(prop):
            continue
        fields[prop.name] = _field_spec(prop, force_optional=True)

    return create_model(
        f"{cls.name}SyntheticRow",
        __config__=ConfigDict(extra="forbid"),
        **fields,
    )


def build_value_model_for_property(prop: Slot) -> type[BaseModel]:
    """Single-field Pydantic model for one prop, used to validate a
    PropertyCorrection's ``value`` against the same constraints ingest
    enforces (type, pattern, min/max, Literal-from-permissible-values,
    array-shape). The lone field is required; the constraint
    set is reused via ``_field_spec``."""
    py_type, field_info = _field_spec(prop, force_optional=False)
    return create_model(
        f"{prop.name}Value",
        __config__=ConfigDict(extra="forbid"),
        **{prop.name: (py_type, field_info)},
    )
