"""Knot metaschema — typed Pydantic models for the spec graph.

Real Python object references between metaschema entities (commitment 2).
Names live only at the persistence boundary.

Build order in this file:
  1. SpecBase + enums
  2. Leaf entities (TypeDefinition, PermissibleValue)
  3. Expression tree (Literal_, SlotPath, Compare, BoolExpr,
     Relation*, ScalarDerivation, FormatDerivation, Within, Between,
     Matches, RecursiveTraversal)
  4. Slot + SlotOverride (with SDK descriptor methods)
  5. OntologyClass + UniqueKey + IdentifierPattern + ReferencePattern
  6. Constraint
  7. Source + Spec root
  8. model_rebuild() calls to resolve forward refs

The SDK affordance — `Movie.year > 1900`, `Movie.imdb_id.from_source(s).is_not_null()`,
`Movie.credits.where(...).collect(...)` — is woven into Slot's operator
overloads + OntologyClass.__getattr__.  The metaschema entities double as
the SDK; no two-class generation per `auto-generated-sdk.md` simplification.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, ClassVar, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 1. SpecBase — shared Pydantic configuration
# ---------------------------------------------------------------------------

class SpecBase(BaseModel):
    """Common parent for every metaschema model.

    `extra="forbid"` enforces commitment 16 (loud failures).
    `frozen=False` because pass-2 rehydration patches placeholder fields.
    `arbitrary_types_allowed=True` lets fields hold real Python object refs.
    """

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        ser_json_inf_nan="strings",
        validate_assignment=False,
        frozen=False,
        use_enum_values=False,
        str_strip_whitespace=False,
        arbitrary_types_allowed=True,
    )


# ---------------------------------------------------------------------------
# 2. Enums
# ---------------------------------------------------------------------------

class ResolutionPolicy(str, Enum):
    """Per-slot reduction under a `RESOLVED`-stance protocol
    (per `multi-valued-semantics.md`).
    """

    ARGMAX_TRUST     = "argmax_trust"      # scalar trust_config, deterministic
    POSTERIOR_MEAN   = "posterior_mean"    # Beta posterior, argmax α/(α+β)
    LCB              = "lcb"               # Beta posterior, mean − k·stddev (conservative)
    MODE             = "mode"
    WEIGHTED_VOTE    = "weighted_vote"
    MEDIAN_NUMERIC   = "median_numeric"
    LATEST_WATERMARK = "latest_watermark"
    UNIQUE_OR_FAIL   = "unique_or_fail"


class Severity(str, Enum):
    """Constraint failure severity."""

    ERROR   = "error"
    WARNING = "warning"


class CompareOp(str, Enum):
    """Comparison operators for `Compare` nodes."""

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


class BoolOpKind(str, Enum):
    """Boolean composition kinds for `BoolExpr` nodes."""

    AND = "and"
    OR  = "or"
    NOT = "not"


class AggFunc(str, Enum):
    """Aggregation functions for `RelationAggregate`."""

    COUNT   = "count"
    SUM     = "sum"
    AVG     = "avg"
    MIN     = "min"
    MAX     = "max"
    COLLECT = "collect"
    FIRST   = "first"


class GroupByMode(str, Enum):
    """Grouping mode for `RelationAggregate`."""

    NONE   = "none"
    SOURCE = "source"


class ReferenceKind(str, Enum):
    """Discriminator for ReferencePattern variants."""

    DIRECT        = "direct"
    DISCRIMINATED = "discriminated"


# ---------------------------------------------------------------------------
# 3. Leaf entities
# ---------------------------------------------------------------------------

class TypeDefinition(SpecBase):
    """A primitive or named type referenced by `Slot.range`."""

    name: str
    base: Optional[str] = None
    pattern: Optional[str] = None
    description: Optional[str] = None

    def __hash__(self) -> int:
        return id(self)


class PermissibleValue(SpecBase):
    """One legal value for an enum-typed slot."""

    text: str
    description: Optional[str] = None
    meaning: Optional[str] = None


# ---------------------------------------------------------------------------
# 4. Expression tree — the unified expression substrate
#
# These nodes appear in:
#   - Slot.derivation                       (forward + backward chain)
#   - Constraint.body                       (validation)
#   - DataContext bodies (modeling layer)   (impl reads — out of scope here)
#   - SDK expression construction           (impl-author ergonomics)
#
# `Compare`, `BoolExpr`, `Within`, `Between`, `Matches` carry boolean-composition
# operator overloads (`&`, `|`, `~`) so users compose:
#     (Movie.year > 1900) & (Movie.runtime > 90)
# ---------------------------------------------------------------------------

class Literal_(SpecBase):
    """A constant value node."""

    value: Any = None


class SlotPath(SpecBase):
    """Walk from a class through an ordered chain of slots to a terminal value."""

    from_class: "OntologyClass"
    slots: list["Slot"] = Field(default_factory=list)


# Boolean composition mixin — applied to every node that should support
# `&`/`|`/`~` so authors compose predicates fluidly.

class _BoolComposable:
    def __and__(self, other: Any) -> "BoolExpr":
        return BoolExpr(op=BoolOpKind.AND, operands=[self, other])

    def __or__(self, other: Any) -> "BoolExpr":
        return BoolExpr(op=BoolOpKind.OR, operands=[self, other])

    def __invert__(self) -> "BoolExpr":
        return BoolExpr(op=BoolOpKind.NOT, operands=[self])


class Compare(SpecBase, _BoolComposable):
    """Comparison predicate: `left op right`."""

    op: CompareOp
    left: Union["SlotPath", "Literal_"]
    right: Optional[Union["SlotPath", "Literal_"]] = None  # None for unary ops


class Within(SpecBase, _BoolComposable):
    """Set-membership predicate — emits SQL `IN (...)`."""

    op: ClassVar[Literal["within"]] = "within"
    left: "SlotPath"
    values: list["Literal_"] = Field(default_factory=list)


class Between(SpecBase, _BoolComposable):
    """Range predicate: `lower ≤ value ≤ upper`."""

    op: ClassVar[Literal["between"]] = "between"
    left: "SlotPath"
    lower: "Literal_"
    upper: "Literal_"
    inclusive: bool = True


class Matches(SpecBase, _BoolComposable):
    """String pattern predicate — emits SQL `LIKE` or regex."""

    op: ClassVar[Literal["matches"]] = "matches"
    left: "SlotPath"
    pattern: str


class BoolExpr(SpecBase, _BoolComposable):
    """Boolean combination of predicate nodes (Compare / BoolExpr / Within / Between / Matches)."""

    op: BoolOpKind
    operands: list[Any] = Field(default_factory=list)
    # `Any` here because the operand union is large and self-referential;
    # validation-by-shape happens at use sites (SQL gen, walk visitors).


class RelationRef(SpecBase):
    """Follow a slot whose range is another class — `Movie.credits`."""

    from_class: "OntologyClass"
    slot: "Slot"

    def transitive(
        self,
        *,
        until: Optional["BoolExpr"] = None,
        max_depth: Optional[int] = None,
    ) -> "RecursiveTraversal":
        """Walk this relation recursively: `Person.knows.transitive(max_depth=3)`."""
        step = SlotPath(from_class=self.from_class, slots=[self.slot])
        return RecursiveTraversal(start=self, step=step, until=until, max_depth=max_depth)

    def where(self, predicate: Any) -> "FilteredRelation":
        return FilteredRelation(relation=self, filter=predicate)


class FilteredRelation(SpecBase):
    """A relation with a row-level predicate."""

    relation: Union["RelationRef", "FilteredRelation"]
    filter: Any  # Compare | BoolExpr | Within | Between | Matches

    def where(self, predicate: Any) -> "FilteredRelation":
        return FilteredRelation(
            relation=self.relation,
            filter=BoolExpr(op=BoolOpKind.AND, operands=[self.filter, predicate]),
        )


class RelationProject(SpecBase):
    """Surface a slot value from each row of the relation."""

    relation: Union["RelationRef", "FilteredRelation"]
    project: "SlotPath"


class RelationCount(SpecBase):
    """Count rows in the relation."""

    relation: Union["RelationRef", "FilteredRelation"]
    distinct: bool = False


class RelationAggregate(SpecBase):
    """Aggregate over rows in the relation (SUM, AVG, COLLECT, etc.)."""

    relation: Union["RelationRef", "FilteredRelation"]
    func: AggFunc
    operand: Optional["SlotPath"] = None
    distinct: bool = False
    group_by: GroupByMode = GroupByMode.NONE
    order_by: list["SlotPath"] = Field(default_factory=list)
    pivot: bool = False  # only valid when group_by == SOURCE


class RelationAny(SpecBase):
    """Boolean: `EXISTS` — any row in the relation matches."""

    relation: Union["RelationRef", "FilteredRelation"]


class RelationAll(SpecBase):
    """Boolean: `NOT EXISTS (NOT body)` — every row satisfies a predicate."""

    relation: Union["RelationRef", "FilteredRelation"]
    body: Optional[Any] = None  # Compare | BoolExpr


class RelationFirst(SpecBase):
    """Surface the first row's projection by an ordering."""

    relation: Union["RelationRef", "FilteredRelation"]
    project: "SlotPath"
    order_by: list["SlotPath"] = Field(default_factory=list)
    assert_unique: bool = False


class RecursiveTraversal(SpecBase):
    """Walk a relation transitively until a stopping predicate.

    Used for class hierarchies (Title → Movie / Series / Episode via `is_a`
    chains) and recursive structural relations (`Person.knows`).
    """

    op: ClassVar[Literal["recursive"]] = "recursive"
    start: "RelationRef"
    step: "SlotPath"
    until: Optional[Any] = None  # Compare | BoolExpr
    max_depth: Optional[int] = None


class ScalarDerivation(SpecBase):
    """Within-row computed value — no relation traversal."""

    expression: Any  # SlotPath | Literal_ | Compare | BoolExpr


class FormatDerivation(SpecBase):
    """Pattern-string serialization: `'{last}, {first}'`."""

    template: str
    slots: list["SlotPath"] = Field(default_factory=list)


# Union type for the `derivation` field on Slot.
DerivationExpr = Union[
    RelationProject,
    RelationCount,
    RelationAggregate,
    RelationAny,
    RelationAll,
    RelationFirst,
    ScalarDerivation,
    FormatDerivation,
]


# ---------------------------------------------------------------------------
# 5. Slot + SDK affordances
#
# Operator overloads on Slot return expression-tree nodes that bind by
# slot identity but use a shared sentinel `from_class`.  The class context
# is inferred from the surrounding DataContext / Constraint.primary at
# fulfill time, so the sentinel is a placeholder, not a runtime defect.
# ---------------------------------------------------------------------------

class _SourceFilteredSlot:
    """Intermediate from `Slot.from_source(source)` — exposes `.is_null()` /
    `.is_not_null()` so impl writers can spell per-source predicates fluidly.
    """

    def __init__(self, slot: "Slot", source: Any) -> None:
        self._slot = slot
        self._source = source

    def is_null(self) -> Compare:
        path = SlotPath(from_class=_sentinel_class, slots=[self._slot])
        return Compare(op=CompareOp.IS_NULL, left=path)

    def is_not_null(self) -> Compare:
        path = SlotPath(from_class=_sentinel_class, slots=[self._slot])
        return Compare(op=CompareOp.IS_NOT_NULL, left=path)


def _coerce_right(other: Any) -> Union[SlotPath, Literal_]:
    """Coerce a comparison RHS into a tree node — slots/paths/literals all welcomed."""
    if isinstance(other, (SlotPath, Literal_)):
        return other
    if isinstance(other, Slot):
        return SlotPath(from_class=_sentinel_class, slots=[other])
    return Literal_(value=other)


class Slot(SpecBase):
    """A property of an OntologyClass.

    Operator overloads (`__gt__`, `__lt__`, `__eq__`, `.in_`, `.between`,
    `.matches`, `.from_source`) produce typed expression-tree nodes so impl
    authors compose predicates as Python expressions rather than strings.
    """

    name: str
    range: Optional[Union[TypeDefinition, "OntologyClass"]] = None
    identifier: bool = False
    required: bool = False
    multivalued: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    pattern: Optional[str] = None
    minimum_value: Optional[float] = None
    maximum_value: Optional[float] = None
    permissible_values: Optional[list[PermissibleValue]] = None
    derivation: Optional[Any] = None  # DerivationExpr
    reference: Optional[Any] = None  # DirectRef | DiscriminatedRef
    description: Optional[str] = None

    # ------------------------------------------------------------------
    # Comparison operators — each returns a Compare node
    # ------------------------------------------------------------------

    def _path(self) -> SlotPath:
        return SlotPath(from_class=_sentinel_class, slots=[self])

    def __eq__(self, other: object) -> Any:  # type: ignore[override]
        return Compare(op=CompareOp.EQ, left=self._path(), right=_coerce_right(other))

    def __ne__(self, other: object) -> Any:  # type: ignore[override]
        return Compare(op=CompareOp.NEQ, left=self._path(), right=_coerce_right(other))

    def __lt__(self, other: object) -> Compare:
        return Compare(op=CompareOp.LT, left=self._path(), right=_coerce_right(other))

    def __le__(self, other: object) -> Compare:
        return Compare(op=CompareOp.LTE, left=self._path(), right=_coerce_right(other))

    def __gt__(self, other: object) -> Compare:
        return Compare(op=CompareOp.GT, left=self._path(), right=_coerce_right(other))

    def __ge__(self, other: object) -> Compare:
        return Compare(op=CompareOp.GTE, left=self._path(), right=_coerce_right(other))

    def __hash__(self) -> int:
        return id(self)

    # ------------------------------------------------------------------
    # SDK methods
    # ------------------------------------------------------------------

    def is_null(self) -> Compare:
        return Compare(op=CompareOp.IS_NULL, left=self._path())

    def is_not_null(self) -> Compare:
        return Compare(op=CompareOp.IS_NOT_NULL, left=self._path())

    def in_(self, values: list[Any]) -> Compare:
        """Inclusive set membership: emits SQL `IN (...)`."""
        return Compare(
            op=CompareOp.IN,
            left=self._path(),
            right=Literal_(value=list(values)),
        )

    def not_in(self, values: list[Any]) -> Compare:
        return Compare(
            op=CompareOp.NOT_IN,
            left=self._path(),
            right=Literal_(value=list(values)),
        )

    def within(self, values: list[Any]) -> Within:
        """Typed `Within` node (preferred over `.in_`) — multi-value SQL `IN`."""
        return Within(
            left=self._path(),
            values=[Literal_(value=v) for v in values],
        )

    def between(self, lower: Any, upper: Any, *, inclusive: bool = True) -> Between:
        return Between(
            left=self._path(),
            lower=Literal_(value=lower),
            upper=Literal_(value=upper),
            inclusive=inclusive,
        )

    def matches(self, pattern: str) -> Matches:
        return Matches(left=self._path(), pattern=pattern)

    def starts_with(self, prefix: str) -> Matches:
        return self.matches(prefix + "%")

    def ends_with(self, suffix: str) -> Matches:
        return self.matches("%" + suffix)

    def from_source(self, source: Any) -> _SourceFilteredSlot:
        """Per-source predicate intermediate (per `er-and-storage.md` § ER impl)."""
        return _SourceFilteredSlot(self, source)


# DerivedSlot — annotation alias; a `Slot` whose `derivation` is non-None.
DerivedSlot = Slot


class SlotOverride(SpecBase):
    """Per-class refinement of a shared Slot's metadata."""

    slot: "Slot"
    required: Optional[bool] = None
    range: Optional[Union[TypeDefinition, "OntologyClass"]] = None
    pattern: Optional[str] = None
    minimum_value: Optional[float] = None
    maximum_value: Optional[float] = None
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# 6. Reference patterns & identifier patterns
# ---------------------------------------------------------------------------

class DirectRef(SpecBase):
    """Plain FK — `target_class` + the slot on this class holding the FK value."""

    ref_kind: ClassVar[ReferenceKind] = ReferenceKind.DIRECT
    target_class: Optional["OntologyClass"] = None
    fk_slot: "Slot"


class DiscriminatedRef(SpecBase):
    """Discriminator-style polymorphic ref — `class_slot` names the target class.

    Per `spec-model.md` § "Polymorphic references": consumers of polymorphic
    classes must declare the specific class connections; knot's static
    dependency graph is otherwise blind to discriminator targets.
    """

    ref_kind: ClassVar[ReferenceKind] = ReferenceKind.DISCRIMINATED
    target_class: Optional["OntologyClass"] = None
    class_slot: "Slot"
    key_slot: "Slot"


class IdentifierPattern(SpecBase):
    """Class-level: declares the polymorphic identifier shape on a reified class."""

    class_slot: "Slot"
    key_slot: "Slot"
    scope: Optional["OntologyClass"] = None


class UniqueKey(SpecBase):
    """Multi-slot uniqueness constraint on an OntologyClass."""

    slots: list["Slot"] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 7. OntologyClass
# ---------------------------------------------------------------------------

class OntologyClass(SpecBase):
    """A typed entity class in the ontology.

    `__getattr__` resolves slot names so impl authors write
    `Movie.imdb_id` rather than indexing into a slot list.  Walks the
    is_a chain + mixins to inherit slot visibility.
    """

    name: str
    is_a: Optional["OntologyClass"] = None
    mixins: list["OntologyClass"] = Field(default_factory=list)
    slots: list["Slot"] = Field(default_factory=list)
    slot_overrides: list["SlotOverride"] = Field(default_factory=list)
    unique_keys: list["UniqueKey"] = Field(default_factory=list)
    abstract: bool = False
    identifier_pattern: Optional["IdentifierPattern"] = None
    description: Optional[str] = None

    def __getattr__(self, item: str) -> "Slot":
        # Pydantic and Python internals probe for sentinel attributes; raise
        # AttributeError without searching slots so they fall back cleanly.
        if item.startswith("_") or item.startswith("model_"):
            raise AttributeError(item)
        for cls in self._class_chain():
            try:
                slots_list = object.__getattribute__(cls, "__dict__").get("__pydantic_fields_set__")
            except Exception:
                pass
            try:
                slots_list = type.__getattribute__(type(cls), "model_fields") and getattr(cls, "slots", None)
            except Exception:
                slots_list = getattr(cls, "slots", None)
            if not slots_list:
                continue
            for slot in slots_list:
                if getattr(slot, "name", None) == item:
                    return slot
        raise AttributeError(
            f"OntologyClass {self.name!r} has no slot {item!r}"
        )

    def __hash__(self) -> int:
        return id(self)

    def _class_chain(self) -> list["OntologyClass"]:
        """Self + is_a ancestors + mixins, breadth-first."""
        seen: list[OntologyClass] = []
        queue: list[OntologyClass] = [self]
        while queue:
            current = queue.pop(0)
            if any(current is s for s in seen):
                continue
            seen.append(current)
            if current.is_a is not None:
                queue.append(current.is_a)
            queue.extend(current.mixins)
        return seen

    def descendants(self, *, max_depth: Optional[int] = None) -> "RecursiveTraversal":
        """Walk the is_a chain downward."""
        is_a_slot = Slot(name="is_a", range=self)
        start = RelationRef(from_class=self, slot=is_a_slot)
        step = SlotPath(from_class=self, slots=[is_a_slot])
        return RecursiveTraversal(start=start, step=step, max_depth=max_depth)


# Sentinel `from_class` used by Slot operator overloads.  Replaced by the
# real class context at SQL-gen / DataContext-fulfill time.  Never stored
# in a published spec.
_sentinel_class = OntologyClass(name="__sentinel__")


# ---------------------------------------------------------------------------
# 8. Constraint
# ---------------------------------------------------------------------------

class Constraint(SpecBase):
    """Cross-row / cross-class invariant.

    `body` is an expression-tree node (`Compare`, `BoolExpr`, `RelationAll`,
    `RelationAny`) evaluated per primary row.  Knot's SQL generator emits
    validation SQL with the uniform `(rule_id, class_name, slot_name,
    offending_pk, detail)` shape per `spec-model.md` + `dq-design.md`.
    """

    name: str
    primary: "OntologyClass"
    body: Any  # BoolExpr | Compare | RelationAll | RelationAny
    severity: Severity = Severity.ERROR
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# 9. Source
# ---------------------------------------------------------------------------

class Source(SpecBase):
    """A team-owned lake declaration.

    Per `source-layer-contract.md`: knot starts at normalize.  The Source
    declares its name, the OntologyClass it produces facts about, and the
    slot that uniquely identifies a row within this source.  Location /
    schema / mapping are bound impl concerns (out of scope for spec graph).
    """

    name: str
    entity_class: "OntologyClass"
    identifier_slot: "Slot"
    description: Optional[str] = None

    def __hash__(self) -> int:
        return id(self)


# ---------------------------------------------------------------------------
# 10. Spec root
# ---------------------------------------------------------------------------

class Spec(SpecBase):
    """The ontology declaration — the input to compile()."""

    id: str
    version: str
    classes: list["OntologyClass"] = Field(default_factory=list)
    slots: list["Slot"] = Field(default_factory=list)
    types: list["TypeDefinition"] = Field(default_factory=list)
    sources: list["Source"] = Field(default_factory=list)
    constraints: list["Constraint"] = Field(default_factory=list)
    prefixes: dict[str, str] = Field(default_factory=dict)
    default_range: Optional["TypeDefinition"] = None


# ---------------------------------------------------------------------------
# 11. Resolve forward refs
# ---------------------------------------------------------------------------

# Order matters here — model_rebuild walks annotations and needs every
# referenced symbol to exist by name in the module.
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
SlotOverride.model_rebuild()
DirectRef.model_rebuild()
DiscriminatedRef.model_rebuild()
IdentifierPattern.model_rebuild()
UniqueKey.model_rebuild()
OntologyClass.model_rebuild()
Constraint.model_rebuild()
Source.model_rebuild()
Spec.model_rebuild()


__all__ = [
    "SpecBase",
    # enums
    "ResolutionPolicy", "Severity", "CompareOp", "BoolOpKind", "AggFunc",
    "GroupByMode", "ReferenceKind",
    # leaf
    "TypeDefinition", "PermissibleValue",
    # expression tree
    "Literal_", "SlotPath", "Compare", "BoolExpr", "Within", "Between",
    "Matches", "RelationRef", "FilteredRelation", "RelationProject",
    "RelationCount", "RelationAggregate", "RelationAny", "RelationAll",
    "RelationFirst", "RecursiveTraversal", "ScalarDerivation",
    "FormatDerivation", "DerivationExpr",
    # slots + classes
    "Slot", "DerivedSlot", "SlotOverride", "OntologyClass",
    # references
    "DirectRef", "DiscriminatedRef", "IdentifierPattern", "UniqueKey",
    # constraints + sources + root
    "Constraint", "Source", "Spec",
]
