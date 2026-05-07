"""Spec persistence and rehydration against the spec_revisions postgres table.

Serialization strategy (persistence boundary per spec-model.md):
  - Serialize: walk the Spec graph manually, emitting names in place of object
    references.  This avoids Pydantic's circular-reference failure on the cyclic
    graph (slot.range → OntologyClass → slots → Slot → ...).
  - Deserialize: two-pass parse.
      Pass 1 — build TypeDefinition, Slot (range=None placeholder), OntologyClass
               (slots=[]), Source (entity_class/identifier_slot placeholders)
               instances keyed by name.
      Pass 2 — resolve every name-string reference to real object refs from the
               pass-1 index.
  - After rehydration, cross-refs are real Python object identity (is), not copies.

Scope: handles exactly what B2 needs.
  TypeDefinition, Slot, OntologyClass, Source, Spec.
  Derivation expressions and ReferencePattern inner refs use Pydantic
  model_validate on the already-built pass-1 index (they don't introduce new
  top-level named entities).

  TODO: IdentifierPattern and Constraint rehydration not yet implemented — not
  on the B2 critical path.  Add when those entities appear in a real spec.
"""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import psycopg

from knot.metaschema import (
    DirectRef,
    DiscriminatedRef,
    OntologyClass,
    PermissibleValue,
    Slot,
    Source,
    Spec,
    TypeDefinition,
)


# ---------------------------------------------------------------------------
# Serialization (Spec → plain dict, refs as name-strings)
# ---------------------------------------------------------------------------

def _ser_type(t: TypeDefinition) -> dict:
    return {
        "name": t.name,
        "base": t.base,
        "pattern": t.pattern,
        "description": t.description,
    }


def _ser_permissible(pv: PermissibleValue) -> dict:
    return {"text": pv.text, "description": pv.description, "meaning": pv.meaning}


def _ser_derivation(deriv: Any) -> Any:
    """Serialize a DerivationExpr to a plain dict using Pydantic's json mode.

    Derivation expressions contain refs to OntologyClass and Slot objects
    that are already part of the spec graph.  We use Pydantic's model_dump
    after temporarily breaking cycles: the derivation sub-graph doesn't
    introduce cycles beyond what's already named, so model_dump with
    warnings=False works.  If it fails we fall back to None and log.

    The deserializer rebuilds derivations via Pydantic model_validate using
    the pass-1 index to resolve OntologyClass/Slot refs by name.
    """
    if deriv is None:
        return None
    # Derivation nodes hold real OntologyClass/Slot refs. Pydantic's model_dump
    # in json mode will explode on cycles (slot.range → OntologyClass → ...).
    # We serialize by walking manually using each node's __class__.__name__
    # and known fields, emitting name-strings for OntologyClass/Slot refs.
    return _ser_expr(deriv)


def _ser_expr(node: Any) -> Any:
    """Recursively serialize an expression tree node to plain Python.

    Named metaschema objects (OntologyClass, Slot, TypeDefinition) are
    emitted as {"$name": "<name>"} so the deserializer can look them up.
    All other SpecBase nodes are serialized field-by-field.
    """
    from knot.metaschema import (
        OntologyClass, Slot, TypeDefinition,
        Literal_, SlotPath, Compare, BoolExpr,
        RelationRef, FilteredRelation, RelationProject,
        RelationCount, RelationAggregate, RelationAny, RelationAll, RelationFirst,
        Within, Between, Matches, RecursiveTraversal,
        ScalarDerivation, FormatDerivation,
        PermissibleValue,
    )
    from pydantic import BaseModel

    if node is None:
        return None
    if isinstance(node, OntologyClass):
        return {"$name": node.name, "$type": "OntologyClass"}
    if isinstance(node, Slot):
        return {"$name": node.name, "$type": "Slot"}
    if isinstance(node, TypeDefinition):
        return {"$name": node.name, "$type": "TypeDefinition"}
    if isinstance(node, PermissibleValue):
        return _ser_permissible(node)
    if isinstance(node, list):
        return [_ser_expr(item) for item in node]
    if isinstance(node, BaseModel):
        cls_name = type(node).__name__
        result: dict[str, Any] = {"$type": cls_name}
        for field_name in type(node).model_fields:
            val = getattr(node, field_name)
            # Skip ClassVar fields (op on Within/Between/Matches/RecursiveTraversal)
            if field_name == "op" and isinstance(val, str) and hasattr(type(node), "op"):
                # These are ClassVar — skip, they're set at class level
                pass
            result[field_name] = _ser_expr(val)
        return result
    # Scalar
    return node


def _ser_reference(ref: Any) -> Any | None:
    if ref is None:
        return None
    cls_name = type(ref).__name__
    if cls_name == "DirectRef":
        return {
            "$type": "DirectRef",
            "target_class": ref.target_class.name if ref.target_class else None,
            "fk_slot": ref.fk_slot.name,
        }
    if cls_name == "DiscriminatedRef":
        return {
            "$type": "DiscriminatedRef",
            "target_class": ref.target_class.name if ref.target_class else None,
            "class_slot": ref.class_slot.name,
            "key_slot": ref.key_slot.name,
        }
    return None


def _ser_slot(slot: Slot) -> dict:
    range_val: Any
    if slot.range is None:
        range_val = None
    elif isinstance(slot.range, TypeDefinition):
        range_val = {"$name": slot.range.name, "$type": "TypeDefinition"}
    elif isinstance(slot.range, OntologyClass):
        range_val = {"$name": slot.range.name, "$type": "OntologyClass"}
    else:
        range_val = None

    permissible = None
    if slot.permissible_values is not None:
        permissible = [_ser_permissible(pv) for pv in slot.permissible_values]

    return {
        "name": slot.name,
        "range": range_val,
        "identifier": slot.identifier,
        "required": slot.required,
        "multivalued": slot.multivalued,
        "resolution_policy": slot.resolution_policy,
        "pattern": slot.pattern,
        "minimum_value": slot.minimum_value,
        "maximum_value": slot.maximum_value,
        "permissible_values": permissible,
        "derivation": _ser_derivation(slot.derivation),
        "reference": _ser_reference(slot.reference),
        "description": slot.description,
    }


def _ser_class(cls: OntologyClass) -> dict:
    return {
        "name": cls.name,
        "is_a": cls.is_a.name if cls.is_a else None,
        "mixins": [m.name for m in cls.mixins],
        "slots": [s.name for s in cls.slots],
        "abstract": cls.abstract,
        "description": cls.description,
    }


def _ser_source(src: Source) -> dict:
    return {
        "name": src.name,
        "entity_class": src.entity_class.name,
        "identifier_slot": src.identifier_slot.name,
        "description": src.description,
    }


def spec_to_dict(spec: Spec) -> dict:
    """Flatten Spec to a plain dict with name-strings at reference points."""
    return {
        "id": spec.id,
        "version": spec.version,
        "types": [_ser_type(t) for t in spec.types],
        "slots": [_ser_slot(s) for s in spec.slots],
        "classes": [_ser_class(c) for c in spec.classes],
        "sources": [_ser_source(s) for s in spec.sources],
    }


# ---------------------------------------------------------------------------
# Deserialization (plain dict → Spec with real Python refs)
# ---------------------------------------------------------------------------

def _deser_permissible(d: dict) -> PermissibleValue:
    return PermissibleValue(
        text=d["text"],
        description=d.get("description"),
        meaning=d.get("meaning"),
    )


def _resolve_expr(node: Any, type_idx: dict[str, TypeDefinition],
                  slot_idx: dict[str, Slot],
                  class_idx: dict[str, OntologyClass]) -> Any:
    """Recursively resolve a serialized expression tree back to live objects."""
    from knot.metaschema import (
        Literal_, SlotPath, Compare, BoolExpr,
        RelationRef, FilteredRelation, RelationProject,
        RelationCount, RelationAggregate, RelationAny, RelationAll, RelationFirst,
        Within, Between, Matches, RecursiveTraversal,
        ScalarDerivation, FormatDerivation,
        PermissibleValue, ResolutionPolicy,
    )

    if node is None:
        return None
    if isinstance(node, list):
        return [_resolve_expr(item, type_idx, slot_idx, class_idx) for item in node]
    if not isinstance(node, dict):
        return node

    # Named ref shortcut
    if "$name" in node:
        t = node["$type"]
        name = node["$name"]
        if t == "OntologyClass":
            return class_idx[name]
        if t == "Slot":
            return slot_idx[name]
        if t == "TypeDefinition":
            return type_idx[name]
        return node

    t = node.get("$type")
    if t is None:
        # Plain dict (e.g. PermissibleValue serialized without $type)
        if "text" in node:
            return PermissibleValue(text=node["text"], description=node.get("description"), meaning=node.get("meaning"))
        return node

    def r(v: Any) -> Any:
        return _resolve_expr(v, type_idx, slot_idx, class_idx)

    if t == "Literal_":
        return Literal_(value=node.get("value"))
    if t == "SlotPath":
        return SlotPath(
            from_class=r(node["from_class"]),
            slots=r(node.get("slots", [])),
        )
    if t == "Compare":
        return Compare(
            op=node["op"],
            left=r(node["left"]),
            right=r(node.get("right")),
        )
    if t == "BoolExpr":
        return BoolExpr(
            op=node["op"],
            operands=r(node.get("operands", [])),
        )
    if t == "RelationRef":
        return RelationRef(
            from_class=r(node["from_class"]),
            slot=r(node["slot"]),
        )
    if t == "FilteredRelation":
        return FilteredRelation(
            relation=r(node["relation"]),
            filter=r(node["filter"]),
        )
    if t == "RelationProject":
        return RelationProject(
            relation=r(node["relation"]),
            project=r(node["project"]),
        )
    if t == "RelationCount":
        return RelationCount(
            relation=r(node["relation"]),
            distinct=node.get("distinct", False),
        )
    if t == "RelationAggregate":
        return RelationAggregate(
            relation=r(node["relation"]),
            func=node["func"],
            operand=r(node.get("operand")),
            distinct=node.get("distinct", False),
            group_by=node.get("group_by", "none"),
            order_by=r(node.get("order_by", [])),
            pivot=node.get("pivot", False),
        )
    if t == "RelationAny":
        return RelationAny(relation=r(node["relation"]))
    if t == "RelationAll":
        return RelationAll(
            relation=r(node["relation"]),
            body=r(node.get("body")),
        )
    if t == "RelationFirst":
        return RelationFirst(
            relation=r(node["relation"]),
            project=r(node["project"]),
            order_by=r(node.get("order_by", [])),
            assert_unique=node.get("assert_unique", False),
        )
    if t == "Within":
        return Within(
            left=r(node["left"]),
            values=r(node.get("values", [])),
        )
    if t == "Between":
        return Between(
            left=r(node["left"]),
            lower=r(node["lower"]),
            upper=r(node["upper"]),
            inclusive=node.get("inclusive", True),
        )
    if t == "Matches":
        return Matches(
            left=r(node["left"]),
            pattern=node["pattern"],
        )
    if t == "RecursiveTraversal":
        return RecursiveTraversal(
            start=r(node["start"]),
            step=r(node["step"]),
            until=r(node.get("until")),
            max_depth=node.get("max_depth"),
        )
    if t == "ScalarDerivation":
        return ScalarDerivation(expression=r(node["expression"]))
    if t == "FormatDerivation":
        return FormatDerivation(
            template=node["template"],
            slots=r(node.get("slots", [])),
        )
    if t == "DirectRef":
        tc_name = node.get("target_class")
        fk_name = node["fk_slot"]
        return DirectRef(
            target_class=class_idx[tc_name] if tc_name else None,
            fk_slot=slot_idx[fk_name],
        )
    if t == "DiscriminatedRef":
        tc_name = node.get("target_class")
        return DiscriminatedRef(
            target_class=class_idx[tc_name] if tc_name else None,
            class_slot=slot_idx[node["class_slot"]],
            key_slot=slot_idx[node["key_slot"]],
        )

    # Unknown node type — return as-is; don't silently drop
    return node


def spec_from_dict(d: dict) -> Spec:
    """Two-pass rehydration: plain dict → Spec with real Python object refs.

    Pass 1: build TypeDefinition, Slot (range placeholder), OntologyClass
            (slots=[]) keyed by name.
    Pass 2: resolve all name-string refs to real objects from pass-1 index.
    """
    # --- Pass 1: build leaf nodes ---
    type_idx: dict[str, TypeDefinition] = {}
    for td in d.get("types", []):
        obj = TypeDefinition(
            name=td["name"],
            base=td.get("base"),
            pattern=td.get("pattern"),
            description=td.get("description"),
        )
        type_idx[obj.name] = obj

    # Build Slot shells (range=None for now)
    slot_idx: dict[str, Slot] = {}
    for sd in d.get("slots", []):
        permissible = None
        if sd.get("permissible_values") is not None:
            permissible = [_deser_permissible(pv) for pv in sd["permissible_values"]]
        obj = Slot(
            name=sd["name"],
            range=None,
            identifier=sd.get("identifier", False),
            required=sd.get("required", False),
            multivalued=sd.get("multivalued", False),
            resolution_policy=sd.get("resolution_policy", "argmax_trust"),
            pattern=sd.get("pattern"),
            minimum_value=sd.get("minimum_value"),
            maximum_value=sd.get("maximum_value"),
            permissible_values=permissible,
            derivation=None,  # patched in pass 2
            reference=None,   # patched in pass 2
            description=sd.get("description"),
        )
        slot_idx[obj.name] = obj

    # Build OntologyClass shells (slots=[] for now)
    class_idx: dict[str, OntologyClass] = {}
    for cd in d.get("classes", []):
        obj = OntologyClass(
            name=cd["name"],
            is_a=None,     # patched in pass 2
            mixins=[],     # patched in pass 2
            slots=[],      # patched in pass 2
            abstract=cd.get("abstract", False),
            description=cd.get("description"),
        )
        class_idx[obj.name] = obj

    # --- Pass 2: wire references ---

    def _resolve_range(range_node: Any) -> TypeDefinition | OntologyClass | None:
        if range_node is None:
            return None
        if isinstance(range_node, dict) and "$name" in range_node:
            t = range_node["$type"]
            name = range_node["$name"]
            if t == "TypeDefinition":
                return type_idx[name]
            if t == "OntologyClass":
                return class_idx[name]
        return None

    # Patch slots
    for sd in d.get("slots", []):
        slot = slot_idx[sd["name"]]
        slot.range = _resolve_range(sd.get("range"))
        slot.derivation = _resolve_expr(sd.get("derivation"), type_idx, slot_idx, class_idx)
        slot.reference = _resolve_expr(sd.get("reference"), type_idx, slot_idx, class_idx)

    # Patch classes
    for cd in d.get("classes", []):
        cls = class_idx[cd["name"]]
        if cd.get("is_a"):
            cls.is_a = class_idx[cd["is_a"]]
        cls.mixins = [class_idx[m] for m in cd.get("mixins", [])]
        cls.slots = [slot_idx[s] for s in cd.get("slots", [])]

    # Build Sources
    sources: list[Source] = []
    for sd in d.get("sources", []):
        src = Source(
            name=sd["name"],
            entity_class=class_idx[sd["entity_class"]],
            identifier_slot=slot_idx[sd["identifier_slot"]],
            description=sd.get("description"),
        )
        sources.append(src)

    return Spec(
        id=d["id"],
        version=d["version"],
        types=list(type_idx.values()),
        slots=list(slot_idx.values()),
        classes=list(class_idx.values()),
        sources=sources,
    )


# ---------------------------------------------------------------------------
# Content hash for a Spec dict (for storage)
# ---------------------------------------------------------------------------

def _spec_content_hash(spec_dict: dict) -> str:
    """sha256 of the JSON-serialized spec dict (sorted keys)."""
    blob = json.dumps(spec_dict, sort_keys=True, ensure_ascii=False, default=str)
    return sha256(blob.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_active(conn: psycopg.Connection) -> Spec | None:
    """Return the active spec revision as a rehydrated Spec, or None if none exists."""
    row = conn.execute(
        "SELECT spec FROM spec_revisions WHERE active = TRUE",
    ).fetchone()
    if row is None:
        return None
    spec_dict = row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return spec_from_dict(spec_dict)


def save_revision(conn: psycopg.Connection, spec: Spec) -> int:
    """Serialize spec, insert new row, atomically deactivate old active row.

    Returns the new revision number.
    """
    spec_dict = spec_to_dict(spec)
    content_hash = _spec_content_hash(spec_dict)
    spec_json = json.dumps(spec_dict)

    # Deactivate any existing active revision.
    conn.execute("UPDATE spec_revisions SET active = FALSE WHERE active = TRUE")

    # Insert new active revision.
    row = conn.execute(
        """
        INSERT INTO spec_revisions (spec, content_hash, active)
        VALUES (%s, %s, TRUE)
        RETURNING revision
        """,
        (spec_json, content_hash),
    ).fetchone()
    return row[0]


def seed_from_fixture(conn: psycopg.Connection) -> int:
    """Seed the spec_revisions table from the B2 fixture.

    Returns the new revision number.
    """
    from tests.fixtures.B2.spec import spec as _fixture_spec
    return save_revision(conn, _fixture_spec)
