"""Knot metaschema — typed Pydantic models for the spec graph.

Real Python object references between metaschema entities (commitment 2).
Names live only at the persistence boundary.

Build order in this file:
  1. SpecBase + enums
  2. TypeExpression hierarchy (Primitive, Array, ClassRef)
  3. SlotConstraints
  4. Expression tree (Literal_, SlotPath, Compare, BoolExpr,
     Relation*, ScalarDerivation, FormatDerivation, Within, Between,
     Matches, RecursiveTraversal)
  5. Slot (with SDK descriptor methods)
  6. OntologyClass
  7. DefinedClass
  8. AnyClass discriminated union
  9. Constraint
 10. Source (thin — name + description only)
 11. NullSemantics, SlotMapping, SourceBinding (reified (Source, Class) binding)
 12. Spec root
 13. model_rebuild() calls to resolve forward refs

The SDK affordance — `Movie.year > 1900`, `Movie.imdb_id.from_source(s).is_not_null()`,
`Movie.credits.where(...).collect(...)` — is woven into Slot's operator
overloads + OntologyClass.__getattr__.  The metaschema entities double as
the SDK; no two-class generation per `auto-generated-sdk.md` simplification.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ---------------------------------------------------------------------------
# 1. SpecBase — shared Pydantic configuration
# ---------------------------------------------------------------------------

_ENTITY_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,62}$"


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


class ResolutionPolicy(StrEnum):
    """Per-slot reduction under a `RESOLVED`-stance protocol
    (per `multi-valued-semantics.md`).
    """

    ARGMAX_TRUST = "argmax_trust"  # scalar trust_config, deterministic
    POSTERIOR_MEAN = "posterior_mean"  # Beta posterior, argmax α/(α+β)
    LCB = "lcb"  # Beta posterior, mean − k·stddev (conservative)


class Severity(StrEnum):
    """Constraint failure severity."""

    ERROR = "error"
    WARNING = "warning"


class CompareOp(StrEnum):
    """Comparison operators for `Compare` nodes."""

    EQ = "eq"
    NEQ = "neq"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"


class BoolOpKind(StrEnum):
    """Boolean composition kinds for `BoolExpr` nodes."""

    AND = "and"
    OR = "or"
    NOT = "not"


class AggFunc(StrEnum):
    """Aggregation functions for `RelationAggregate`."""

    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"
    COLLECT = "collect"
    FIRST = "first"


class GroupByMode(StrEnum):
    """Grouping mode for `RelationAggregate`."""

    NONE = "none"
    SOURCE = "source"


# ---------------------------------------------------------------------------
# 3. TypeExpression hierarchy
#
# Recursive sum type: every slot's `type` field is one of these.
#   Primitive("string")           — a named scalar type
#   Array(of=Primitive("string")) — homogeneous array of any TypeExpression
#   ClassRef(target_class=Movie)  — canonical_id FK to another class
# ---------------------------------------------------------------------------

STANDARD_PRIMITIVE_NAMES: list[str] = [
    "string",
    "integer",
    "float",
    "boolean",
    "datetime",
    "date",
]


class Primitive(SpecBase):
    """A scalar primitive type, e.g. Primitive("string")."""

    name: str  # must be one of STANDARD_PRIMITIVE_NAMES

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Primitive):
            return self.name == other.name
        return NotImplemented


class Array(SpecBase):
    """Homogeneous array of another TypeExpression."""

    # Forward ref resolved at model_rebuild() time
    of: Annotated[Primitive | Array | ClassRef, Field()]

    def __hash__(self) -> int:
        return id(self)


class ClassRef(SpecBase):
    """FK reference to another OntologyClass (stored as canonical_id TEXT)."""

    target_class: OntologyClass

    def __hash__(self) -> int:
        return id(self)


# TypeExpression — the sum type for all slot types.
# Defined after the three concrete classes exist so the union is valid at runtime.
TypeExpression = Primitive | Array | ClassRef


# ---------------------------------------------------------------------------
# 4. SlotConstraints — all slot-level constraint data in one place
# ---------------------------------------------------------------------------


class SlotConstraints(SpecBase):
    """Optional constraints layered on a slot's type."""

    pattern: str | None = None
    min_value: float | None = None
    max_value: float | None = None
    permissible_values: list[str] | None = None


# ---------------------------------------------------------------------------
# 5. Expression tree — the unified expression substrate
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

    from_class: OntologyClass
    slots: list[Slot] = Field(default_factory=list)


# Boolean composition mixin — applied to every node that should support
# `&`/`|`/`~` so authors compose predicates fluidly.


class _BoolComposable:
    def __and__(self, other: Any) -> BoolExpr:
        return BoolExpr(op=BoolOpKind.AND, operands=[self, other])

    def __or__(self, other: Any) -> BoolExpr:
        return BoolExpr(op=BoolOpKind.OR, operands=[self, other])

    def __invert__(self) -> BoolExpr:
        return BoolExpr(op=BoolOpKind.NOT, operands=[self])


class Compare(SpecBase, _BoolComposable):
    """Comparison predicate: `left op right`."""

    op: CompareOp
    left: SlotPath | Literal_
    right: SlotPath | Literal_ | None = None  # None for unary ops


class Within(SpecBase, _BoolComposable):
    """Set-membership predicate — emits SQL `IN (...)`."""

    op: ClassVar[Literal["within"]] = "within"
    left: SlotPath
    values: list[Literal_] = Field(default_factory=list)


class Between(SpecBase, _BoolComposable):
    """Range predicate: `lower ≤ value ≤ upper`."""

    op: ClassVar[Literal["between"]] = "between"
    left: SlotPath
    lower: Literal_
    upper: Literal_
    inclusive: bool = True


class Matches(SpecBase, _BoolComposable):
    """String pattern predicate — emits SQL `LIKE` or regex."""

    op: ClassVar[Literal["matches"]] = "matches"
    left: SlotPath
    pattern: str


class BoolExpr(SpecBase, _BoolComposable):
    """Boolean combination of predicate nodes (Compare / BoolExpr / Within / Between / Matches)."""

    op: BoolOpKind
    operands: list[Any] = Field(default_factory=list)
    # `Any` here because the operand union is large and self-referential;
    # validation-by-shape happens at use sites (SQL gen, walk visitors).


class RelationRef(SpecBase):
    """Follow a slot whose range is another class — `Movie.credits`."""

    from_class: OntologyClass
    slot: Slot

    def transitive(
        self,
        *,
        until: BoolExpr | None = None,
        max_depth: int | None = None,
    ) -> RecursiveTraversal:
        """Walk this relation recursively: `Person.knows.transitive(max_depth=3)`."""
        step = SlotPath(from_class=self.from_class, slots=[self.slot])
        return RecursiveTraversal(start=self, step=step, until=until, max_depth=max_depth)

    def where(self, predicate: Any) -> FilteredRelation:
        return FilteredRelation(relation=self, filter=predicate)


class FilteredRelation(SpecBase):
    """A relation with a row-level predicate."""

    relation: RelationRef | FilteredRelation | ReverseRelation
    filter: Any  # Compare | BoolExpr | Within | Between | Matches

    def where(self, predicate: Any) -> FilteredRelation:
        return FilteredRelation(
            relation=self.relation,
            filter=BoolExpr(op=BoolOpKind.AND, operands=[self.filter, predicate]),
        )


class RelationProject(SpecBase):
    """Surface a slot value from each row of the relation."""

    relation: RelationRef | FilteredRelation | ReverseRelation
    project: SlotPath


class RelationCount(SpecBase):
    """Count rows in the relation."""

    relation: RelationRef | FilteredRelation | ReverseRelation
    distinct: bool = False


class RelationAggregate(SpecBase):
    """Aggregate over rows in the relation (SUM, AVG, COLLECT, etc.)."""

    relation: RelationRef | FilteredRelation | ReverseRelation
    func: AggFunc
    operand: SlotPath | None = None
    distinct: bool = False
    group_by: GroupByMode = GroupByMode.NONE
    order_by: list[SlotPath] = Field(default_factory=list)
    pivot: bool = False  # only valid when group_by == SOURCE


class RelationAny(SpecBase):
    """Boolean: `EXISTS` — any row in the relation matches."""

    relation: RelationRef | FilteredRelation | ReverseRelation


class RelationAll(SpecBase):
    """Boolean: `NOT EXISTS (NOT body)` — every row satisfies a predicate."""

    relation: RelationRef | FilteredRelation | ReverseRelation
    body: Any | None = None  # Compare | BoolExpr


class RelationFirst(SpecBase):
    """Surface the first row's projection by an ordering."""

    relation: RelationRef | FilteredRelation | ReverseRelation
    project: SlotPath
    order_by: list[SlotPath] = Field(default_factory=list)
    assert_unique: bool = False


class RecursiveTraversal(SpecBase):
    """Walk a relation transitively until a stopping predicate.

    Used for class hierarchies (Title → Movie / Series / Episode via `is_a`
    chains) and recursive structural relations (`Person.knows`).
    """

    op: ClassVar[Literal["recursive"]] = "recursive"
    start: RelationRef
    step: SlotPath
    until: Any | None = None  # Compare | BoolExpr
    max_depth: int | None = None


class ReverseRelation(SpecBase):
    """Reverse-FK traversal: all rows of target_class whose fk_slot value
    matches the canonical_id of the primary (outer) row.

    Represents: "all Credit rows whose Credit.person == this.canonical_id".
    Used as the ``relation`` argument to RelationAll / RelationAny / RelationFirst
    when traversing from parent-class rows back to referencing rows.

    ``target_class`` — the class being traversed to (e.g. Credit).
    ``fk_slot``      — the slot on target_class that holds the FK back to
                       the primary class (e.g. Credit.person).
    """

    target_class: OntologyClass
    fk_slot: Slot


class ScalarDerivation(SpecBase):
    """Within-row computed value — no relation traversal."""

    expression: Any  # SlotPath | Literal_ | Compare | BoolExpr


class FormatDerivation(SpecBase):
    """Pattern-string serialization: `'{last}, {first}'`."""

    template: str
    slots: list[SlotPath] = Field(default_factory=list)


# Union type for the `derivation` field on Slot.
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
# 6. Slot + SDK affordances
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

    def __init__(self, slot: Slot, source: Any) -> None:
        self._slot = slot
        self._source = source

    def is_null(self) -> Compare:
        path = SlotPath(from_class=_sentinel_class, slots=[self._slot])
        return Compare(op=CompareOp.IS_NULL, left=path)

    def is_not_null(self) -> Compare:
        path = SlotPath(from_class=_sentinel_class, slots=[self._slot])
        return Compare(op=CompareOp.IS_NOT_NULL, left=path)


def _coerce_right(other: Any) -> SlotPath | Literal_:
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

    name: str = Field(pattern=_ENTITY_NAME_PATTERN)
    type: TypeExpression | None = None
    identifier: bool = False
    required: bool = False
    resolution_policy: ResolutionPolicy = ResolutionPolicy.ARGMAX_TRUST
    constraints: SlotConstraints | None = None
    derivation: Any | None = None  # DerivationExpr
    description: str | None = None

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


# ---------------------------------------------------------------------------
# 7. OntologyClass
# ---------------------------------------------------------------------------


class OntologyClass(SpecBase):
    """A typed entity class in the ontology (concrete or abstract).

    ``kind`` discriminates between "concrete" (ingestable, has a table) and
    "abstract" (not ingestable, no table — used as a mixin/parent only).
    The ``abstract`` property is kept for backward-compat reads.

    `__getattr__` resolves slot names so impl authors write
    `Movie.imdb_id` rather than indexing into a slot list.  Walks the
    is_a chain + mixins to inherit slot visibility.
    """

    name: str = Field(pattern=_ENTITY_NAME_PATTERN)
    kind: Literal["concrete", "abstract"] = "concrete"
    is_a: OntologyClass | None = None
    mixins: list[OntologyClass] = Field(default_factory=list)
    slots: list[Slot] = Field(default_factory=list)
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_abstract_kw(cls, data: Any) -> Any:
        """Accept legacy ``abstract: bool`` at construction and map to ``kind``.

        Existing test fixtures and external callers pass ``abstract=True/False``;
        translate at the pre-validation stage so the new ``kind`` discriminator
        gets the right value without breaking the call sites.
        """
        if isinstance(data, dict) and "abstract" in data and "kind" not in data:
            abstract_val = data.pop("abstract")
            data["kind"] = "abstract" if abstract_val else "concrete"
        return data

    @property
    def abstract(self) -> bool:
        """Backward-compat read: True when kind == 'abstract'."""
        return self.kind == "abstract"

    def __getattr__(self, item: str) -> Slot:
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
                slots_list = type.__getattribute__(type(cls), "model_fields") and getattr(
                    cls, "slots", None
                )
            except Exception:
                slots_list = getattr(cls, "slots", None)
            if not slots_list:
                continue
            for slot in slots_list:
                if getattr(slot, "name", None) == item:
                    return slot
        raise AttributeError(f"OntologyClass {self.name!r} has no slot {item!r}")

    def __hash__(self) -> int:
        return id(self)

    def _class_chain(self) -> list[OntologyClass]:
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

    def descendants(self, *, max_depth: int | None = None) -> RecursiveTraversal:
        """Walk the is_a chain downward."""
        is_a_slot = Slot(name="is_a", type=ClassRef(target_class=self))
        start = RelationRef(from_class=self, slot=is_a_slot)
        step = SlotPath(from_class=self, slots=[is_a_slot])
        return RecursiveTraversal(start=start, step=step, max_depth=max_depth)


class DefinedClass(SpecBase):
    """A class backed by a VIEW rather than a table (OWL-DL defined class).

    Structurally distinct from OntologyClass:
      - ``definition`` (SQL predicate) is mandatory — it is the WHERE-clause
        of the VIEW that selects rows from the ``is_a`` parent table.
      - ``is_a`` is required: must point to a concrete or abstract OntologyClass
        (no DefinedClass nesting).
      - No ``slots`` field — own slots are meaningless for a VIEW; the VIEW
        inherits all columns from the parent table.
      - Storage is a VIEW; the DDL emitter never creates a table for this class.

    ``kind`` is a fixed discriminator literal ("defined") used by the
    ``AnyClass`` discriminated union.
    """

    name: str = Field(pattern=_ENTITY_NAME_PATTERN)
    kind: Literal["defined"] = "defined"
    is_a: OntologyClass  # required; no DefinedClass nesting
    definition: str  # SQL predicate (WHERE-clause body), validated via sqlglot at publish
    mixins: list[OntologyClass] = Field(default_factory=list)
    description: str | None = None

    def __hash__(self) -> int:
        return id(self)


# AnyClass — discriminated union over the two class kinds.
# Pydantic uses the ``kind`` field to route deserialization.
AnyClass = Annotated[OntologyClass | DefinedClass, Field(discriminator="kind")]


# Sentinel `from_class` used by Slot operator overloads.  Replaced by the
# real class context at SQL-gen / DataContext-fulfill time.  Never stored
# in a published spec.
_sentinel_class = OntologyClass(name="__sentinel__")


# ---------------------------------------------------------------------------
# 8. Constraint
# ---------------------------------------------------------------------------


class Constraint(SpecBase):
    """Cross-row / cross-class invariant.

    ``body`` is a SQL predicate string (WHERE-clause fragment) validated via
    sqlglot at publish time.  Knot's SQL generator emits validation SQL with
    the uniform ``(rule_id, class_name, slot_name, offending_pk, detail)``
    shape per the constraint spec.

    Example body: ``year >= 1888``
    Example body with ClassRef traversal: ``director IS NOT NULL``
    """

    name: str = Field(pattern=_ENTITY_NAME_PATTERN)
    primary: OntologyClass
    body: str  # SQL predicate validated via sqlglot
    severity: Severity = Severity.ERROR
    message: str | None = None


# ---------------------------------------------------------------------------
# 9. Source (thin — just identity; per-class metadata lives on SourceBinding)
# ---------------------------------------------------------------------------


class Source(SpecBase):
    """A named external system (imdb, tmdb, wiki).

    Thin — just identity.  The per-class relationship metadata (which class
    it feeds, which slot is the native identifier, trust priors, field
    mappings) lives on ``SourceBinding``.  One Source can bind to multiple
    classes.
    """

    name: str = Field(pattern=_ENTITY_NAME_PATTERN)
    description: str | None = None

    def __hash__(self) -> int:
        return id(self)


# ---------------------------------------------------------------------------
# 10. NullSemantics, SlotMapping, SourceBinding — reified (Source, Class) binding
# ---------------------------------------------------------------------------


class NullSemantics(StrEnum):
    """How to interpret a NULL value from a source field."""

    NO_CLAIM = "no_claim"  # NULL means "I don't know" — skip in resolution
    ASSERTED_ABSENT = "asserted_absent"  # NULL means "I assert this has no value"


class SlotMapping(SpecBase):
    """How one source field projects to one class slot.

    ``slot``          — target Slot on the class.
    ``source_field``  — field name in the source's row payload (may differ from slot.name).
    ``default``       — value to use when the source omits the field entirely.
    ``null_semantics``— how to handle explicit NULL from the source.
    ``prior``         — optional per-slot Beta(α,β) prior overriding the binding-level prior.
    """

    slot: Slot
    source_field: str
    default: Any | None = None
    null_semantics: NullSemantics = NullSemantics.NO_CLAIM
    prior: tuple[float, float] | None = None  # Beta(α,β) — None means use binding-level prior


class SourceBinding(SpecBase):
    """Reified (Source, OntologyClass) binding.

    One per (Source, OntologyClass) pair. Carries everything that the old
    fat ``Source`` carried beyond its name. A single Source can have multiple
    bindings (one per class it contributes to).

    ``source``          — the named external system.
    ``class_``          — the OntologyClass this binding feeds.
    ``identifier_slot`` — which class slot is the source-native identifier.
    ``mappings``        — optional per-slot projection rules (source_field → slot).
                          Slots not covered by any mapping are passed through by name.
    ``trust_prior``     — Beta(α,β) seed for POSTERIOR_MEAN / LCB.  Beta(1,1) = uniform.
    ``required_slots``  — slots that must be non-null; batch rejected with 422 on violation.
    ``description``     — human-readable note on this binding.
    """

    source: Source
    class_: OntologyClass = Field(alias="class")
    identifier_slot: Slot
    mappings: list[SlotMapping] = Field(default_factory=list)
    trust_prior: tuple[float, float] = (1.0, 1.0)  # Beta(α,β)
    required_slots: list[Slot] = Field(default_factory=list)
    description: str | None = None

    def __hash__(self) -> int:
        return id(self)

    @property
    def binding_id(self) -> str:
        """Deterministic identifier for this binding: ``{source.name}__{class_.name}``."""
        return f"{self.source.name}__{self.class_.name}"


# ---------------------------------------------------------------------------
# 11. Spec root
# ---------------------------------------------------------------------------


class Spec(SpecBase):
    """The ontology declaration — the input to compile()."""

    id: str
    version: str
    classes: list[AnyClass] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    source_bindings: list[SourceBinding] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    prefixes: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 12. Resolve forward refs
# ---------------------------------------------------------------------------

# Order matters here — model_rebuild walks annotations and needs every
# referenced symbol to exist by name in the module.
Primitive.model_rebuild()
Array.model_rebuild()
ClassRef.model_rebuild()
SlotConstraints.model_rebuild()
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
ReverseRelation.model_rebuild()
ScalarDerivation.model_rebuild()
FormatDerivation.model_rebuild()
Slot.model_rebuild()
OntologyClass.model_rebuild()
DefinedClass.model_rebuild()
Constraint.model_rebuild()
Source.model_rebuild()
SlotMapping.model_rebuild()
SourceBinding.model_rebuild()
Spec.model_rebuild()


__all__ = [
    "SpecBase",
    # enums
    "ResolutionPolicy",
    "Severity",
    "CompareOp",
    "BoolOpKind",
    "AggFunc",
    "GroupByMode",
    "NullSemantics",
    # type expression hierarchy
    "STANDARD_PRIMITIVE_NAMES",
    "TypeExpression",
    "Primitive",
    "Array",
    "ClassRef",
    # slot constraints
    "SlotConstraints",
    # expression tree
    "Literal_",
    "SlotPath",
    "Compare",
    "BoolExpr",
    "Within",
    "Between",
    "Matches",
    "RelationRef",
    "FilteredRelation",
    "RelationProject",
    "RelationCount",
    "RelationAggregate",
    "RelationAny",
    "RelationAll",
    "RelationFirst",
    "RecursiveTraversal",
    "ReverseRelation",
    "ScalarDerivation",
    "FormatDerivation",
    "DerivationExpr",
    # slots + classes
    "Slot",
    "DerivedSlot",
    "OntologyClass",
    "DefinedClass",
    "AnyClass",
    # constraints + sources + bindings + root
    "Constraint",
    "Source",
    "SlotMapping",
    "SourceBinding",
    "Spec",
]
