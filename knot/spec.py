"""knot — spec builder.

Single-file dataclass-based spec construction. SQL strings everywhere SQL
appears (``Constraint.body``, ``VirtualClass.definition``, per-slot SQL
in ``SourceBinding.mappings``).

The type system is structural — a ``TypeExpression`` is one of:

  - ``Primitive``           — enum of the closed primitive set
  - ``Array(of=…)``         — homogeneous container over another TypeExpression
  - ``ClassRef(target=…)``  — FK to another class (stored as canonical_id)

Builder methods accept either a ``TypeExpression`` or a primitive name
string (``"text"`` → ``Primitive.TEXT``). All entities are plain
``@dataclass`` records so a future Java port maps 1:1 to ``record`` /
``sealed interface`` / ``enum``.

Validation happens at two levels:

  - Entity-local: ``__post_init__`` rejects empty names, out-of-range
    accuracy, and (via ``StrEnum`` coercion) unknown ``ClassKind`` /
    ``Severity`` values.
  - Cross-entity: ``Spec.validate()`` returns a list of well-formedness
    errors (orphan references, missing identifier slots, duplicate
    names, etc.). Empty list = valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Primitive(StrEnum):
    """The closed set of primitive scalar types."""

    TEXT = "text"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATE = "date"
    TIMESTAMP = "timestamp"


class ClassKind(StrEnum):
    """Whether an ``OntologyClass`` materializes a table (``CONCRETE``)
    or is mixin-only (``ABSTRACT``)."""

    CONCRETE = "concrete"
    ABSTRACT = "abstract"


class Severity(StrEnum):
    """Constraint violation severity. ``ERROR`` blocks writes; ``WARNING``
    is reported but does not block."""

    ERROR = "error"
    WARNING = "warning"


# ---------------------------------------------------------------------------
# Type expressions
# ---------------------------------------------------------------------------


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


_NAME_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]*$"


def _check_name(kind: str, name: str) -> None:
    import re

    if not isinstance(name, str) or not name:
        raise ValueError(f"{kind} name must be a non-empty string, got {name!r}")
    if not re.match(_NAME_PATTERN, name):
        raise ValueError(
            f"{kind} name {name!r} must match {_NAME_PATTERN} "
            f"(letters, digits, underscores; starts with letter or underscore)"
        )


@dataclass
class Slot:
    """A property of a class — primitive, array, or FK."""

    name: str
    type: TypeExpression
    identifier: bool = False
    required: bool = False
    description: str | None = None

    def __post_init__(self) -> None:
        _check_name("Slot", self.name)
        if self.identifier and not self.required:
            # An identifier is by definition required (NOT NULL in the
            # canonical table). Coerce silently — common builder mistake.
            self.required = True

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
    kind: ClassKind = ClassKind.CONCRETE
    is_a: OntologyClass | None = None
    mixins: list[OntologyClass] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    description: str | None = None

    def __post_init__(self) -> None:
        _check_name("OntologyClass", self.name)
        if isinstance(self.kind, str):
            try:
                self.kind = ClassKind(self.kind)
            except ValueError as e:
                raise ValueError(
                    f"OntologyClass.kind must be one of "
                    f"{[k.value for k in ClassKind]}; got {self.kind!r}"
                ) from e

    def slot(
        self,
        name: str,
        type: TypeExpression | str,
        *,
        identifier: bool = False,
        required: bool = False,
        description: str | None = None,
    ) -> Slot:
        if any(s.name == name for s in self.slots):
            raise ValueError(
                f"OntologyClass {self.name!r} already has a slot named {name!r}"
            )
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
        if any(s.name == name for s in self.slots):
            raise ValueError(
                f"OntologyClass {self.name!r} already has a slot named {name!r}"
            )
        s = Slot(
            name=name,
            type=ClassRef(target=to),
            required=required,
            description=description,
        )
        self.slots.append(s)
        return s

    def get_slot(self, name: str) -> Slot:
        for cls in self.chain():
            for sl in cls.slots:
                if sl.name == name:
                    return sl
        raise KeyError(f"{self.name!r} has no slot {name!r}")

    def __getitem__(self, name: str) -> Slot:
        return self.get_slot(name)

    def identifier_slot(self) -> Slot:
        """The first slot up the is_a + mixin chain marked ``identifier=True``."""
        for sl in self.effective_slots():
            if sl.identifier:
                return sl
        raise ValueError(f"OntologyClass {self.name!r} has no identifier slot")

    def chain(self) -> list[OntologyClass]:
        """Self + is_a ancestors + mixins, breadth-first."""
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

    def effective_slots(self) -> list[Slot]:
        """Every slot ``self`` effectively has — own + inherited via
        ``is_a`` + mixins. First-seen-wins on name collision."""
        seen: set[str] = set()
        out: list[Slot] = []
        for parent in self.chain():
            for sl in parent.slots:
                if sl.name in seen:
                    continue
                seen.add(sl.name)
                out.append(sl)
        return out


@dataclass
class VirtualClass:
    """A virtual class — materialized as a SQL view over an is_a parent table,
    rows selected by the ``definition`` predicate. No table of its own."""

    name: str
    is_a: OntologyClass
    definition: str
    description: str | None = None

    def __post_init__(self) -> None:
        _check_name("VirtualClass", self.name)
        if not isinstance(self.definition, str) or not self.definition.strip():
            raise ValueError(
                f"VirtualClass {self.name!r} requires a non-empty SQL definition"
            )


# ---------------------------------------------------------------------------
# Constraint / Source / SourceBinding
# ---------------------------------------------------------------------------


@dataclass
class Constraint:
    """Cross-row / cross-class invariant — SQL predicate body."""

    name: str
    primary: OntologyClass
    body: str
    severity: Severity = Severity.ERROR
    message: str | None = None

    def __post_init__(self) -> None:
        _check_name("Constraint", self.name)
        if not isinstance(self.body, str) or not self.body.strip():
            raise ValueError(
                f"Constraint {self.name!r} requires a non-empty SQL body"
            )
        if isinstance(self.severity, str):
            try:
                self.severity = Severity(self.severity)
            except ValueError as e:
                raise ValueError(
                    f"Constraint.severity must be one of "
                    f"{[s.value for s in Severity]}; got {self.severity!r}"
                ) from e


@dataclass
class Source:
    """A named external system (imdb, tmdb)."""

    name: str
    description: str | None = None

    def __post_init__(self) -> None:
        _check_name("Source", self.name)


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

    def __post_init__(self) -> None:
        if not (0.0 <= self.accuracy <= 1.0):
            raise ValueError(
                f"SourceBinding accuracy must be in [0, 1]; got {self.accuracy}"
            )

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

    def __post_init__(self) -> None:
        _check_name("Spec.id", self.id)
        if not isinstance(self.version, str) or not self.version.strip():
            raise ValueError("Spec.version must be a non-empty string")

    # -- builder methods --

    def add_class(
        self,
        name: str,
        *,
        kind: ClassKind | str = ClassKind.CONCRETE,
        is_a: OntologyClass | None = None,
        mixins: list[OntologyClass] | None = None,
        description: str | None = None,
    ) -> OntologyClass:
        self._check_unique_class_name(name)
        cls = OntologyClass(
            name=name,
            kind=kind if isinstance(kind, ClassKind) else ClassKind(kind),
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
        self._check_unique_class_name(name)
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
        severity: Severity | str = Severity.ERROR,
        message: str | None = None,
    ) -> Constraint:
        if any(c.name == name for c in self.constraints):
            raise ValueError(f"Spec already has a constraint named {name!r}")
        c = Constraint(
            name=name,
            primary=primary,
            body=body,
            severity=severity if isinstance(severity, Severity) else Severity(severity),
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
        if any(s.name == name for s in self.sources):
            raise ValueError(f"Spec already has a source named {name!r}")
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
        if any(
            b.source is source and b.class_ is class_ for b in self.source_bindings
        ):
            raise ValueError(
                f"Spec already has a binding for source {source.name!r} → "
                f"class {class_.name!r}"
            )
        b = SourceBinding(
            source=source,
            class_=class_,
            identifier_slot=identifier,
            accuracy=accuracy,
            description=description,
        )
        self.source_bindings.append(b)
        return b

    def _check_unique_class_name(self, name: str) -> None:
        if any(c.name == name for c in self.classes):
            raise ValueError(f"Spec already has a class named {name!r}")

    # -- well-formedness validation --

    def class_by_name(self, name: str) -> OntologyClass | VirtualClass:
        for c in self.classes:
            if c.name == name:
                return c
        raise KeyError(f"Spec has no class named {name!r}")

    def validate(self) -> list[str]:
        """Cross-entity well-formedness checks.

        Returns a list of error messages; empty list means the spec is
        valid. Local entity checks (name shape, accuracy bounds, enum
        values) have already run in each entity's ``__post_init__``.
        """
        errs: list[str] = []
        classes_by_name: dict[str, OntologyClass | VirtualClass] = {}
        for c in self.classes:
            if c.name in classes_by_name:
                errs.append(f"duplicate class name {c.name!r}")
            classes_by_name[c.name] = c

        concrete_or_abstract: dict[str, OntologyClass] = {
            c.name: c for c in self.classes if isinstance(c, OntologyClass)
        }

        for c in self.classes:
            # is_a / mixins of OntologyClass
            if isinstance(c, OntologyClass):
                if c.is_a is not None and c.is_a.name not in concrete_or_abstract:
                    errs.append(
                        f"class {c.name!r}.is_a → {c.is_a.name!r}: not in spec"
                    )
                for m in c.mixins:
                    if m.name not in concrete_or_abstract:
                        errs.append(
                            f"class {c.name!r} mixin {m.name!r}: not in spec"
                        )
                # Slot name uniqueness within the class
                seen_slots: set[str] = set()
                for sl in c.slots:
                    if sl.name in seen_slots:
                        errs.append(
                            f"class {c.name!r} has duplicate slot {sl.name!r}"
                        )
                    seen_slots.add(sl.name)
                    # ClassRef target must exist
                    if isinstance(sl.type, ClassRef):
                        if sl.type.target.name not in concrete_or_abstract:
                            errs.append(
                                f"slot {c.name}.{sl.name} ClassRef → "
                                f"{sl.type.target.name!r}: not in spec"
                            )
                # Concrete classes must have exactly one identifier slot
                # (effective — counting inherited)
                if c.kind == ClassKind.CONCRETE:
                    ids = [s for s in c.effective_slots() if s.identifier]
                    if len(ids) == 0:
                        errs.append(
                            f"concrete class {c.name!r} has no identifier slot"
                        )
                    elif len(ids) > 1:
                        names = ", ".join(s.name for s in ids)
                        errs.append(
                            f"concrete class {c.name!r} has multiple "
                            f"identifier slots: {names}"
                        )
            elif isinstance(c, VirtualClass):
                if c.is_a.name not in concrete_or_abstract:
                    errs.append(
                        f"virtual class {c.name!r}.is_a → {c.is_a.name!r}: "
                        f"not in spec"
                    )

        # Constraint references
        constraint_names: set[str] = set()
        for c in self.constraints:
            if c.name in constraint_names:
                errs.append(f"duplicate constraint name {c.name!r}")
            constraint_names.add(c.name)
            if c.primary.name not in concrete_or_abstract:
                errs.append(
                    f"constraint {c.name!r}.primary → {c.primary.name!r}: "
                    f"not in spec"
                )

        # Source name uniqueness
        sources_by_name: dict[str, Source] = {}
        for s in self.sources:
            if s.name in sources_by_name:
                errs.append(f"duplicate source name {s.name!r}")
            sources_by_name[s.name] = s

        # SourceBinding references
        binding_keys: set[tuple[str, str]] = set()
        for b in self.source_bindings:
            key = (b.source.name, b.class_.name)
            if key in binding_keys:
                errs.append(
                    f"duplicate binding source={b.source.name!r} "
                    f"class={b.class_.name!r}"
                )
            binding_keys.add(key)
            if b.source.name not in sources_by_name:
                errs.append(
                    f"binding source={b.source.name!r} class={b.class_.name!r}: "
                    f"source not in spec"
                )
            if b.class_.name not in concrete_or_abstract:
                errs.append(
                    f"binding source={b.source.name!r} class={b.class_.name!r}: "
                    f"class not in spec"
                )
                continue
            cls = concrete_or_abstract[b.class_.name]
            if cls.kind != ClassKind.CONCRETE:
                errs.append(
                    f"binding source={b.source.name!r} class={b.class_.name!r}: "
                    f"class is {cls.kind.value}, only concrete classes can bind"
                )
            # identifier_slot must be on the bound class's effective slots
            eff_names = {s.name for s in cls.effective_slots()}
            if b.identifier_slot.name not in eff_names:
                errs.append(
                    f"binding source={b.source.name!r} class={b.class_.name!r}: "
                    f"identifier_slot {b.identifier_slot.name!r} not on class"
                )
            # mapping keys must be slots on the bound class
            for slot_name in b.mappings:
                if slot_name not in eff_names:
                    errs.append(
                        f"binding source={b.source.name!r} "
                        f"class={b.class_.name!r}: mapping references "
                        f"slot {slot_name!r} not on class"
                    )

        return errs

    def validate_strict(self) -> None:
        """Like ``validate`` but raises ``SpecError`` on any failure."""
        errs = self.validate()
        if errs:
            raise SpecError(
                "Spec failed validation:\n  - " + "\n  - ".join(errs)
            )


class SpecError(ValueError):
    """Raised by ``Spec.validate_strict`` when well-formedness fails."""


__all__ = [
    "Primitive",
    "ClassKind",
    "Severity",
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
    "SpecError",
]
