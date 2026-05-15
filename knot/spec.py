"""knot — spec builder.

Single-file dataclass-based spec construction. Bodies and view
predicates are authored through the semantic builder (``knot.expr``);
no raw SQL strings cross knot's user surface. Slot types come from
``knot.types`` (the one canonical surface — ``types.TEXT``,
``types.ARRAY(types.TEXT)``, ``types.FK(other_class)``).

Under the hood the type AST is one of:

  - ``Primitive``           — enum of the closed primitive set
  - ``Array(of=…)``         — homogeneous container over another TypeExpression
  - ``ClassRef(target=…)``  — FK to another class (stored as canonical_id)

These are internal — callers go through ``knot.types`` rather than
constructing them directly. All entities are plain
``@dataclass(slots=True)`` records so a future Java port maps 1:1 to
``record`` / ``sealed interface`` / ``enum``.

Validation happens at two levels:

  - Entity-local: ``__post_init__`` rejects empty names, out-of-range
    accuracy, and (via ``StrEnum`` coercion) unknown ``ClassKind`` /
    ``Severity`` values.
  - Cross-entity: ``Spec.validate()`` returns a list of well-formedness
    errors (orphan references, missing identifier slots, duplicate
    names, etc.). Empty list = valid.

Body validation (typos in slot references) is caught at construction
time by the builder: ``movie.col.nonexistent`` raises ``KeyError``
before the expression tree is built. No post-hoc SQL parsing required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from knot.expr import CountRel, Exists, Expr, FkRef, Ref
from knot.select import Query

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


@dataclass(slots=True)
class Array:
    """Homogeneous array of another ``TypeExpression``."""

    of: TypeExpression

    def __str__(self) -> str:
        return f"array<{self.of}>"


@dataclass(slots=True)
class ClassRef:
    """FK reference to another class — stored as the target's canonical_id."""

    target: OntologyClass

    def __str__(self) -> str:
        return self.target.name


TypeExpression = Primitive | Array | ClassRef


def _coerce_type(t: TypeExpression) -> TypeExpression:
    """Validate a slot type. Canonical type API only — use the ``knot.types``
    module (``types.TEXT``, ``types.ARRAY(types.TEXT)``, ``types.FK(cls)``).
    No string shorthand, no enum direct access."""
    if isinstance(t, (Primitive, Array, ClassRef)):
        return t
    raise TypeError(
        f"Slot type must be a value from knot.types (TEXT/INTEGER/FLOAT/"
        f"BOOLEAN/DATE/TIMESTAMP/ARRAY(...)/FK(...)); got {type(t).__name__}"
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


@dataclass(slots=True)
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
# Builder helper — col.<slot_name> accessor for spec-relative slot refs
# ---------------------------------------------------------------------------


class _ColAccess:
    """``cls.col.year`` returns a ``Ref`` for primitive/array slots and
    an ``FkRef`` for FK slots — the latter is navigable for transparent
    chained access (``Movie.col.director.name``). Typos raise
    ``KeyError`` at attribute time (no post-hoc validation needed)."""

    __slots__ = ("_cls",)

    def __init__(self, cls: OntologyClass):
        object.__setattr__(self, "_cls", cls)

    def __getattr__(self, name: str) -> Ref | FkRef:
        # Guard pydantic / repr internals.
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __getitem__(self, name: str) -> Ref | FkRef:
        cls = object.__getattribute__(self, "_cls")
        slot = cls.get_slot(name)
        if isinstance(slot.type, ClassRef):
            return FkRef(
                class_name=cls.name,
                slot_name=name,
                target_class_name=slot.type.target.name,
            )
        return Ref(class_name=cls.name, slot_name=name)


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


@dataclass(slots=True)
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
        type: TypeExpression,
        *,
        identifier: bool = False,
        required: bool = False,
        description: str | None = None,
    ) -> Slot:
        if any(s.name == name for s in self.slots):
            raise ValueError(f"OntologyClass {self.name!r} already has a slot named {name!r}")
        s = Slot(
            name=name,
            type=_coerce_type(type),
            identifier=identifier,
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

    # ------------------------------------------------------------------
    # Builder methods — produce Expr objects for use in constraint
    # bodies / VirtualClass predicates.
    # ------------------------------------------------------------------

    @property
    def col(self) -> _ColAccess:
        """``movie.col.year`` → ``Ref(class_name='Movie', slot_name='year')``.

        ``movie.col[name]`` is the long form, useful for dynamic names.
        Either form raises ``KeyError`` on construction if the slot
        doesn't exist (including through is_a / mixin inheritance)."""
        return _ColAccess(self)

    def has_any(
        self,
        other: OntologyClass,
        *,
        where: Expr | None = None,
        via: str | None = None,
        **slot_eq: Any,
    ) -> Exists:
        """∃ row in ``other`` linked back to ``self`` via an FK on
        ``other``, optionally constrained by per-slot equality kwargs
        and an additional ``where`` predicate.

        The back-pointing FK is inferred from ``other.effective_slots()``
        — exactly one slot must be a ``ClassRef`` to ``self``. If
        multiple FKs match, pass ``via=<fk_slot_name>`` to disambiguate.
        """
        fk_slot = _infer_back_fk(other, target=self, via=via)
        ident = self.identifier_slot()
        return Exists(
            other_class_name=other.name,
            fk_slot_name=fk_slot.name,
            primary_class_name=self.name,
            primary_identifier=ident.name,
            where=_combine_where(other, where, slot_eq),
            negated=False,
        )

    def has_none(
        self,
        other: OntologyClass,
        *,
        where: Expr | None = None,
        via: str | None = None,
        **slot_eq: Any,
    ) -> Exists:
        """``NOT EXISTS`` form of ``has_any``."""
        ex = self.has_any(other, where=where, via=via, **slot_eq)
        return Exists(
            other_class_name=ex.other_class_name,
            fk_slot_name=ex.fk_slot_name,
            primary_class_name=ex.primary_class_name,
            primary_identifier=ex.primary_identifier,
            where=ex.where,
            negated=True,
        )

    def has_count(
        self,
        other: OntologyClass,
        *,
        where: Expr | None = None,
        via: str | None = None,
        **slot_eq: Any,
    ) -> CountRel:
        """``(SELECT COUNT(*) …)`` — value-expression comparable with
        ``>``/``>=``/``==``/etc.:
        ``movie.has_count(credit) >= 3``."""
        fk_slot = _infer_back_fk(other, target=self, via=via)
        ident = self.identifier_slot()
        return CountRel(
            other_class_name=other.name,
            fk_slot_name=fk_slot.name,
            primary_class_name=self.name,
            primary_identifier=ident.name,
            where=_combine_where(other, where, slot_eq),
        )

    # ------------------------------------------------------------------
    # Read substrate — query entry points. Each returns a fresh ``Query``;
    # chain further with ``.where()`` / ``.order_by()`` / ``.limit()`` /
    # ``.offset()`` / ``.select()`` on the returned ``Query``.
    # ------------------------------------------------------------------

    def where(self, predicate: Expr) -> Query:
        return Query(class_name=self.name).where(predicate)

    def order_by(self, ref: Expr, direction: str = "asc") -> Query:
        return Query(class_name=self.name).order_by(ref, direction)

    def limit(self, n: int) -> Query:
        return Query(class_name=self.name).limit(n)

    def offset(self, n: int) -> Query:
        return Query(class_name=self.name).offset(n)

    def select(self, *refs: Expr) -> Query:
        return Query(class_name=self.name).select(*refs)


def _infer_back_fk(
    other: OntologyClass,
    *,
    target: OntologyClass,
    via: str | None,
) -> Slot:
    """Find the slot on ``other`` whose ``type`` is a ``ClassRef`` to
    ``target``. If ``via`` is given, require that specific slot."""
    candidates = [
        sl
        for sl in other.effective_slots()
        if isinstance(sl.type, ClassRef) and sl.type.target is target
    ]
    if via is not None:
        chosen = [sl for sl in candidates if sl.name == via]
        if not chosen:
            raise ValueError(f"{other.name}.{via} is not a FK to {target.name!r}")
        return chosen[0]
    if not candidates:
        raise ValueError(f"class {other.name!r} has no FK back to {target.name!r}")
    if len(candidates) > 1:
        names = ", ".join(c.name for c in candidates)
        raise ValueError(
            f"class {other.name!r} has multiple FKs to {target.name!r} "
            f"({names}); disambiguate with via=<slot_name>"
        )
    return candidates[0]


def _combine_where(
    other: OntologyClass,
    where: Expr | None,
    slot_eq: dict[str, Any],
) -> Expr | None:
    """Combine the user's ``where=`` predicate with ``slot=value`` kwargs
    (validated against ``other``'s effective slots) into a single Expr."""
    clauses: list[Expr] = []
    for slot_name, value in slot_eq.items():
        # Validates the slot exists; raises KeyError on typo.
        other.get_slot(slot_name)
        clauses.append(Ref(class_name=other.name, slot_name=slot_name) == value)
    if where is not None:
        clauses.append(where)
    if not clauses:
        return None
    combined = clauses[0]
    for c in clauses[1:]:
        combined = combined & c
    return combined


@dataclass(slots=True)
class VirtualClass:
    """A virtual class — materialized as a SQL view over an is_a parent
    table, rows selected by the ``definition`` predicate. The definition
    is an ``Expr`` produced by the semantic builder, not raw SQL."""

    name: str
    is_a: OntologyClass
    definition: Expr
    description: str | None = None

    def __post_init__(self) -> None:
        _check_name("VirtualClass", self.name)
        if not isinstance(self.definition, Expr):
            raise TypeError(
                f"VirtualClass {self.name!r}.definition must be an Expr "
                f"(use the builder: e.g. movie.has_any(credit, ...)); "
                f"got {type(self.definition).__name__}"
            )


# ---------------------------------------------------------------------------
# Constraint / Source / SourceBinding
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Constraint:
    """Cross-row / cross-class invariant — body is an ``Expr`` from
    the semantic builder, not raw SQL."""

    name: str
    primary: OntologyClass
    body: Expr
    severity: Severity = Severity.ERROR
    message: str | None = None

    def __post_init__(self) -> None:
        _check_name("Constraint", self.name)
        if not isinstance(self.body, Expr):
            raise TypeError(
                f"Constraint {self.name!r}.body must be an Expr "
                f"(use the builder: e.g. movie.col.year >= 1888); "
                f"got {type(self.body).__name__}"
            )
        if isinstance(self.severity, str):
            try:
                self.severity = Severity(self.severity)
            except ValueError as e:
                raise ValueError(
                    f"Constraint.severity must be one of "
                    f"{[s.value for s in Severity]}; got {self.severity!r}"
                ) from e


@dataclass(slots=True)
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


# Reserved synthetic source for human-curated overrides. The resolver
# treats this like any other source — high accuracy in source_accuracy
# is what makes corrections "win" the per-slot argmax tie-break.
CORRECTIONS_SOURCE_NAME: str = "_user_corrections"


@dataclass(frozen=True, slots=True)
class SourceMap:
    """Per-slot projection from a raw source row to a class slot value.

    ``uses`` declares the raw source field names the SQL expression
    references — the batch write emitter plumbs only these fields
    through. ``sql`` is the postgres expression evaluated server-side,
    referencing the raw field names by bare identifier.

    For the common case of passing a single raw column through verbatim,
    use ``SourceMap.passthrough("release_year")`` or pass a bare string
    to ``binding.map(year="release_year")`` and it coerces.
    """

    uses: tuple[str, ...]
    sql: str

    def __post_init__(self) -> None:
        # Tolerate ``uses=["a", "b"]`` at construction even though the
        # field's declared type is tuple — coerce here.
        if not isinstance(self.uses, tuple):
            object.__setattr__(self, "uses", tuple(self.uses))

    @classmethod
    def passthrough(cls, raw_field: str) -> SourceMap:
        """Trivial-case shorthand: ``slot = raw_field`` with no SQL transform."""
        return cls(uses=(raw_field,), sql=raw_field)


def _coerce_source_map(v: Any) -> SourceMap:
    if isinstance(v, SourceMap):
        return v
    if isinstance(v, str):
        return SourceMap.passthrough(v)
    raise TypeError(
        f"SourceBinding.map value must be a SourceMap or str (raw column "
        f"name shorthand); got {type(v).__name__}"
    )


@dataclass(slots=True)
class SourceBinding:
    """(Source, OntologyClass) binding with per-slot projections.

    ``accuracy`` is the spec author's guess at the fraction of past claims
    this source got right (0.0 - 1.0). The resolver derives a Beta prior
    from ``accuracy`` and ``BINDING_PRIOR_STRENGTH``; see ``beta_prior``.

    ``mappings`` maps slot names to ``SourceMap`` records that carry
    both the raw fields used and the SQL expression evaluated.
    """

    source: Source
    class_: OntologyClass
    identifier_slot: Slot
    accuracy: float = 0.67
    mappings: dict[str, SourceMap] = field(default_factory=dict)
    description: str | None = None

    def __post_init__(self) -> None:
        if not (0.0 <= self.accuracy <= 1.0):
            raise ValueError(f"SourceBinding accuracy must be in [0, 1]; got {self.accuracy}")

    def map(self, **mappings: Any) -> SourceBinding:
        """Add slot mappings. Values may be ``SourceMap`` instances or
        bare strings (interpreted as the raw column name to pass through)."""
        for slot_name, value in mappings.items():
            self.mappings[slot_name] = _coerce_source_map(value)
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


@dataclass(slots=True)
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
        where: Expr,
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
        body: Expr,
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
        if name == CORRECTIONS_SOURCE_NAME:
            raise ValueError(
                f"{name!r} is a reserved source name — use "
                f"spec.enable_corrections() instead of add_source()"
            )
        if any(s.name == name for s in self.sources):
            raise ValueError(f"Spec already has a source named {name!r}")
        s = Source(name=name, description=description)
        self.sources.append(s)
        return s

    def enable_corrections(
        self,
        *,
        accuracy: float = 0.99,
        description: str | None = "human overrides",
    ) -> Source:
        """Register the ``_user_corrections`` synthetic source and bind
        it to every concrete ``OntologyClass`` in the spec.

        High default accuracy (0.99) means corrections override declared
        sources at the resolver tie-break. Operators can tune via
        ``UPDATE source_accuracy SET accuracy = … WHERE source_name =
        '_user_corrections' AND class_name = '<X>'`` without touching
        the spec.

        Idempotent: calling again is a no-op if the source already
        exists. Returns the (possibly pre-existing) ``Source`` object.
        """
        existing = next(
            (s for s in self.sources if s.name == CORRECTIONS_SOURCE_NAME),
            None,
        )
        if existing is not None:
            return existing
        source = Source(name=CORRECTIONS_SOURCE_NAME, description=description)
        self.sources.append(source)
        for cls in self.concrete_classes():
            self.bind(
                source,
                cls,
                identifier=cls.identifier_slot(),
                accuracy=accuracy,
            )
        return source

    def corrections_binding_for(self, cls: OntologyClass) -> SourceBinding:
        """The ``_user_corrections`` binding for ``cls``. Convenience
        for the write path. Raises ``KeyError`` if corrections aren't
        enabled or ``cls`` isn't bound."""
        for b in self.source_bindings:
            if b.source.name == CORRECTIONS_SOURCE_NAME and b.class_ is cls:
                return b
        raise KeyError(
            f"no _user_corrections binding for class {cls.name!r} — "
            f"call spec.enable_corrections() first"
        )

    def bind(
        self,
        source: Source,
        class_: OntologyClass,
        *,
        identifier: Slot,
        accuracy: float = 0.67,
        description: str | None = None,
    ) -> SourceBinding:
        if any(b.source is source and b.class_ is class_ for b in self.source_bindings):
            raise ValueError(
                f"Spec already has a binding for source {source.name!r} → class {class_.name!r}"
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

    def concrete_classes(self) -> list[OntologyClass]:
        """Concrete ``OntologyClass`` entries — skips abstract and
        ``VirtualClass``. Used everywhere the emitters loop over
        "classes that materialize a table"."""
        return [
            c for c in self.classes if isinstance(c, OntologyClass) and c.kind == ClassKind.CONCRETE
        ]

    def virtual_classes(self) -> list[VirtualClass]:
        """``VirtualClass`` entries — backed by a view, not a table."""
        return [c for c in self.classes if isinstance(c, VirtualClass)]

    def class_by_name(self, name: str) -> OntologyClass | VirtualClass:
        for c in self.classes:
            if c.name == name:
                return c
        raise KeyError(f"Spec has no class named {name!r}")

    def validate(self) -> list[str]:
        """Cross-entity well-formedness checks.

        Returns a list of error messages; empty list means the spec is
        valid. Local entity checks (name shape, accuracy bounds, enum
        values, body Expr typing) have already run in each entity's
        ``__post_init__``. Body slot-ref typos are caught at expression
        construction time by ``movie.col.<slot>`` raising ``KeyError``.
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
            match c:
                case OntologyClass():
                    if c.is_a is not None and c.is_a.name not in concrete_or_abstract:
                        errs.append(f"class {c.name!r}.is_a → {c.is_a.name!r}: not in spec")
                    for m in c.mixins:
                        if m.name not in concrete_or_abstract:
                            errs.append(f"class {c.name!r} mixin {m.name!r}: not in spec")
                    # Slot name uniqueness within the class
                    seen_slots: set[str] = set()
                    for sl in c.slots:
                        if sl.name in seen_slots:
                            errs.append(f"class {c.name!r} has duplicate slot {sl.name!r}")
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
                            errs.append(f"concrete class {c.name!r} has no identifier slot")
                        elif len(ids) > 1:
                            names = ", ".join(s.name for s in ids)
                            errs.append(
                                f"concrete class {c.name!r} has multiple identifier slots: {names}"
                            )
                case VirtualClass():
                    if c.is_a.name not in concrete_or_abstract:
                        errs.append(f"virtual class {c.name!r}.is_a → {c.is_a.name!r}: not in spec")

        # Constraint references
        constraint_names: set[str] = set()
        for c in self.constraints:
            if c.name in constraint_names:
                errs.append(f"duplicate constraint name {c.name!r}")
            constraint_names.add(c.name)
            if c.primary.name not in concrete_or_abstract:
                errs.append(f"constraint {c.name!r}.primary → {c.primary.name!r}: not in spec")

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
                errs.append(f"duplicate binding source={b.source.name!r} class={b.class_.name!r}")
            binding_keys.add(key)
            if b.source.name not in sources_by_name:
                errs.append(
                    f"binding source={b.source.name!r} class={b.class_.name!r}: source not in spec"
                )
            if b.class_.name not in concrete_or_abstract:
                errs.append(
                    f"binding source={b.source.name!r} class={b.class_.name!r}: class not in spec"
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

        # is_a / mixin cycle detection
        for c in self.classes:
            if isinstance(c, OntologyClass) and _participates_in_cycle(c):
                errs.append(f"class {c.name!r} participates in an is_a / mixin cycle")

        return errs

    def validate_strict(self) -> None:
        """Like ``validate`` but raises ``SpecError`` on any failure."""
        errs = self.validate()
        if errs:
            raise SpecError("Spec failed validation:\n  - " + "\n  - ".join(errs))


class SpecError(ValueError):
    """Raised by ``Spec.validate_strict`` when well-formedness fails."""


# ---------------------------------------------------------------------------
# Validation helpers (module-level so they can be unit-tested in isolation
# and don't pollute Spec's instance namespace)
# ---------------------------------------------------------------------------


def _participates_in_cycle(cls: OntologyClass) -> bool:
    """True iff ``cls`` would appear in its own is_a / mixin chain.

    Walks parents BFS without ``chain()``'s dedup; returns True the
    moment we encounter ``cls`` itself in the parent set."""
    seen: set[int] = set()
    queue: list[OntologyClass] = []
    if cls.is_a is not None:
        queue.append(cls.is_a)
    queue.extend(cls.mixins)
    while queue:
        cur = queue.pop(0)
        if cur is cls:
            return True
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        if cur.is_a is not None:
            queue.append(cur.is_a)
        queue.extend(cur.mixins)
    return False


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
    "SourceMap",
    "BINDING_PRIOR_STRENGTH",
    "CORRECTIONS_SOURCE_NAME",
    "Spec",
    "SpecError",
]
