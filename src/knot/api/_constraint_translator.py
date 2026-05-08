"""JSON-shape Pydantic models for constraint expression trees + translator.

Path (b) from the design: parallel JSON-wrapper models with ``kind``
discriminator fields live here at the API boundary.  The metaschema stays
Python-authoring-natural (no ``kind`` fields added); the JSON-shape concern
is isolated in this module.

Public surface
--------------
ExprJson
    Annotated Union — Pydantic dispatches on ``kind`` via discriminator.

translate_expr(node_json, spec) -> ExprNode
    Walk the JSON tree, resolve slot names against spec.slots,
    return the corresponding metaschema object.
    Raises HTTPException(404) on unknown slot reference.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from knot.ontology.metaschema import (
    Between,
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    Literal_,
    Matches,
    OntologyClass,
    RelationAll,
    RelationAny,
    ReverseRelation,
    Slot,
    SlotPath,
    Spec,
    Within,
)


# ---------------------------------------------------------------------------
# JSON-wrapper Pydantic models
# ---------------------------------------------------------------------------

class _JsonBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _LiteralJson(_JsonBase):
    kind: Literal["literal"]
    value: Any


class _SlotPathJson(_JsonBase):
    """Slot names as strings; resolved against spec.slots at translate time."""

    kind: Literal["slot_path"]
    slots: list[str]


class _CompareJson(_JsonBase):
    kind: Literal["compare"]
    op: CompareOp
    left: "ExprJson"
    right: "ExprJson | None" = None


class _BoolExprJson(_JsonBase):
    kind: Literal["bool_expr"]
    op: BoolOpKind
    args: list["ExprJson"]


class _WithinJson(_JsonBase):
    kind: Literal["within"]
    slot: str          # slot name; resolved against spec.slots
    values: list[Any]


class _BetweenJson(_JsonBase):
    kind: Literal["between"]
    slot: str          # slot name; resolved against spec.slots
    low: Any
    high: Any


class _MatchesJson(_JsonBase):
    kind: Literal["matches"]
    slot: str          # slot name; resolved against spec.slots
    pattern: str


class _RelationAllJson(_JsonBase):
    kind: Literal["relation_all"]
    relation: "ExprJson"
    predicate: "ExprJson"


class _RelationAnyJson(_JsonBase):
    kind: Literal["relation_any"]
    relation: "ExprJson"
    predicate: "ExprJson"


class _ReverseRelationJson(_JsonBase):
    """Reverse-FK traversal: all rows of target_class whose fk_slot matches
    the outer row's canonical_id.

    ``target_class_name`` — name of the class being traversed to (e.g. "Credit").
    ``fk_slot_name``      — name of the FK slot on target_class that points back
                           to the primary class (e.g. "person").
    """

    kind: Literal["reverse_relation"]
    target_class_name: str
    fk_slot_name: str


# Annotated union — Pydantic dispatches on the ``kind`` field automatically.
ExprJson = Annotated[
    Union[
        _LiteralJson,
        _SlotPathJson,
        _CompareJson,
        _BoolExprJson,
        _WithinJson,
        _BetweenJson,
        _MatchesJson,
        _RelationAllJson,
        _RelationAnyJson,
        _ReverseRelationJson,
    ],
    Field(discriminator="kind"),
]

# Resolve forward references in the recursive models.
_CompareJson.model_rebuild()
_BoolExprJson.model_rebuild()
_RelationAllJson.model_rebuild()
_RelationAnyJson.model_rebuild()


# ---------------------------------------------------------------------------
# Translator — JSON tree → metaschema expression tree
# ---------------------------------------------------------------------------

def _find_slot(spec: Spec, name: str) -> Slot:
    for s in spec.slots:
        if s.name == name:
            return s
    raise HTTPException(404, f"Slot {name!r} not on this draft")


def _find_class(spec: Spec, name: str) -> OntologyClass:
    for c in spec.classes:
        if c.name == name:
            return c
    raise HTTPException(404, f"OntologyClass {name!r} not on this draft")


def _sentinel_from_class(spec: Spec, primary_class: OntologyClass) -> OntologyClass:
    """Return the primary class object from the spec (for SlotPath.from_class)."""
    return primary_class


def translate_expr(node_json: ExprJson, spec: Spec, primary_class: OntologyClass) -> Any:
    """Walk the JSON expression tree, resolving slot names to metaschema Slot objects.

    ``primary_class`` is the constraint's primary class; it is used as
    ``SlotPath.from_class`` for all slot-path nodes.

    Raises ``HTTPException(404)`` on unknown slot reference.
    """
    if isinstance(node_json, _LiteralJson):
        return Literal_(value=node_json.value)

    if isinstance(node_json, _SlotPathJson):
        slots = [_find_slot(spec, name) for name in node_json.slots]
        return SlotPath(from_class=primary_class, slots=slots)

    if isinstance(node_json, _CompareJson):
        left = translate_expr(node_json.left, spec, primary_class)
        right = (
            translate_expr(node_json.right, spec, primary_class)
            if node_json.right is not None
            else None
        )
        return Compare(op=node_json.op, left=left, right=right)

    if isinstance(node_json, _BoolExprJson):
        operands = [translate_expr(a, spec, primary_class) for a in node_json.args]
        return BoolExpr(op=node_json.op, operands=operands)

    if isinstance(node_json, _WithinJson):
        slot = _find_slot(spec, node_json.slot)
        left = SlotPath(from_class=primary_class, slots=[slot])
        return Within(left=left, values=[Literal_(value=v) for v in node_json.values])

    if isinstance(node_json, _BetweenJson):
        slot = _find_slot(spec, node_json.slot)
        left = SlotPath(from_class=primary_class, slots=[slot])
        return Between(
            left=left,
            lower=Literal_(value=node_json.low),
            upper=Literal_(value=node_json.high),
        )

    if isinstance(node_json, _MatchesJson):
        slot = _find_slot(spec, node_json.slot)
        left = SlotPath(from_class=primary_class, slots=[slot])
        return Matches(left=left, pattern=node_json.pattern)

    if isinstance(node_json, _RelationAllJson):
        # Relation traversal: SQL compilation depends on relation handlers
        # (NotImplementedError surfaces at execute time — expected).
        relation = translate_expr(node_json.relation, spec, primary_class)
        body = translate_expr(node_json.predicate, spec, primary_class)
        return RelationAll(relation=relation, body=body)

    if isinstance(node_json, _RelationAnyJson):
        relation = translate_expr(node_json.relation, spec, primary_class)
        return RelationAny(relation=relation)

    if isinstance(node_json, _ReverseRelationJson):
        target_cls = _find_class(spec, node_json.target_class_name)
        fk_slot = _find_slot(spec, node_json.fk_slot_name)
        return ReverseRelation(target_class=target_cls, fk_slot=fk_slot)

    # Unreachable — discriminator exhausts all variants.
    raise HTTPException(400, f"Unsupported expression kind: {type(node_json).__name__}")
