"""Canonical serialization and content hashing for knot Pydantic specs.

Algorithm per design/staging/spec-versioning.md:
  1. Walk the live Pydantic object graph with cycle detection
  2. Strip RUNTIME fields by (class_name, field_name)
  3. Recursively strip defaults (empty containers + explicit-default values)
  4. JCS encode (RFC 8785) via the `jcs` library
  5. sha256 the bytes for compute_content_hash

The intermediate is plain Python scalars/dicts/lists — Pydantic-independent —
so Pydantic version bumps do not shift the bytes.

Cycle handling (v2): named SpecBase nodes (OntologyClass, Slot, TypeDefinition,
Source, etc. — any SpecBase subclass with a `name` field) are tracked by
object id. The first visit emits the full canonical form; subsequent visits
emit {"$ref": "<name>"} to break cycles without losing identity. This matches
the persistence-boundary commitment: "real Python object references in-memory;
references flatten to names for JSONB storage."
"""

from __future__ import annotations

from hashlib import sha256 as _sha256
from typing import Any

import jcs
from pydantic import BaseModel

CANONICAL_DUMP_VERSION: int = 2

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


def _get_node_name(obj: BaseModel) -> str | None:
    """Return the .name field of a SpecBase-style node, or None if absent."""
    try:
        v = object.__getattribute__(obj, "__dict__").get("name") or getattr(obj, "name", None)
        if isinstance(v, str):
            return v
    except Exception:
        pass
    return None


def _serialize_node(
    obj: Any,
    visited: dict[int, str],
) -> Any:
    """Recursively serialize a live Pydantic object graph to plain Python.

    Named SpecBase instances (those with a `name: str` field) are tracked by
    object id. First visit → full dict. Subsequent visits → {"$ref": "<name>"}.
    This breaks cycles introduced by slot.range and cross-class references.
    """
    if isinstance(obj, BaseModel):
        oid = id(obj)
        node_name = _get_node_name(obj)

        if node_name is not None:
            # Named node — cycle / de-dup guard.
            if oid in visited:
                return {"$ref": visited[oid]}
            visited[oid] = node_name

        class_name = type(obj).__name__
        result: dict[str, Any] = {}
        for field_name, field_info in type(obj).model_fields.items():
            if (class_name, field_name) in _RUNTIME_FIELDS:
                continue
            value = getattr(obj, field_name)
            result[field_name] = _serialize_node(value, visited)
        return result

    if isinstance(obj, list):
        return [_serialize_node(item, visited) for item in obj]

    if isinstance(obj, (set, frozenset)):
        return sorted(_serialize_node(item, visited) for item in obj)

    # Scalars — pass through as-is.
    return obj


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def canonical_dump(spec: BaseModel) -> bytes:
    """Pydantic model → strip RUNTIME fields → strip defaults → JCS bytes.

    Returns RFC 8785-compliant UTF-8 bytes. The intermediate is
    Pydantic-independent; Pydantic minor-version bumps do not shift the bytes.

    Cycle handling: named SpecBase nodes (OntologyClass, Slot, TypeDefinition,
    Source, etc.) that are visited more than once emit {"$ref": "<name>"} on
    subsequent visits, breaking cycles from cross-class slot.range references.
    """
    visited: dict[int, str] = {}
    intermediate = _serialize_node(spec, visited)
    cleaned = _strip_defaults(intermediate, type(spec))
    return jcs.canonicalize(cleaned)


def compute_content_hash(spec: BaseModel) -> str:
    """Return sha256(canonical_dump(spec)).hexdigest() — bare 64-char hex."""
    return _sha256(canonical_dump(spec)).hexdigest()
