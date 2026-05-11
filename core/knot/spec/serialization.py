"""Full-fidelity Spec ↔ dict round-trip.

Distinct from `canonical.py`:
  - `canonical.py`     — strip-defaults + RUNTIME-fields-removed
                         serialization used for content hashing.
  - `serialization.py` — full-fidelity round-trip used for persistence
                         to `spec_revisions` and rehydration on read.

Cycle handling: named SpecBase nodes (Slot, OntologyClass, Source, Constraint)
are tracked by counter `$uid`; second visit emits
`{"$ref": <uid>, "$kind": "..."}` so two entities with the same name
(e.g. Movie.imdb_id vs Person.imdb_id) round-trip as distinct objects.

Two-pass rehydration: slots → classes → sources → constraints,
since each layer's cross-refs need the prior layer's entities indexed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from knot.spec.errors import PublishGateError
from knot.spec.metaschema import (
    Array,
    BoolExpr,
    ClassRef,
    Compare,
    Constraint,
    FilteredRelation,
    FormatDerivation,
    Literal_,
    Matches,
    OntologyClass,
    Primitive,
    RecursiveTraversal,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationFirst,
    RelationProject,
    RelationRef,
    ReverseRelation,
    ScalarDerivation,
    Slot,
    SlotConstraints,
    SlotPath,
    Source,
    Spec,
    Within,
)
from knot.spec.metaschema import Between as _Between

# ─── Serializer ─────────────────────────────────────────────────────────────

_NAMED_CLASSES = (Slot, OntologyClass, Source, Constraint)


def _is_named(obj: Any) -> bool:
    return isinstance(obj, _NAMED_CLASSES) and isinstance(getattr(obj, "name", None), str)


class _SerCtx:
    """Per-serialize state. Tracks named-entity object identity by counter
    UID so two entities with the same name (e.g. `Movie.imdb_id` vs
    `Person.imdb_id`) serialize / deserialize as distinct objects.
    """

    def __init__(self) -> None:
        self.id_to_uid: dict[int, int] = {}
        self.next_uid: int = 0

    def assign(self, oid: int) -> int:
        uid = self.next_uid
        self.id_to_uid[oid] = uid
        self.next_uid += 1
        return uid


def _ser(obj: Any, ctx: _SerCtx) -> Any:
    """Serialize a Pydantic graph to plain Python.

    Named SpecBase nodes carry a per-object `$uid` (counter) on first visit;
    repeat visits emit `{"$ref": <uid>, "$kind": "<class>"}`. Names live in
    the payload alongside but never key the cycle table — collisions across
    classes (Movie.imdb_id vs Person.imdb_id) are handled correctly.
    """
    if isinstance(obj, BaseModel):
        oid = id(obj)
        if _is_named(obj):
            if oid in ctx.id_to_uid:
                return {"$ref": ctx.id_to_uid[oid], "$kind": type(obj).__name__}
            uid = ctx.assign(oid)
        else:
            uid = None

        result: dict[str, Any] = {"$kind": type(obj).__name__}
        if uid is not None:
            result["$uid"] = uid
        for fname in type(obj).model_fields:
            value = getattr(obj, fname)
            result[fname] = _ser(value, ctx)
        return result

    if isinstance(obj, list):
        return [_ser(item, ctx) for item in obj]
    if isinstance(obj, tuple):
        return [_ser(item, ctx) for item in obj]
    if isinstance(obj, dict):
        return {k: _ser(v, ctx) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(_ser(item, ctx) for item in obj)

    # Enums → their .value
    val = getattr(obj, "value", None)
    if val is not None and hasattr(obj, "name") and not isinstance(obj, BaseModel):
        if isinstance(val, (str, int, float, bool)):
            return val

    return obj


def spec_to_dict(spec: Spec) -> dict[str, Any]:
    """Full-fidelity serialize a Spec.  Round-trips via `spec_from_dict`."""
    return _ser(spec, _SerCtx())


# ─── Deserializer ───────────────────────────────────────────────────────────

# Lookup: $kind class name → metaschema class
_KIND_REGISTRY: dict[str, type] = {
    "Primitive": Primitive,
    "Array": Array,
    "ClassRef": ClassRef,
    "SlotConstraints": SlotConstraints,
    "Slot": Slot,
    "OntologyClass": OntologyClass,
    "Constraint": Constraint,
    "Source": Source,
    "Spec": Spec,
    "Literal_": Literal_,
    "SlotPath": SlotPath,
    "Compare": Compare,
    "BoolExpr": BoolExpr,
    "Within": Within,
    "Between": _Between,
    "Matches": Matches,
    "RelationRef": RelationRef,
    "FilteredRelation": FilteredRelation,
    "RelationProject": RelationProject,
    "RelationCount": RelationCount,
    "RelationAggregate": RelationAggregate,
    "RelationAny": RelationAny,
    "RelationAll": RelationAll,
    "RelationFirst": RelationFirst,
    "RecursiveTraversal": RecursiveTraversal,
    "ReverseRelation": ReverseRelation,
    "ScalarDerivation": ScalarDerivation,
    "FormatDerivation": FormatDerivation,
}


class _Index:
    """Pass-1 build → UID-keyed dictionary of placeholder Pydantic objects."""

    def __init__(self) -> None:
        self.by_uid: dict[int, Any] = {}

    def get(self, uid: int) -> Any:
        if uid not in self.by_uid:
            raise KeyError(f"$ref → uid={uid} not found in pass-1 index")
        return self.by_uid[uid]


_PLACEHOLDER_KINDS = {
    "Slot": Slot,
    "OntologyClass": OntologyClass,
    "Source": Source,
    "Constraint": Constraint,
}


def _pass1_build(d: Any, index: _Index) -> None:
    """Recursively walk the JSON tree; for each named entity that carries a
    `$uid`, create a name-only placeholder keyed by uid.  Source and
    Constraint placeholders are built later in pass 2 because their
    cross-refs (entity_class, identifier_slot, primary, body) need to
    resolve through the uid index.
    """
    if isinstance(d, list):
        for item in d:
            _pass1_build(item, index)
        return
    if not isinstance(d, dict):
        return
    if "$ref" in d:
        return

    kind = d.get("$kind")
    uid = d.get("$uid")
    name = d.get("name")
    placeholder_cls = _PLACEHOLDER_KINDS.get(kind) if isinstance(kind, str) else None

    if (
        placeholder_cls in (Slot, OntologyClass)
        and isinstance(uid, int)
        and isinstance(name, str)
        and uid not in index.by_uid
    ):
        index.by_uid[uid] = placeholder_cls(name=name)

    for value in d.values():
        _pass1_build(value, index)


def _is_ref(d: Any) -> bool:
    return isinstance(d, dict) and "$ref" in d


def _resolve(d: Any, index: _Index) -> Any:
    """Pass-2: walk JSON → real Pydantic object graph.

    `$ref` tokens look up the placeholder in the uid index.
    Inline definitions with `$uid` patch the corresponding placeholder
    (preserving identity); inline definitions without `$uid` (Sources,
    Constraints, expression-tree nodes) construct fresh.
    """
    if isinstance(d, list):
        return [_resolve(item, index) for item in d]
    if isinstance(d, tuple):
        return tuple(_resolve(item, index) for item in d)
    if not isinstance(d, dict):
        return d
    if "$ref" in d:
        return index.get(d["$ref"])

    kind = d.get("$kind")
    if kind is None:
        # Plain dict (e.g. prefixes); recurse into values.
        return {k: _resolve(v, index) for k, v in d.items() if k != "$kind"}

    cls = _KIND_REGISTRY.get(kind)
    if cls is None:
        raise PublishGateError(f"Unknown $kind during rehydration: {kind!r}")

    kwargs: dict[str, Any] = {}
    for fname, raw in d.items():
        if fname.startswith("$"):
            continue
        kwargs[fname] = _resolve(raw, index)

    # For named entities with a uid, patch the existing pass-1 placeholder
    # so cross-refs share Python object identity.
    uid = d.get("$uid")
    if isinstance(uid, int) and uid in index.by_uid:
        existing = index.by_uid[uid]
        if isinstance(existing, (Slot, OntologyClass)):
            for k, v in kwargs.items():
                if k != "name":
                    setattr(existing, k, v)
            return existing

    obj = cls(**kwargs)

    # Source and Constraint placeholders aren't pre-built; index them by
    # uid here so any subsequent $ref lands cleanly.
    if isinstance(uid, int) and uid not in index.by_uid:
        index.by_uid[uid] = obj

    return obj


def spec_from_dict(d: dict[str, Any]) -> Spec:
    """Two-pass rehydration: dict → Spec with real Python object refs throughout."""
    index = _Index()
    _pass1_build(d, index)

    # Pass 2: slots first (type expressions have no deps),
    # then classes (slots field needs slots),
    # then sources (need class + slot),
    # then constraints (need class + expression refs into slots/classes).
    slots_resolved = [_resolve(sd, index) for sd in d.get("slots", [])]
    classes_resolved = [_resolve(cd, index) for cd in d.get("classes", [])]
    sources_resolved = [_resolve(s, index) for s in d.get("sources", [])]
    constraints_resolved = [_resolve(c, index) for c in d.get("constraints", [])]

    return Spec(
        id=d["id"],
        version=d["version"],
        slots=slots_resolved,
        classes=classes_resolved,
        sources=sources_resolved,
        constraints=constraints_resolved,
        prefixes=d.get("prefixes", {}),
    )
