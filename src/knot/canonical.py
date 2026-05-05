"""Canonical serialization and content hashing for knot Pydantic specs.

Algorithm per design/staging/spec-versioning.md:
  1. model_dump(mode="python", exclude_unset=False) → full dict
  2. Strip RUNTIME fields by (class_name, field_name)
  3. Recursively strip defaults (empty containers + explicit-default values)
  4. JCS encode (RFC 8785) via the `jcs` library
  5. sha256 the bytes for compute_content_hash

The intermediate is plain Python scalars/dicts/lists — Pydantic-independent —
so Pydantic version bumps do not shift the bytes.
"""

from __future__ import annotations

from hashlib import sha256 as _sha256
from typing import Any

import jcs
from pydantic import BaseModel

CANONICAL_DUMP_VERSION: int = 1

# ---------------------------------------------------------------------------
# RUNTIME field taxonomy — excluded from the canonical hash.
# Typed as frozenset[tuple[class_name, field_name]].
# Source: design/staging/spec-versioning.md § "RUNTIME vs CANONICAL field taxonomy"
# ---------------------------------------------------------------------------

_RUNTIME_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        # description is RUNTIME on these six ontology-object classes
        ("OntologyClass", "description"),
        ("Slot", "description"),
        ("SlotOverride", "description"),
        ("PermissibleValue", "description"),
        ("TypeDefinition", "description"),
        ("Constraint", "description"),
        # Spec envelope authoring metadata
        ("Spec", "created_at"),
        ("Spec", "last_modified"),
        ("Spec", "author"),
        ("Spec", "revision_id"),
        ("Spec", "display_label"),
    }
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _is_empty_container(value: Any) -> bool:
    """True for [], {}, set() — empty containers that are default-equivalent."""
    return isinstance(value, (list, dict, set, frozenset)) and len(value) == 0


def _strip_defaults(data: Any, model_class: type[BaseModel] | None) -> Any:
    """Recursively strip default values and empty containers from *data*.

    *model_class* is the Pydantic class whose model_fields we consult for
    defaults at this level. For nested objects it is carried through; for
    plain dicts/lists (no associated model class) only empty-container stripping
    applies.
    """
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            # Recurse first so inner objects are stripped before we decide
            # whether the outer field is default.
            child_class: type[BaseModel] | None = None
            if model_class is not None:
                field_info = model_class.model_fields.get(key)
                if field_info is not None and isinstance(value, dict):
                    # Try to resolve the annotation to a BaseModel subclass
                    # so we can strip its defaults too.
                    ann = field_info.annotation
                    if isinstance(ann, type) and issubclass(ann, BaseModel):
                        child_class = ann

            stripped = _strip_defaults(value, child_class)

            # Drop if empty container
            if _is_empty_container(stripped):
                continue

            # Drop if matches explicit default from model_fields
            if model_class is not None:
                field_info = model_class.model_fields.get(key)
                if field_info is not None:
                    default = _field_default(field_info)
                    if default is not _SENTINEL and stripped == default:
                        continue

            out[key] = stripped
        return out

    if isinstance(data, list):
        return [_strip_defaults(item, None) for item in data]

    return data


class _Sentinel:
    """Unique object meaning "no default available"."""
    __slots__ = ()


_SENTINEL = _Sentinel()


def _field_default(field_info: Any) -> Any:
    """Return the default value for a field, or _SENTINEL if none."""
    from pydantic_core import PydanticUndefined  # type: ignore[import]

    if field_info.default is not PydanticUndefined:
        return field_info.default
    # default_factory: call it to get the default value
    if field_info.default_factory is not None:
        try:
            return field_info.default_factory()
        except Exception:
            return _SENTINEL
    return _SENTINEL


def _strip_runtime(
    data: dict[str, Any],
    class_name: str,
) -> dict[str, Any]:
    """Remove RUNTIME fields from *data* for the given *class_name*.

    Only operates at the top-level dict for that class; recursion into nested
    objects is handled by _build_intermediate.
    """
    return {
        k: v
        for k, v in data.items()
        if (class_name, k) not in _RUNTIME_FIELDS
    }


def _build_intermediate(obj: Any, model_class: type[BaseModel] | None) -> Any:
    """Recursively convert a model_dump output into a Pydantic-independent
    intermediate of plain Python scalars, dicts, and lists.

    This is also where RUNTIME-field stripping happens: whenever we encounter
    a dict that came from a known BaseModel subclass, we strip its RUNTIME fields
    before recursing into children.
    """
    if isinstance(obj, dict):
        # Strip RUNTIME fields for known class
        class_name = model_class.__name__ if model_class is not None else ""
        if class_name:
            obj = _strip_runtime(obj, class_name)

        result: dict[str, Any] = {}
        for key, value in obj.items():
            child_class: type[BaseModel] | None = None
            if model_class is not None:
                field_info = model_class.model_fields.get(key)
                if field_info is not None:
                    ann = field_info.annotation
                    # Unwrap Optional[X] → X
                    ann = _unwrap_optional(ann)
                    if isinstance(ann, type) and issubclass(ann, BaseModel):
                        child_class = ann
            result[key] = _build_intermediate(value, child_class)
        return result

    if isinstance(obj, list):
        return [_build_intermediate(item, None) for item in obj]

    if isinstance(obj, set | frozenset):
        return sorted(_build_intermediate(item, None) for item in obj)

    # Scalars: str, int, float, bool, None pass through.
    # Non-JSON-serialisable types (date, datetime, Decimal, enum, …) are
    # converted to their string or numeric representation by model_dump
    # mode="python" — they arrive here already as native Python types.
    return obj


def _unwrap_optional(ann: Any) -> Any:
    """Unwrap Optional[X] (= Union[X, None]) to X; return ann unchanged otherwise."""
    import types as _types
    import typing

    origin = getattr(ann, "__origin__", None)
    # Handle `X | None` (Python 3.10+ union types)
    if isinstance(ann, _types.UnionType):
        args = [a for a in ann.__args__ if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return ann
    # Handle typing.Optional / typing.Union
    if origin is typing.Union:
        args = [a for a in ann.__args__ if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return ann


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def canonical_dump(spec: BaseModel) -> bytes:
    """Pydantic model → strip RUNTIME fields → strip defaults → JCS bytes.

    Returns RFC 8785-compliant UTF-8 bytes. The intermediate is
    Pydantic-independent; Pydantic minor-version bumps do not shift the bytes.
    """
    raw: dict[str, Any] = spec.model_dump(mode="python", exclude_unset=False)
    intermediate = _build_intermediate(raw, type(spec))
    cleaned = _strip_defaults(intermediate, type(spec))
    return jcs.canonicalize(cleaned)


def compute_content_hash(spec: BaseModel) -> str:
    """Return sha256(canonical_dump(spec)).hexdigest() — bare 64-char hex."""
    return _sha256(canonical_dump(spec)).hexdigest()
