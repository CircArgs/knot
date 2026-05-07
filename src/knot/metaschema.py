"""Knot metaschema — Pydantic types for ontology spec authoring.

OntologyClass, Slot, TypeDefinition, Source, Spec, the expression tree
(Compare, RelationProject, etc.), and ResolutionPolicy.  Real Pydantic
object references throughout; no name-string lookups at the in-memory layer.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic._internal._model_construction import ModelMetaclass


# ---------------------------------------------------------------------------
# --- 1. SpecBase ---
# ---------------------------------------------------------------------------

class _SpecMeta(ModelMetaclass):
    """Metaclass that exposes Pydantic field defaults as class-level attributes.

    Enables the ConfigRef pattern: `Config.threshold` in a DataContext
    expression returns the field's default value (or a sentinel) rather than
    raising AttributeError.  This is a stub for the full ConfigRef machinery
    described in staging/datacontext-config-binding.md.
    """

    def __getattr__(cls, item: str) -> Any:
        # Walk MRO manually to find __pydantic_fields__ without triggering
        # __getattr__ recursion (model_fields property uses getattr internally).
        from pydantic_core import PydanticUndefined
        for klass in type.__getattribute__(cls, "__mro__"):
            pf = klass.__dict__.get("__pydantic_fields__")
            if pf and item in pf:
                fi = pf[item]
                if fi.default is not PydanticUndefined and fi.default is not None:
                    return fi.default
                if fi.default_factory is not None:
                    return fi.default_factory()
                return None
        raise AttributeError(item)


class SpecBase(BaseModel, metaclass=_SpecMeta):
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
    """Boolean combination of Compare / BoolExpr / Within / Between / Matches nodes."""
    op: BoolOp
    operands: list[Compare | BoolExpr | Within | Between | Matches] = Field(default_factory=list)

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

    def transitive(self, *, until: BoolExpr | None = None, max_depth: int | None = None) -> RecursiveTraversal:
        """Walk this relation recursively: `Person.knows.transitive(max_depth=3)`."""
        step = SlotPath(from_class=self.from_class, slots=[self.slot])
        return RecursiveTraversal(start=self, step=step, until=until, max_depth=max_depth)


class FilteredRelation(SpecBase):
    """A relation with a row-level predicate."""
    relation: RelationRef | FilteredRelation
    filter: Compare | BoolExpr


class RelationProject(SpecBase):
    """Surface a slot value from each row of the relation."""
    relation: RelationRef | FilteredRelation
    project: SlotPath

    def select(self, *slots: Slot) -> list[RelationProject]:
        """Multi-slot labeled projection: `rel.select(slot_a, slot_b)`.

        Returns one RelationProject per slot, sharing the same relation.
        Mirrors Gremlin's `select('a', 'b', 'c')` shape.
        """
        return [
            RelationProject(
                relation=self.relation,
                project=SlotPath(from_class=self.project.from_class, slots=[s]),
            )
            for s in slots
        ]


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


class Within(SpecBase):
    """Set-membership predicate: value ∈ {a, b, c, ...}. Emits SQL `IN (...)`."""
    op: ClassVar[Literal["within"]] = "within"
    left: SlotPath
    values: list[Literal_]

    def __and__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.AND, operands=[self, other])

    def __or__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.OR, operands=[self, other])

    def __invert__(self) -> BoolExpr:
        return BoolExpr(op=BoolOp.NOT, operands=[self])


class Between(SpecBase):
    """Range predicate: lower ≤ value ≤ upper (inclusive=True) or strict bounds."""
    op: ClassVar[Literal["between"]] = "between"
    left: SlotPath
    lower: Literal_
    upper: Literal_
    inclusive: bool = True

    def __and__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.AND, operands=[self, other])

    def __or__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.OR, operands=[self, other])

    def __invert__(self) -> BoolExpr:
        return BoolExpr(op=BoolOp.NOT, operands=[self])


class Matches(SpecBase):
    """String pattern predicate: LIKE / regex. Distinct from Compare for SQL emission."""
    op: ClassVar[Literal["matches"]] = "matches"
    left: SlotPath
    pattern: str

    def __and__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.AND, operands=[self, other])

    def __or__(self, other: Compare | BoolExpr) -> BoolExpr:
        return BoolExpr(op=BoolOp.OR, operands=[self, other])

    def __invert__(self) -> BoolExpr:
        return BoolExpr(op=BoolOp.NOT, operands=[self])


class RecursiveTraversal(SpecBase):
    """Walk a relation transitively until a stopping predicate.

    Used for class hierarchies (Title → Movie / Series / Episode via is_a chains)
    and recursive structural relations (Person.knows, etc.).
    """
    op: ClassVar[Literal["recursive"]] = "recursive"
    start: RelationRef
    step: SlotPath
    until: BoolExpr | None = None
    max_depth: int | None = None


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

class _SourceFilteredSlot:
    """Intermediate returned by Slot.from_source() — allows .is_null() / .is_not_null()."""

    def __init__(self, slot: Slot, source: Any) -> None:
        self._slot = slot
        self._source = source

    def is_null(self) -> Compare:
        path = SlotPath(from_class=_sentinel_class, slots=[self._slot])
        return Compare(op=CompareOp.IS_NULL, left=path)

    def is_not_null(self) -> Compare:
        path = SlotPath(from_class=_sentinel_class, slots=[self._slot])
        return Compare(op=CompareOp.IS_NOT_NULL, left=path)


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

    def from_source(self, source: Any) -> _SourceFilteredSlot:
        """Return an intermediate that supports .is_null() / .is_not_null() per source."""
        return _SourceFilteredSlot(self, source)

    def within(self, values: list[Any]) -> Within:
        """Set-membership predicate: `slot.within(["Action", "Sci-Fi"])`."""
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Within(left=path, values=[Literal_(value=v) for v in values])

    def between(self, lower: Any, upper: Any, *, inclusive: bool = True) -> Between:
        """Range predicate: `slot.between(1990, 2000)` or `.between(1990, 2000, inclusive=False)`."""
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Between(left=path, lower=Literal_(value=lower), upper=Literal_(value=upper), inclusive=inclusive)

    def matches(self, pattern: str) -> Matches:
        """Pattern predicate: `slot.matches(r"^tt\\d+")` — emits SQL LIKE / regex."""
        path = SlotPath(from_class=_sentinel_class, slots=[self])
        return Matches(left=path, pattern=pattern)

    def starts_with(self, prefix: str) -> Matches:
        """Convenience: `slot.starts_with("tt")` → `Matches(pattern="tt%")`."""
        return self.matches(prefix + "%")

    def ends_with(self, suffix: str) -> Matches:
        """Convenience: `slot.ends_with(".jpg")` → `Matches(pattern="%.jpg")`."""
        return self.matches("%" + suffix)


# DerivedSlot — a Slot whose derivation field is non-None.  Used as an
# annotation in multi-class DataContext primaries for derived-edge views.
DerivedSlot = Slot


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

    def __getattr__(self, item: str) -> Slot:
        """Allow slot access by name: Movie.imdb_id → the Slot named 'imdb_id'."""
        # Walk own slots and inherited slots (is_a chain + mixins).
        for cls in self._class_chain():
            for slot in cls.model_fields_set and [] or []:
                pass  # pydantic model_fields_set check not useful here
            try:
                slot_list = object.__getattribute__(cls, "slots")
            except AttributeError:
                continue
            for slot in slot_list:
                if slot.name == item:
                    return slot
        raise AttributeError(f"OntologyClass {self.name!r} has no slot {item!r}")

    def _class_chain(self) -> list[OntologyClass]:
        """Self + is_a ancestors, breadth-first (mixins included)."""
        seen: list[OntologyClass] = []
        queue: list[OntologyClass] = [self]
        while queue:
            current = queue.pop(0)
            if current in seen:
                continue
            seen.append(current)
            if current.is_a is not None:
                queue.append(current.is_a)
            queue.extend(current.mixins)
        return seen

    def descendants(self, *, max_depth: int | None = None) -> RecursiveTraversal:
        """Walk the is_a chain downward: `Title.descendants()`.

        Produces a RecursiveTraversal over the is_a relation starting from this
        class.  The step slot is a sentinel SlotPath representing the is_a link;
        Translator impls resolve the concrete slot at emit time.
        """
        # Sentinel slot representing the is_a structural link.
        is_a_slot = Slot(name="is_a", range=self)
        start = RelationRef(from_class=self, slot=is_a_slot)
        step = SlotPath(from_class=self, slots=[is_a_slot])
        return RecursiveTraversal(start=start, step=step, max_depth=max_depth)


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
    sources: list[Source] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# --- 12. model_rebuild for forward refs ---
# ---------------------------------------------------------------------------

IdentifierPattern.model_rebuild()
DirectRef.model_rebuild()
DiscriminatedRef.model_rebuild()
SlotPath.model_rebuild()
Compare.model_rebuild()
Within.model_rebuild()
Between.model_rebuild()
Matches.model_rebuild()
BoolExpr.model_rebuild()
RelationRef.model_rebuild()
FilteredRelation.model_rebuild()
RelationProject.model_rebuild()
RelationCount.model_rebuild()
RelationAggregate.model_rebuild()
RelationAny.model_rebuild()
RelationAll.model_rebuild()
RelationFirst.model_rebuild()
RecursiveTraversal.model_rebuild()
ScalarDerivation.model_rebuild()
FormatDerivation.model_rebuild()
Slot.model_rebuild()
OntologyClass.model_rebuild()
Source.model_rebuild()
Spec.model_rebuild()
