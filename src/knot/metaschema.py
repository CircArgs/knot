"""Knot metaschema — Pydantic types for ontology spec authoring.

OntologyClass, Slot, TypeDefinition, Source, Spec, the expression tree
(Compare, RelationProject, etc.), and ResolutionPolicy.  Real Pydantic
object references throughout; no name-string lookups at the in-memory layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# --- 1. SpecBase ---
# ---------------------------------------------------------------------------

class SpecBase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        ser_json_bytes="base64",
        ser_json_inf_nan="strings",
        validate_assignment=True,
        frozen=False,
        use_enum_values=True,
        str_strip_whitespace=False,
        arbitrary_types_allowed=True,
    )


# ---------------------------------------------------------------------------
# --- 2. Enums ---
# ---------------------------------------------------------------------------

class ResolutionPolicy(str, Enum):
    ARGMAX_TRUST     = "argmax_trust"      # default: highest-trust contribution wins
    MODE             = "mode"              # most-frequent value (ties → ARGMAX_TRUST tiebreak)
    WEIGHTED_VOTE    = "weighted_vote"     # value with highest sum of trust scores
    MEDIAN_NUMERIC   = "median_numeric"    # numeric only; median of values
    LATEST_WATERMARK = "latest_watermark"  # contribution with latest asserted_at
    UNIQUE_OR_FAIL   = "unique_or_fail"    # all sources must agree; disagreement raises


class CompareOp(str, Enum):
    EQ          = "eq"
    NEQ         = "neq"
    GT          = "gt"
    GTE         = "gte"
    LT          = "lt"
    LTE         = "lte"
    IN          = "in"
    NOT_IN      = "not_in"
    IS_NULL     = "is_null"
    IS_NOT_NULL = "is_not_null"


class BoolOp(str, Enum):
    AND = "and"
    OR  = "or"
    NOT = "not"


class AggFunc(str, Enum):
    COUNT   = "count"
    SUM     = "sum"
    AVG     = "avg"
    MIN     = "min"
    MAX     = "max"
    COLLECT = "collect"
    FIRST   = "first"


class GroupByMode(str, Enum):
    NONE   = "none"
    SOURCE = "source"


# ---------------------------------------------------------------------------
# --- 3. TypeDefinition ---
# ---------------------------------------------------------------------------

class TypeDefinition(SpecBase):
    name: str
    base: str | None = None          # Python base type name: "str", "int", "float", etc.
    pattern: str | None = None       # optional regex constraint at the type level
    description: str | None = None


# ---------------------------------------------------------------------------
# --- 4. PermissibleValue ---
# ---------------------------------------------------------------------------

class PermissibleValue(SpecBase):
    text: str
    description: str | None = None
    meaning: str | None = None       # URI / CURIE; optional


# ---------------------------------------------------------------------------
# --- 5. IdentifierPattern ---
# ---------------------------------------------------------------------------

class IdentifierPattern(SpecBase):
    """Declares the polymorphic identifier shape on a reified class."""
    class_slot: Slot    # slot holding the entity class name
    key_slot: Slot      # slot holding the entity src key
    scope: OntologyClass | None = None  # if set, valid only within this scope


# ---------------------------------------------------------------------------
# --- 6. ReferencePattern types ---
# ---------------------------------------------------------------------------

class DirectRef(SpecBase):
    """Plain FK — target_class + slot on this class holding the FK value."""
    target_class: OntologyClass | None = None
    fk_slot: Slot


class DiscriminatedRef(SpecBase):
    """Discriminator-style polymorphic ref — class_slot names the target class."""
    target_class: OntologyClass | None = None
    class_slot: Slot    # slot holding the target class name (discriminator)
    key_slot: Slot      # slot holding the target entity key


# ---------------------------------------------------------------------------
# --- 7. Expression tree ---
# ---------------------------------------------------------------------------

class Literal_(SpecBase):
    """A constant value node in the expression tree."""
    value: Any = None


class SlotPath(SpecBase):
    """Walk from a class through an ordered chain of slots to a terminal value."""
    from_class: OntologyClass
    slots: list[Slot] = Field(default_factory=list)


class Compare(SpecBase):
    """Comparison predicate: left op right."""
    op: CompareOp
    left: SlotPath | Literal_
    right: SlotPath | Literal_ | None = None  # None for unary ops (IS_NULL, IS_NOT_NULL)

    # --- operator overloads so Compare nodes compose into BoolExpr ---
    def __and__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.AND, operands=[self, other])

    def __or__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.OR, operands=[self, other])

    def __invert__(self) -> BoolExpr:
        return BoolExpr(op=BoolOp.NOT, operands=[self])


class BoolExpr(SpecBase):
    """Boolean combination of Compare / BoolExpr nodes."""
    op: BoolOp
    operands: list[Compare | BoolExpr] = Field(default_factory=list)

    def __and__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.AND, operands=[self, other])

    def __or__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.OR, operands=[self, other])

    def __invert__(self) -> BoolExpr:
        return BoolExpr(op=BoolOp.NOT, operands=[self])


class RelationRef(SpecBase):
    """Follow a slot whose range is another class — Movie.credits, etc."""
    from_class: OntologyClass
    slot: Slot          # slot whose range is an OntologyClass

    # alias used by some design-doc examples (primary_class)
    @property
    def primary_class(self) -> OntologyClass:
        return self.from_class


class FilteredRelation(SpecBase):
    """A relation with a row-level predicate."""
    relation: RelationRef | FilteredRelation
    filter: Compare | BoolExpr


class RelationProject(SpecBase):
    """Surface a slot value from each row of the relation."""
    relation: RelationRef | FilteredRelation
    project: SlotPath


class RelationCount(SpecBase):
    """Count rows in the relation. Scalar integer."""
    relation: RelationRef | FilteredRelation
    distinct: bool = False


class RelationAggregate(SpecBase):
    """Aggregate over rows in the relation."""
    relation: RelationRef | FilteredRelation
    func: AggFunc
    operand: SlotPath | None = None     # None for COUNT-style (implicit *)
    distinct: bool = False
    group_by: GroupByMode = GroupByMode.NONE
    order_by: list[SlotPath] = Field(default_factory=list)
    pivot: bool = False                 # only valid when group_by == SOURCE


class RelationAny(SpecBase):
    """Boolean: EXISTS — any row in the relation matches."""
    relation: RelationRef | FilteredRelation


class RelationAll(SpecBase):
    """Boolean: NOT EXISTS (NOT body) — every row satisfies a predicate."""
    relation: RelationRef | FilteredRelation
    body: Compare | BoolExpr | None = None


class RelationFirst(SpecBase):
    """Surface the first row's projection by an ordering."""
    relation: RelationRef | FilteredRelation
    project: SlotPath
    order_by: list[SlotPath] = Field(default_factory=list)
    assert_unique: bool = False


class ScalarDerivation(SpecBase):
    """Within-row computed value — no relation traversal."""
    expression: SlotPath | Literal_ | Compare | BoolExpr


class FormatDerivation(SpecBase):
    """Pattern-string serialization: '{last}, {first}'."""
    template: str
    slots: list[SlotPath] = Field(default_factory=list)


# Union type for the derivation field on Slot.
DerivationExpr = (
    RelationProject
    | RelationCount
    | RelationAggregate
    | RelationAny
    | RelationAll
    | RelationFirst
    | ScalarDerivation
    | FormatDerivation
)


# ---------------------------------------------------------------------------
# --- 8. Slot ---
# ---------------------------------------------------------------------------

class Slot(SpecBase):
    name: str
    range: TypeDefinition | OntologyClass | None = None   # None only for derived slots before patch
    identifier: bool = False
    required: bool = False
    multivalued: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    pattern: str | None = None
    minimum_value: float | None = None
    maximum_value: float | None = None
    permissible_values: list[PermissibleValue] | None = None
    derivation: DerivationExpr | None = None
    reference: DirectRef | DiscriminatedRef | None = None
    description: str | None = None

    # --- operator overloads: `Credit.role == "director"` → Compare node ---
    def __eq__(self, other: object) -> Compare:  # type: ignore[override]
        right = other if isinstance(other, (SlotPath, Literal_)) else Literal_(value=other)
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Compare(op=CompareOp.EQ, left=path, right=right)

    def __ne__(self, other: object) -> Compare:  # type: ignore[override]
        right = other if isinstance(other, (SlotPath, Literal_)) else Literal_(value=other)
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Compare(op=CompareOp.NEQ, left=path, right=right)

    def __lt__(self, other: object) -> Compare:
        right = other if isinstance(other, (SlotPath, Literal_)) else Literal_(value=other)
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Compare(op=CompareOp.LT, left=path, right=right)

    def __le__(self, other: object) -> Compare:
        right = other if isinstance(other, (SlotPath, Literal_)) else Literal_(value=other)
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Compare(op=CompareOp.LTE, left=path, right=right)

    def __gt__(self, other: object) -> Compare:
        right = other if isinstance(other, (SlotPath, Literal_)) else Literal_(value=other)
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Compare(op=CompareOp.GT, left=path, right=right)

    def __ge__(self, other: object) -> Compare:
        right = other if isinstance(other, (SlotPath, Literal_)) else Literal_(value=other)
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Compare(op=CompareOp.GTE, left=path, right=right)

    def __hash__(self) -> int:
        return id(self)


# ---------------------------------------------------------------------------
# --- 9. OntologyClass ---
# ---------------------------------------------------------------------------

class OntologyClass(SpecBase):
    name: str
    is_a: OntologyClass | None = None
    mixins: list[OntologyClass] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    abstract: bool = False
    identifier_pattern: IdentifierPattern | None = None
    description: str | None = None


# Sentinel used by Slot operator overloads — never stored in a real Spec.
# SlotPath requires a from_class; when the class context is unknown at overload
# time, we fill this placeholder.  Downstream consumers replace it as needed.
_sentinel_class = OntologyClass(name="__sentinel__")


# ---------------------------------------------------------------------------
# --- 10. Source ---
# ---------------------------------------------------------------------------

class Source(SpecBase):
    name: str
    entity_class: OntologyClass
    identifier_slot: Slot
    description: str | None = None


# ---------------------------------------------------------------------------
# --- 11. Spec root ---
# ---------------------------------------------------------------------------

class Spec(SpecBase):
    id: str
    version: str
    classes: list[OntologyClass] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    types: list[TypeDefinition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# --- 12. model_rebuild for forward refs ---
# ---------------------------------------------------------------------------

IdentifierPattern.model_rebuild()
DirectRef.model_rebuild()
DiscriminatedRef.model_rebuild()
SlotPath.model_rebuild()
Compare.model_rebuild()
BoolExpr.model_rebuild()
RelationRef.model_rebuild()
FilteredRelation.model_rebuild()
RelationProject.model_rebuild()
RelationCount.model_rebuild()
RelationAggregate.model_rebuild()
RelationAny.model_rebuild()
RelationAll.model_rebuild()
RelationFirst.model_rebuild()
ScalarDerivation.model_rebuild()
FormatDerivation.model_rebuild()
Slot.model_rebuild()
OntologyClass.model_rebuild()
Source.model_rebuild()
Spec.model_rebuild()
