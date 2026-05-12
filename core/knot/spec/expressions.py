"""JSON-shape Pydantic models for constraint expression trees + translator.

Path (b) from the design: parallel JSON-wrapper models with ``kind``
discriminator fields live here at the API boundary. The metaschema stays
Python-authoring-natural (no ``kind`` fields added); the JSON-shape concern
is isolated here.

Public surface
--------------
ExprJson
    Annotated Union — Pydantic dispatches on ``kind`` via discriminator.

translate_expr(node_json, spec, primary_class) -> ExprNode
    Walk the JSON tree, resolve slot/class names against ``spec``,
    return the corresponding metaschema object.
    Raises ``ExprTranslationError`` on unknown name or unsupported kind.

This module previously lived in ``knot/api/constraint_translator.py``.
It moved to ``knot/spec/`` because translation is a pure spec-graph
primitive (JSON tree → metaschema tree); the API layer is just one
caller. ``ExprTranslationError`` replaces the old ``HTTPException`` so
non-API callers (graph/spec.py) can catch and remap.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from knot.spec.metaschema import (
    AggFunc,
    Between,
    BoolExpr,
    BoolOpKind,
    Compare,
    CompareOp,
    FilteredRelation,
    Literal_,
    Matches,
    OntologyClass,
    RelationAggregate,
    RelationAll,
    RelationAny,
    RelationCount,
    RelationProject,
    RelationRef,
    ReverseRelation,
    Slot,
    SlotPath,
    Spec,
    Within,
)


class ExprTranslationError(Exception):
    """Raised on unknown slot/class reference or unsupported kind during
    JSON-to-metaschema translation. Caller (route or graph orchestrator)
    decides how to surface the error (404 / 400)."""


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
    left: ExprJson
    right: ExprJson | None = None


class _BoolExprJson(_JsonBase):
    kind: Literal["bool_expr"]
    op: BoolOpKind
    args: list[ExprJson]


class _WithinJson(_JsonBase):
    kind: Literal["within"]
    slot: str  # slot name; resolved against spec.slots
    values: list[Any]


class _BetweenJson(_JsonBase):
    kind: Literal["between"]
    slot: str  # slot name; resolved against spec.slots
    low: Any
    high: Any


class _MatchesJson(_JsonBase):
    kind: Literal["matches"]
    slot: str  # slot name; resolved against spec.slots
    pattern: str


class _RelationAllJson(_JsonBase):
    kind: Literal["relation_all"]
    relation: ExprJson
    predicate: ExprJson


class _RelationAnyJson(_JsonBase):
    kind: Literal["relation_any"]
    relation: ExprJson
    predicate: ExprJson


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


class _RelationRefJson(_JsonBase):
    """Forward relation traversal: follow a slot whose range is another class.

    ``from_class_name`` — name of the source class (e.g. "Movie").
    ``slot_name``       — name of the FK slot on the source class (e.g. "director").
    """

    kind: Literal["relation_ref"]
    from_class_name: str
    slot_name: str


class _FilteredRelationJson(_JsonBase):
    """A relation with a row-level filter predicate applied."""

    kind: Literal["filtered_relation"]
    relation: ExprJson
    predicate: ExprJson


class _RelationProjectJson(_JsonBase):
    """Project a slot value from each row of the relation → array.

    ``relation``  — the relation to traverse (RelationRef, ReverseRelation,
                    or FilteredRelation wrapping one of those).
    ``slot_name`` — the slot on the target class to project.
    """

    kind: Literal["relation_project"]
    relation: ExprJson
    slot_name: str


class _RelationCountJson(_JsonBase):
    """Count rows in the relation → integer scalar."""

    kind: Literal["relation_count"]
    relation: ExprJson
    distinct: bool = False


class _RelationAggregateJson(_JsonBase):
    """Aggregate a slot over rows in the relation.

    ``func``      — aggregation function (AggFunc enum value as string).
    ``slot_name`` — slot on target class to aggregate (required for all funcs
                    except COUNT).
    """

    kind: Literal["relation_aggregate"]
    relation: ExprJson
    func: AggFunc
    slot_name: str | None = None
    distinct: bool = False


# Annotated union — Pydantic dispatches on the ``kind`` field automatically.
ExprJson = Annotated[
    _LiteralJson
    | _SlotPathJson
    | _CompareJson
    | _BoolExprJson
    | _WithinJson
    | _BetweenJson
    | _MatchesJson
    | _RelationAllJson
    | _RelationAnyJson
    | _ReverseRelationJson
    | _RelationRefJson
    | _FilteredRelationJson
    | _RelationProjectJson
    | _RelationCountJson
    | _RelationAggregateJson,
    Field(discriminator="kind"),
]

# Resolve forward references in the recursive models.
_CompareJson.model_rebuild()
_BoolExprJson.model_rebuild()
_RelationAllJson.model_rebuild()
_RelationAnyJson.model_rebuild()
_FilteredRelationJson.model_rebuild()
_RelationProjectJson.model_rebuild()
_RelationCountJson.model_rebuild()
_RelationAggregateJson.model_rebuild()


# ---------------------------------------------------------------------------
# Translator — JSON tree → metaschema expression tree
# ---------------------------------------------------------------------------


def _find_slot(spec: Spec, name: str) -> Slot:
    """Find a Slot by name, searching all class slots (own only, not mixin-walked).

    Slots are now inline on each OntologyClass. We search all classes for a
    slot with the given name. If multiple classes define a slot with the same
    name (different objects), we return the first found — the caller is
    responsible for providing enough context (primary_class) for the expression
    to be meaningful. In practice, expression bodies always reference slots
    that belong to the constraint's primary class or a related class.
    """
    for c in spec.classes:
        for s in c.slots:
            if s.name == name:
                return s
    raise ExprTranslationError(f"Slot {name!r} not found on any class in this draft")


def _find_class(spec: Spec, name: str) -> OntologyClass:
    for c in spec.classes:
        if c.name == name:
            return c
    raise ExprTranslationError(f"OntologyClass {name!r} not on this draft")


def _relation_target_class(relation: Any, spec: Spec) -> OntologyClass:
    """Extract the target OntologyClass from a resolved relation node.

    Used to build ``SlotPath.from_class`` for project/aggregate operands.
    """
    if isinstance(relation, ReverseRelation):
        return relation.target_class
    if isinstance(relation, FilteredRelation):
        return _relation_target_class(relation.relation, spec)
    if isinstance(relation, RelationRef):
        slot = relation.slot
        if isinstance(slot.range, OntologyClass):
            return slot.range
        raise ExprTranslationError(
            f"RelationRef slot {slot.name!r} has no OntologyClass range; "
            "cannot infer target class for projection."
        )
    raise ExprTranslationError(
        f"Cannot determine target class from relation type {type(relation).__name__!r}."
    )


def translate_expr(node_json: ExprJson, spec: Spec, primary_class: OntologyClass) -> Any:
    """Walk the JSON expression tree, resolving slot names to metaschema Slot objects.

    ``primary_class`` is the constraint's primary class; it is used as
    ``SlotPath.from_class`` for all slot-path nodes.

    Raises ``ExprTranslationError`` on unknown slot/class reference or
    unsupported kind.
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

    if isinstance(node_json, _RelationRefJson):
        from_cls = _find_class(spec, node_json.from_class_name)
        slot = _find_slot(spec, node_json.slot_name)
        return RelationRef(from_class=from_cls, slot=slot)

    if isinstance(node_json, _FilteredRelationJson):
        relation = translate_expr(node_json.relation, spec, primary_class)
        predicate = translate_expr(node_json.predicate, spec, primary_class)
        return FilteredRelation(relation=relation, filter=predicate)

    if isinstance(node_json, _RelationProjectJson):
        relation = translate_expr(node_json.relation, spec, primary_class)
        # Resolve the projected slot against the target class of the relation.
        proj_slot = _find_slot(spec, node_json.slot_name)
        # Determine the target class for SlotPath.from_class.
        target_cls = _relation_target_class(relation, spec)
        project = SlotPath(from_class=target_cls, slots=[proj_slot])
        return RelationProject(relation=relation, project=project)

    if isinstance(node_json, _RelationCountJson):
        relation = translate_expr(node_json.relation, spec, primary_class)
        return RelationCount(relation=relation, distinct=node_json.distinct)

    if isinstance(node_json, _RelationAggregateJson):
        relation = translate_expr(node_json.relation, spec, primary_class)
        operand = None
        if node_json.slot_name is not None:
            agg_slot = _find_slot(spec, node_json.slot_name)
            target_cls = _relation_target_class(relation, spec)
            operand = SlotPath(from_class=target_cls, slots=[agg_slot])
        return RelationAggregate(
            relation=relation,
            func=node_json.func,
            operand=operand,
            distinct=node_json.distinct,
        )

    # Unreachable — discriminator exhausts all variants.
    raise ExprTranslationError(f"Unsupported expression kind: {type(node_json).__name__}")
