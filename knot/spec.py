"""knot — spec builder.

Single-file dataclass-based spec construction. SQL strings everywhere SQL
appears (``Constraint.body``, ``VirtualClass.definition``, per-slot SQL
in ``SourceBinding.mappings``).

The type system is structural — a ``TypeExpression`` is one of:

  - ``Primitive``       — enum of the closed primitive set
  - ``Array(of=…)``     — homogeneous container over another TypeExpression
  - ``ClassRef(target=…)``  — FK to another class (stored as canonical_id)

Builder methods accept either a ``TypeExpression`` or a primitive name
string (``"text"`` → ``Primitive.TEXT``). All entities are plain
``@dataclass`` records so a future Java port maps 1:1 to
``record`` / ``sealed interface`` / ``enum``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


# ---------------------------------------------------------------------------
# Type expressions
# ---------------------------------------------------------------------------


class Primitive(StrEnum):
    """The closed set of primitive scalar types."""

    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


@dataclass
class Array:
    """Homogeneous array of another ``TypeExpression``."""

    of: TypeExpression

    def __str__(self) -> str:
        return f"array<{self.of}>"


@dataclass
class ClassRef:
    """FK reference to another class — stored as the target's canonical_id."""

    target: OntologyClass

    def __str__(self) -> str:
        return self.target.name


TypeExpression = Primitive | Array | ClassRef


def _coerce_type(t: TypeExpression | str) -> TypeExpression:
    """Builder helper — accept ``"text"`` as shorthand for ``Primitive.TEXT``."""
    if isinstance(t, (Primitive, Array, ClassRef)):
        return t
    if isinstance(t, str):
        try:
            return Primitive(t)
        except ValueError as e:
            raise ValueError(
                f"Unknown primitive type {t!r}. Valid: "
                f"{[p.value for p in Primitive]}; "
                f"for FK use .fk(to=...) or pass ClassRef(target=...) directly."
            ) from e
    raise TypeError(
        f"Slot type must be a TypeExpression or primitive name string, "
        f"got {type(t).__name__}"
    )


# ---------------------------------------------------------------------------
# Slot
# ---------------------------------------------------------------------------


@dataclass
class Slot:
    """A property of a class — primitive, array, or FK."""

    name: str
    type: TypeExpression
    identifier: bool = False
    required: bool = False
    description: str | None = None

    @property
    def is_fk(self) -> bool:
        return isinstance(self.type, ClassRef)


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


@dataclass
class OntologyClass:
    """A typed entity class — concrete (has a table) or abstract (mixin only)."""

    name: str
    kind: str = "concrete"  # "concrete" | "abstract"
    is_a: OntologyClass | None = None
    mixins: list[OntologyClass] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    description: str | None = None

    def slot(
        self,
        name: str,
        type: TypeExpression | str,
        *,
        identifier: bool = False,
        required: bool = False,
        description: str | None = None,
    ) -> Slot:
        s = Slot(
            name=name,
            type=_coerce_type(type),
            identifier=identifier,
            required=required,
            description=description,
        )
        self.slots.append(s)
        return s

    def fk(
        self,
        name: str,
        *,
        to: OntologyClass,
        required: bool = False,
        description: str | None = None,
    ) -> Slot:
        s = Slot(
            name=name,
            type=ClassRef(target=to),
            required=required,
            description=description,
        )
        self.slots.append(s)
        return s

    def get_slot(self, name: str) -> Slot:
        for cls in self._chain():
            for sl in cls.slots:
                if sl.name == name:
                    return sl
        raise KeyError(f"{self.name!r} has no slot {name!r}")

    def __getitem__(self, name: str) -> Slot:
        return self.get_slot(name)

    def _chain(self) -> list[OntologyClass]:
        seen: list[OntologyClass] = []
        queue: list[OntologyClass] = [self]
        while queue:
            cur = queue.pop(0)
            if any(cur is x for x in seen):
                continue
            seen.append(cur)
            if cur.is_a is not None:
                queue.append(cur.is_a)
            queue.extend(cur.mixins)
        return seen


@dataclass
class VirtualClass:
    """A virtual class — materialized as a SQL view over an is_a parent table,
    rows selected by the ``definition`` predicate. No table of its own."""

    name: str
    is_a: OntologyClass
    definition: str
    description: str | None = None


# ---------------------------------------------------------------------------
# Constraint / Source / SourceBinding
# ---------------------------------------------------------------------------


@dataclass
class Constraint:
    """Cross-row / cross-class invariant — SQL predicate body."""

    name: str
    primary: OntologyClass
    body: str
    severity: str = "error"  # "error" | "warning"
    message: str | None = None


@dataclass
class Source:
    """A named external system (imdb, tmdb)."""

    name: str
    description: str | None = None


# Module-level default stiffness for source priors, in evidence-units.
# A host that needs a stiffer or more easily-budged prior rebinds this
# before constructing bindings (no per-binding knob in the normal API).
BINDING_PRIOR_STRENGTH: int = 3


@dataclass
class SourceBinding:
    """(Source, OntologyClass) binding with per-slot SQL projections.

    ``accuracy`` is the spec author's guess at the fraction of past claims
    this source got right (0.0 - 1.0). The resolver derives a Beta prior
    from ``accuracy`` and ``BINDING_PRIOR_STRENGTH``; see ``beta_prior``.
    """

    source: Source
    class_: OntologyClass
    identifier_slot: Slot
    accuracy: float = 0.67
    mappings: dict[str, str] = field(default_factory=dict)
    description: str | None = None

    def map(self, **mappings: str) -> SourceBinding:
        self.mappings.update(mappings)
        return self

    @property
    def beta_prior(self) -> tuple[float, float]:
        """Resolver-facing Beta(α, β) derived from ``accuracy`` and the
        module-level ``BINDING_PRIOR_STRENGTH``."""
        k = BINDING_PRIOR_STRENGTH
        return (self.accuracy * k, (1.0 - self.accuracy) * k)


# ---------------------------------------------------------------------------
# Spec root + builder
# ---------------------------------------------------------------------------


@dataclass
class Spec:
    """Ontology declaration root."""

    id: str
    version: str
    classes: list[OntologyClass | VirtualClass] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    source_bindings: list[SourceBinding] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)

    def add_class(
        self,
        name: str,
        *,
        kind: str = "concrete",
        is_a: OntologyClass | None = None,
        mixins: list[OntologyClass] | None = None,
        description: str | None = None,
    ) -> OntologyClass:
        cls = OntologyClass(
            name=name,
            kind=kind,
            is_a=is_a,
            mixins=list(mixins) if mixins else [],
            description=description,
        )
        self.classes.append(cls)
        return cls

    def add_virtual_class(
        self,
        name: str,
        *,
        base: OntologyClass,
        where: str,
        description: str | None = None,
    ) -> VirtualClass:
        vc = VirtualClass(
            name=name,
            is_a=base,
            definition=where,
            description=description,
        )
        self.classes.append(vc)
        return vc

    def add_constraint(
        self,
        name: str,
        *,
        primary: OntologyClass,
        body: str,
        severity: str = "error",
        message: str | None = None,
    ) -> Constraint:
        c = Constraint(
            name=name,
            primary=primary,
            body=body,
            severity=severity,
            message=message,
        )
        self.constraints.append(c)
        return c

    def add_source(
        self,
        name: str,
        *,
        description: str | None = None,
    ) -> Source:
        s = Source(name=name, description=description)
        self.sources.append(s)
        return s

    def bind(
        self,
        source: Source,
        class_: OntologyClass,
        *,
        identifier: Slot,
        accuracy: float = 0.67,
        description: str | None = None,
    ) -> SourceBinding:
        b = SourceBinding(
            source=source,
            class_=class_,
            identifier_slot=identifier,
            accuracy=accuracy,
            description=description,
        )
        self.source_bindings.append(b)
        return b


__all__ = [
    "Primitive",
    "Array",
    "ClassRef",
    "TypeExpression",
    "Slot",
    "OntologyClass",
    "VirtualClass",
    "Constraint",
    "Source",
    "SourceBinding",
    "BINDING_PRIOR_STRENGTH",
    "Spec",
]
