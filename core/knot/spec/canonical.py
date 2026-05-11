"""Canonical serialization + content hashing for the spec graph.

Per `spec-versioning.md`:
  - Walk the live Pydantic object graph
  - Strip RUNTIME fields (description on display-only entities; Spec
    envelope authoring metadata)
  - Recursively strip defaults (empty containers, explicit defaults)
  - Serialize via RFC 8785 JCS (jcs library) — Pydantic-independent
    intermediate so Pydantic minor bumps don't shift bytes
  - sha256 the canonical bytes for `compute_content_hash`

Cycle handling (CANONICAL_DUMP_VERSION = 2): named SpecBase nodes (those
with a `name: str` field) are tracked by object id.  First visit emits the
full canonical form; subsequent visits emit `{"$ref": "<name>"}`.  This
breaks cycles from cross-class `slot.range` references without losing
identity.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import jcs
from pydantic import BaseModel
from pydantic_core import PydanticUndefined

from knot.spec.metaschema import Primitive

CANONICAL_DUMP_VERSION: int = 3


# ---------------------------------------------------------------------------
# RUNTIME fields — excluded from the canonical hash.
# Pulled from `spec-versioning.md` § "RUNTIME vs CANONICAL field taxonomy".
# ---------------------------------------------------------------------------

_RUNTIME_FIELDS: frozenset[tuple[str, str]] = frozenset(
    {
        # description is RUNTIME on these display-bearing classes
        ("OntologyClass", "description"),
        ("Slot", "description"),
        ("Constraint", "description"),
        ("Source", "description"),
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


class _Sentinel:
    """Marker for "no field default available"."""

    __slots__ = ()


_NO_DEFAULT = _Sentinel()


def _field_default(field_info: Any) -> Any:
    """Return the field's default, or `_NO_DEFAULT` if it has none.

    `default_factory` is invoked once to materialize the value; if the call
    raises (rare — empty list/dict factories don't raise) we fall back to
    `_NO_DEFAULT` rather than guess.
    """
    if field_info.default is not PydanticUndefined:
        return field_info.default
    if field_info.default_factory is not None:
        try:
            return field_info.default_factory()
        except Exception:
            return _NO_DEFAULT
    return _NO_DEFAULT


def _is_empty_container(value: Any) -> bool:
    return isinstance(value, (list, dict, set, frozenset)) and len(value) == 0


def _node_name(obj: BaseModel) -> str | None:
    """Return `obj.name` if it's a non-empty string, else None.

    Named SpecBase nodes participate in cycle de-dup; unnamed nodes
    (`Compare`, `BoolExpr`, expression-tree nodes) inline every visit.

    `Primitive` is excluded: it is a pure value type (no cycles possible),
    and different Python objects with the same name should produce identical
    canonical output regardless of object identity.
    """
    if isinstance(obj, Primitive):
        return None
    try:
        v = getattr(obj, "name", None)
        if isinstance(v, str) and v:
            return v
    except Exception:
        pass
    return None


def _serialize_node(obj: Any, visited: dict[int, str]) -> Any:
    """Recursively serialize a live Pydantic object graph to plain Python.

    Named SpecBase instances are tracked by object id:
      - first visit → full dict
      - subsequent visits → `{"$ref": "<name>"}`
    """
    if isinstance(obj, BaseModel):
        oid = id(obj)
        name = _node_name(obj)
        if name is not None:
            if oid in visited:
                return {"$ref": visited[oid]}
            visited[oid] = name

        class_name = type(obj).__name__
        result: dict[str, Any] = {}
        for field_name in type(obj).model_fields:
            if (class_name, field_name) in _RUNTIME_FIELDS:
                continue
            value = getattr(obj, field_name)
            result[field_name] = _serialize_node(value, visited)
        return result

    if isinstance(obj, list):
        return [_serialize_node(item, visited) for item in obj]

    if isinstance(obj, (set, frozenset)):
        return sorted(_serialize_node(item, visited) for item in obj)

    if isinstance(obj, dict):
        return {k: _serialize_node(v, visited) for k, v in obj.items()}

    # Enum-like with a `.value` attr → emit the value (matches use_enum_values=True
    # behaviour at the persistence boundary).
    val = getattr(obj, "value", None)
    if val is not None and type(obj).__name__ != "type" and hasattr(obj, "name"):
        # Looks like an enum; only treat str-valued enums explicitly.
        if isinstance(val, (str, int, float, bool)):
            return val

    return obj


def _strip_defaults(data: Any, model_class: type[BaseModel] | None) -> Any:
    """Recursively strip default values + empty containers.

    `model_class` is the Pydantic class whose `model_fields` we consult to
    decide whether a value matches the default.  For nested dicts produced
    by `_serialize_node` we propagate the child class when the field's
    annotation resolves to a `BaseModel` subclass.
    """
    if isinstance(data, dict):
        out: dict[str, Any] = {}
        for key, value in data.items():
            child_class: type[BaseModel] | None = None
            if model_class is not None:
                fi = model_class.model_fields.get(key)
                if fi is not None and isinstance(value, dict):
                    ann = fi.annotation
                    if isinstance(ann, type) and issubclass(ann, BaseModel):
                        child_class = ann
            stripped = _strip_defaults(value, child_class)

            if _is_empty_container(stripped):
                continue

            if model_class is not None:
                fi = model_class.model_fields.get(key)
                if fi is not None:
                    default = _field_default(fi)
                    if default is not _NO_DEFAULT and stripped == default:
                        continue

            out[key] = stripped
        return out

    if isinstance(data, list):
        return [_strip_defaults(item, None) for item in data]

    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def canonical_dump(spec: BaseModel) -> bytes:
    """Pydantic model → strip RUNTIME → strip defaults → JCS bytes.

    Returns RFC 8785-compliant UTF-8 bytes.  The intermediate is plain
    Python; the canonical form is invariant under Pydantic minor versions.
    """
    visited: dict[int, str] = {}
    intermediate = _serialize_node(spec, visited)
    cleaned = _strip_defaults(intermediate, type(spec))
    return jcs.canonicalize(cleaned)


def compute_content_hash(spec: BaseModel) -> str:
    """Return `sha256(canonical_dump(spec)).hexdigest()` — bare 64-char hex."""
    return sha256(canonical_dump(spec)).hexdigest()
