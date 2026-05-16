"""knot — spec builder.

Single-file dataclass-based spec construction. Bodies and view
predicates are authored through the semantic builder
(``knot.ast.expr``); no raw SQL strings cross knot's user surface.
Slot types come from ``knot.ast.types`` (the one canonical surface —
``types.TEXT``, ``types.ARRAY(types.TEXT)``, FK by passing an
``OntologyClass`` directly to ``slot()``).

Under the hood the type AST is one of:

  - ``Primitive``           — enum of the closed primitive set
  - ``Array(of=…)``         — homogeneous container over another TypeExpression
  - ``ClassRef(target=…)``  — FK to another class (stored as canonical_id)

These are internal — callers go through ``knot.ast.types`` rather than
constructing them directly. All entities are plain
``@dataclass(slots=True)`` records so a future Java port maps 1:1 to
``record`` / ``sealed interface`` / ``enum``.

Validation happens at two levels:

  - Entity-local: ``__post_init__`` rejects empty names and (via
    ``StrEnum`` coercion) unknown ``ClassKind`` / ``Severity`` values.
  - Cross-entity: ``Spec.validate()`` raises ``SpecError`` if the spec
    has any well-formedness errors (orphan references, missing
    identifier slots, duplicate names, etc.). The façade methods
    (``init_sql``, ``Query.sql`` etc.) call it automatically.

Body validation (typos in slot references) is caught at construction
time by the builder: ``movie.col.nonexistent`` raises ``KeyError``
before the expression tree is built. No post-hoc SQL parsing required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from knot.ast.expr import CountRel, Exists, Expr, FkRef, Ref
from knot.ast.select import Query
from knot.ast.types import ClassRef, TypeExpression, _coerce_type

# ---------------------------------------------------------------------------
# Enums (class-shape + constraint-severity — the type-expression enum lives
# in ``knot.ast.types`` since it's part of the AST primitives)
# ---------------------------------------------------------------------------


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
    _spec: Spec | None = field(default=None, repr=False, compare=False)

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

    # Layer-targeted query entry points. Every read declares its
    # layer explicitly — no silent default.
    #
    #   cls.resolved          argmax view, one row per canonical_id,
    #                         slot values are the resolver's winners
    #   cls.all_sources       per-source provenance, one row per
    #                         canonical_id, slot columns are jsonb
    #                         keyed by source_name with {value, weight}
    #   cls.from_source(s)    one source's claims about this class —
    #                         raw bindings, scoped to source ``s``
    #
    # All return ``Query`` and chain the same fluent surface
    # (``.where`` / ``.order_by`` / ``.limit`` / ``.offset`` /
    # ``.select`` / ``.sql``). The cross-source raw bindings stream
    # is intentionally unexposed — internal / admin concern.

    def _query(self, target_suffix: str) -> Query:
        if self.kind != ClassKind.CONCRETE:
            raise ValueError(
                f"OntologyClass {self.name!r} is {self.kind.value!r}; "
                f"only concrete classes have a {target_suffix} relation"
            )
        return Query(
            class_name=self.name,
            target_suffix=target_suffix,
            _spec=self._spec,
        )

    @property
    def resolved(self) -> Query:
        """Query against ``<class>_resolved`` — the resolver's
        argmax view, one row per canonical_id with the highest-weight
        non-null value per slot. The user-facing "current state"
        read shape."""
        return self._query(target_suffix="_resolved")

    @property
    def all_sources(self) -> Query:
        """Query against ``<class>_all_sources`` — per-source
        provenance view, one row per canonical_id with each slot
        column as a jsonb of ``{source_name: {value, weight}}``.
        Slot columns project as jsonb here, not their underlying
        scalar type."""
        return self._query(target_suffix="_all_sources")

    def from_source(self, source: Source) -> Query:
        """Query one source's claims about this class. Returns a
        Query against ``<class>_bindings`` filtered to
        ``source_name = '<name>'``. The bindings layer is otherwise
        unexposed; use this when you want to inspect what a specific
        source has said (e.g. pre-ER raw rows, or per-source debug)."""
        from knot.ast.expr import Raw

        # source.name is constrained to [A-Za-z_][A-Za-z0-9_]* by
        # ``_check_name`` at Source.__post_init__, so quotes can't
        # appear here. Defense-in-depth escape kept.
        src = source.name.replace("'", "''")
        return self._query(target_suffix="_bindings").where(
            Raw(f"source_name = '{src}'")
        )

    # ------------------------------------------------------------------
    # Class-anchored builder methods — constraints, virtuals, corrections.
    # Spec is the registrar (add_class, add_source); per-entity facts live
    # on the entity they describe.
    # ------------------------------------------------------------------

    def add_constraint(
        self,
        name: str,
        body: Expr,
        *,
        severity: Severity | str = Severity.ERROR,
        message: str | None = None,
    ) -> Constraint:
        """Attach a constraint whose primary class is this one. The
        constraint body is evaluated against this class's resolved view
        at validation time."""
        if self._spec is None:
            raise RuntimeError(
                f"OntologyClass {self.name!r} not attached to a Spec — "
                f"create via spec.add_class() rather than constructing directly"
            )
        if any(c.name == name for c in self._spec.constraints):
            raise ValueError(f"Spec already has a constraint named {name!r}")
        c = Constraint(
            name=name,
            primary=self,
            body=body,
            severity=Severity(severity) if isinstance(severity, str) else severity,
            message=message,
        )
        self._spec.constraints.append(c)
        return c

    def add_virtual(
        self,
        name: str,
        *,
        where: Expr,
        description: str | None = None,
    ) -> VirtualClass:
        """Define a virtual subclass — rows of this class that satisfy
        ``where``. Materialized as a SQL view at deploy time."""
        if self._spec is None:
            raise RuntimeError(
                f"OntologyClass {self.name!r} not attached to a Spec — "
                f"create via spec.add_class() rather than constructing directly"
            )
        self._spec._check_unique_class_name(name)
        vc = VirtualClass(
            name=name, is_a=self, definition=where, description=description
        )
        self._spec.classes.append(vc)
        return vc

    def corrections_binding(self) -> SourceBinding:
        """Return the ``_user_corrections`` binding for this class.
        Raises ``KeyError`` if corrections aren't enabled."""
        if self._spec is None:
            raise RuntimeError(f"OntologyClass {self.name!r} not attached to a Spec")
        for b in self._spec.source_bindings:
            if b.source.name == CORRECTIONS_SOURCE_NAME and b.class_ is self:
                return b
        raise KeyError(
            f"no _user_corrections binding for class {self.name!r} — "
            f"call spec.enable_corrections() first"
        )


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
    """A named external system (imdb, tmdb).

    Sources are created via ``spec.add_source(...)``, which sets the
    back-reference ``_spec`` so ``source.bind(cls, ...)`` can register
    the resulting ``SourceBinding`` on its owning spec."""

    name: str
    description: str | None = None
    _spec: Spec | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        _check_name("Source", self.name)

    def bind(
        self,
        cls: OntologyClass,
        *,
        description: str | None = None,
    ) -> SourceBinding:
        """Create a binding from this source to ``cls`` and register it
        on the owning spec.

        Weight starts at ``DEFAULT_WEIGHT`` for every slot. To set it,
        call ``binding.set_default_weight(...)`` or
        ``binding.set_weight(slot, value)`` on the returned binding —
        weight is the resolver's argmax key, declared separately from
        "what this source publishes". The score itself is opaque to
        knot: any float will do, the higher one wins."""
        if self._spec is None:
            raise RuntimeError(
                f"Source {self.name!r} not attached to a Spec — create via "
                f"spec.add_source() rather than constructing directly"
            )
        if any(
            b.source is self and b.class_ is cls for b in self._spec.source_bindings
        ):
            raise ValueError(
                f"Spec already has a binding for {self.name!r} → {cls.name!r}"
            )
        b = SourceBinding(source=self, class_=cls, description=description)
        self._spec.source_bindings.append(b)
        return b


# Reserved synthetic source for human-curated overrides. The resolver
# treats this like any other source — a high weight in source_weight
# is what makes corrections "win" the per-slot argmax tie-break.
CORRECTIONS_SOURCE_NAME: str = "_user_corrections"

# Default per-slot weight applied to a fresh binding. Opaque to knot;
# higher wins. Operators tune by calling
# ``binding.set_default_weight(...)`` / ``binding.set_weight(slot,
# value)`` at spec build time, or by ``UPDATE`` on ``source_weight``
# at runtime. Module constant so adapters can rebind it before import.
DEFAULT_WEIGHT: float = 1.0


@dataclass(slots=True)
class SlotMapping:
    """Per-slot mapping in a ``SourceBinding``.

    Declares how one class slot's value is computed from raw source
    fields:

    - ``class_slot``  — the slot name on the ``OntologyClass``.
    - ``source_slot`` — tuple of raw source field names this mapping
      references. Coerced from a single string.
    - ``sql``         — optional postgres expression evaluated
      server-side over the ``source_slot`` fields. ``None`` means
      passthrough of ``source_slot[0]``.

    Weight is declared separately on the owning ``SourceBinding`` via
    ``set_default_weight`` / ``set_weight``; it's not part of the
    ingest-mapping shape.
    """

    class_slot: str
    source_slot: tuple[str, ...]
    sql: str | None = None

    def __post_init__(self) -> None:
        # Tolerate ``source_slot=`` passed as bare string or any iterable
        # at construction time; coerce to the declared tuple shape. The
        # field is typed as tuple, so mypy can't see the bare-string
        # case at the type level — runtime check is real, ignore the
        # ``unreachable`` flag.
        raw: Any = self.source_slot
        if isinstance(raw, str):
            self.source_slot = (raw,)
        else:
            self.source_slot = tuple(raw)
        if not self.source_slot:
            raise ValueError(
                f"SlotMapping for {self.class_slot!r} requires at least one source_slot"
            )

    @property
    def effective_sql(self) -> str:
        """Postgres expression to evaluate — explicit ``sql`` if set,
        otherwise bare ``source_slot[0]`` passthrough."""
        return self.sql if self.sql is not None else self.source_slot[0]


@dataclass(slots=True)
class SourceBinding:
    """(Source, OntologyClass) binding — what one source publishes
    about one class, and how its raw fields map onto the class's slots.

    Weight is a separate concern, carried on this same binding but
    set via ``set_default_weight`` / ``set_weight`` (not via
    ``bind()`` / ``slot()`` kwargs). Weights are opaque to knot — any
    float will do; the resolver picks per-slot winners by argmax over
    the live ``source_weight`` table, which the spec seeds at deploy
    time from these values (INSERT-only — once a row exists,
    operator's runtime tuning is authoritative). Calibration /
    probability semantics belong to the external algorithm that
    produced the numbers, not to knot.

    ``slot_mappings`` describes ingest mechanics (which source field
    feeds which class slot, optional SQL transform). Unmapped class
    slots are implicit passthroughs of the same name with no SQL.
    """

    source: Source
    class_: OntologyClass
    default_weight: float = DEFAULT_WEIGHT
    slot_mappings: dict[str, SlotMapping] = field(default_factory=dict)
    slot_weights: dict[str, float] = field(default_factory=dict)
    description: str | None = None

    @property
    def identifier_slot(self) -> Slot:
        """The class's identifier slot. Carried as a property (not a
        field) because every binding for a class shares the same one."""
        return self.class_.identifier_slot()

    # ------------------------------------------------------------------
    # Ingest-mapping declaration (no weight here — see set_weight below).
    # ------------------------------------------------------------------

    def slot(
        self,
        *,
        class_slot: str,
        source_slot: str | tuple[str, ...] | None = None,
        sql: str | None = None,
    ) -> SourceBinding:
        """Declare an explicit mapping for one class slot.

        - ``class_slot`` is required.
        - ``source_slot`` defaults to ``class_slot`` (same name). Pass a
          tuple of strings when ``sql`` references multiple raw fields.
        - ``sql`` is the optional postgres expression over those fields.

        Weight is set separately via ``set_default_weight`` /
        ``set_weight`` — those are the only knobs that touch weight.
        """
        # Validate the class slot exists (raises KeyError on typo).
        self.class_.get_slot(class_slot)
        if source_slot is None:
            source_slot = (class_slot,)
        elif isinstance(source_slot, str):
            source_slot = (source_slot,)
        else:
            source_slot = tuple(source_slot)
        self.slot_mappings[class_slot] = SlotMapping(
            class_slot=class_slot, source_slot=source_slot, sql=sql
        )
        return self

    def effective_mapping(self, class_slot_name: str) -> SlotMapping:
        """Return the effective ``SlotMapping`` for a class slot —
        the explicit one if declared, otherwise an implicit passthrough
        (same name, no SQL transform)."""
        if class_slot_name in self.slot_mappings:
            return self.slot_mappings[class_slot_name]
        return SlotMapping(
            class_slot=class_slot_name, source_slot=(class_slot_name,), sql=None
        )

    # ------------------------------------------------------------------
    # Weight API — separate concern from ingest mapping. Weight is the
    # resolver's argmax key; mapping is "how to project the row".
    # ------------------------------------------------------------------

    def set_default_weight(self, value: float) -> SourceBinding:
        """Default weight for any non-identifier slot that doesn't have
        a per-slot override. Applies to every slot of this binding
        unless overridden via ``set_weight(slot, value)``.

        Weights are opaque floats — knot does no calibration check,
        the higher value wins."""
        self.default_weight = value
        return self

    def set_weight(self, slot: str, value: float) -> SourceBinding:
        """Per-slot weight override for one non-identifier slot.
        Replaces the default for this slot only. Rejected on the
        identifier slot (identity is not argmax-resolved)."""
        slot_obj = self.class_.get_slot(slot)
        if slot_obj is self.identifier_slot:
            raise ValueError(
                f"weight is meaningless on the identifier slot "
                f"{slot!r} — identity is not argmax-resolved"
            )
        self.slot_weights[slot] = value
        return self

    def weight_for(self, class_slot_name: str) -> float:
        """Effective weight for ``class_slot_name``: the slot's explicit
        value (from ``set_weight``) if set, otherwise the binding's
        ``default_weight``."""
        if class_slot_name in self.slot_weights:
            return self.slot_weights[class_slot_name]
        return self.default_weight

    # ------------------------------------------------------------------
    # Runtime helpers — ingest, ER. Methods on the binding because the
    # binding pins (source, class); the user shouldn't have to repeat
    # those at the call site.
    # ------------------------------------------------------------------

    def write_sql(
        self,
        *,
        schema: str = "knot_data",
        bindings_suffix: str = "_bindings",
    ) -> tuple[str, str]:
        """Return ``(close_out_sql, insert_sql)`` for this binding's
        SCD2 write. Both reference a single ``%(rows)s::jsonb``
        parameter — the host's connector binds the rows.

        Run both statements in one transaction::

            close_out, insert = binding.write_sql()
            with pg.transaction(), pg.cursor() as cur:
                cur.execute(close_out, {"rows": rows})
                cur.execute(insert,    {"rows": rows})

        Multi-binding atomic write: call ``binding.write_sql()`` per
        binding, run all the statements in one ``pg.transaction()``.
        Constraint enforcement is the host's concern — run
        ``spec.emit_validation()`` after the write inside the same
        transaction and roll back if any return rows.
        """
        if self.source._spec is None:
            raise RuntimeError(
                f"binding {self.source.name!r} → {self.class_.name!r} "
                f"is not attached to a Spec"
            )
        self.source._spec.validate()
        from knot.compile.write import emit_binding_write_sql

        return emit_binding_write_sql(
            self, schema=schema, bindings_suffix=bindings_suffix
        )

    def assign_canonical_sql(
        self,
        *,
        schema: str = "knot_data",
        bindings_suffix: str = "_bindings",
    ) -> str:
        """Return the SQL template that assigns a ``canonical_id`` to
        one unresolved binding row. Three named placeholders —
        ``%(canonical_id)s``, ``%(source_identifier)s``,
        ``%(er_metadata)s`` (None to leave unchanged, JSON string to
        set). Host binds via ``cur.execute(sql, params)``. See
        ``knot.compile.write.emit_assign_canonical_sql``."""
        from knot.compile.write import emit_assign_canonical_sql

        return emit_assign_canonical_sql(
            self, schema=schema, bindings_suffix=bindings_suffix
        )

    def recanonicalize_sql(
        self,
        *,
        schema: str = "knot_data",
        bindings_suffix: str = "_bindings",
    ) -> str:
        """Return the SQL template that reassigns a binding row's
        ``canonical_id``, preserving history via SCD2. Three named
        placeholders — ``%(new_canonical_id)s``,
        ``%(source_identifier)s``, ``%(er_metadata)s`` (None inherits
        the closed row's metadata; JSON string overrides). See
        ``knot.compile.write.emit_recanonicalize_sql``."""
        from knot.compile.write import emit_recanonicalize_sql

        return emit_recanonicalize_sql(
            self, schema=schema, bindings_suffix=bindings_suffix
        )

    def close_out_sql(
        self,
        *,
        schema: str = "knot_data",
        bindings_suffix: str = "_bindings",
    ) -> str:
        """Return the SQL template that closes out one open binding
        row without inserting a replacement. Two named placeholders
        — ``%(canonical_id)s``, ``%(source_identifier)s``. Used to
        retract a source's claim. See
        ``knot.compile.write.emit_close_out_sql``."""
        from knot.compile.write import emit_close_out_sql

        return emit_close_out_sql(self, schema=schema, bindings_suffix=bindings_suffix)


# ---------------------------------------------------------------------------
# Spec root + builder
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Spec:
    """Ontology declaration root.

    The identifier slot is spec-level, not per-class: ``add_class()``
    auto-adds a slot named ``identifier_slot_name`` of
    ``identifier_type`` to every concrete class (and to abstract
    classes that don't inherit one). ``identifier_slot_name`` is
    required — knot won't pick a name for you. ``identifier_type``
    defaults to ``types.TEXT`` since string keys are nearly universal;
    pass ``types.INTEGER`` or another primitive when needed.

    No per-class identifier override — if a class genuinely needs a
    different identifier shape, that's outside knot's single-team
    posture. No spec-level ``id`` or ``version`` either: the deploy
    schema (``init_sql(schema=…)``) is the only structural name knot
    cares about. If you want to label the spec for your team, do it
    in the codebase (filename, module name, repo).
    """

    identifier_slot_name: str  # required — no default; e.g. "canonical_id"
    classes: list[OntologyClass | VirtualClass] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    source_bindings: list[SourceBinding] = field(default_factory=list)
    constraints: list[Constraint] = field(default_factory=list)
    # ``None`` defaults to ``types.TEXT`` in __post_init__ (module-import
    # order means we can't reference Primitive.TEXT in the field default
    # cleanly).
    identifier_type: Any = None

    def __post_init__(self) -> None:
        _check_name("Spec.identifier_slot_name", self.identifier_slot_name)
        if self.identifier_type is None:
            from knot.ast.types import Primitive

            self.identifier_type = Primitive.TEXT

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
            _spec=self,
        )
        self.classes.append(cls)
        # Auto-add the spec's identifier slot — but only if no parent
        # in the is_a / mixin chain already contributes one. Skipping
        # the auto-add when inherited prevents the duplicate-identifier
        # error from validate().
        if not any(s.identifier for s in cls.effective_slots()):
            cls.slot(self.identifier_slot_name, self.identifier_type, identifier=True)
        return cls

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
        s = Source(name=name, description=description, _spec=self)
        self.sources.append(s)
        return s

    def enable_corrections(
        self,
        *,
        default_weight: float = 1e6,
        description: str | None = "human overrides",
    ) -> Source:
        """Register the ``_user_corrections`` synthetic source and bind
        it to every concrete ``OntologyClass`` in the spec.

        A large ``default_weight`` (1e6 by default) means corrections
        dominate the resolver's argmax against any declared source.
        Operators tune per-slot via ``UPDATE source_weight SET weight =
        … WHERE source_name = '_user_corrections' AND class_name =
        '<X>' AND slot_name = '<Y>'`` without touching the spec, or
        call ``cls.corrections_binding().set_weight(slot, value)`` to
        set per-slot values at spec build time.

        Idempotent: calling again is a no-op if the source already
        exists. Returns the (possibly pre-existing) ``Source`` object.
        """
        existing = next(
            (s for s in self.sources if s.name == CORRECTIONS_SOURCE_NAME),
            None,
        )
        if existing is not None:
            return existing
        source = Source(
            name=CORRECTIONS_SOURCE_NAME, description=description, _spec=self
        )
        self.sources.append(source)
        for cls in self.concrete_classes():
            binding = source.bind(cls)
            binding.set_default_weight(default_weight)
        return source

    def _check_unique_class_name(self, name: str) -> None:
        if any(c.name == name for c in self.classes):
            raise ValueError(f"Spec already has a class named {name!r}")

    # -- well-formedness validation --

    def concrete_classes(self) -> list[OntologyClass]:
        """Concrete ``OntologyClass`` entries — skips abstract and
        ``VirtualClass``. Used everywhere the emitters loop over
        "classes that materialize a table"."""
        return [
            c
            for c in self.classes
            if isinstance(c, OntologyClass) and c.kind == ClassKind.CONCRETE
        ]

    def virtual_classes(self) -> list[VirtualClass]:
        """``VirtualClass`` entries — backed by a view, not a table."""
        return [c for c in self.classes if isinstance(c, VirtualClass)]

    def class_by_name(self, name: str) -> OntologyClass | VirtualClass:
        for c in self.classes:
            if c.name == name:
                return c
        raise KeyError(f"Spec has no class named {name!r}")

    def _validation_errors(self) -> list[str]:
        """Internal: list cross-entity well-formedness errors.

        Local entity checks (name shape, enum values,
        body Expr typing) have already run in each entity's
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
                                f"concrete class {c.name!r} has multiple identifier slots: {names}"
                            )
                case VirtualClass():
                    if c.is_a.name not in concrete_or_abstract:
                        errs.append(
                            f"virtual class {c.name!r}.is_a → {c.is_a.name!r}: not in spec"
                        )

        # Constraint references
        constraint_names: set[str] = set()
        for con in self.constraints:
            if con.name in constraint_names:
                errs.append(f"duplicate constraint name {con.name!r}")
            constraint_names.add(con.name)
            if con.primary.name not in concrete_or_abstract:
                errs.append(
                    f"constraint {con.name!r}.primary → {con.primary.name!r}: not in spec"
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
                    f"duplicate binding source={b.source.name!r} class={b.class_.name!r}"
                )
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
                continue
            # slot_mappings keys must be slots on the bound class
            eff_names = {s.name for s in cls.effective_slots()}
            for slot_name in b.slot_mappings:
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

    def validate(self) -> None:
        """Cross-entity well-formedness check. Raises ``SpecError`` if
        the spec is malformed; returns ``None`` otherwise.

        Use ``pytest.raises(SpecError, match=…)`` to assert on specific
        errors in tests. For programmatic inspection of all errors,
        call ``self._validation_errors()`` directly."""
        errs = self._validation_errors()
        if errs:
            raise SpecError("Spec failed validation:\n  - " + "\n  - ".join(errs))

    # ------------------------------------------------------------------
    # Compile façade — ergonomic methods that delegate to ``knot.compile``.
    # Lazy imports preserve the spec → compile direction (compile modules
    # aren't loaded until a method fires).
    #
    # Every façade method calls ``validate()`` first; façade-mode never
    # compiles a known-invalid spec. The free functions in
    # ``knot.compile.*`` are validation-free — they're the back door for
    # adapters and tests that want to compile arbitrary inputs.
    #
    # Three methods, three concerns:
    #   - validate        — well-formedness check
    #   - init_sql        — schema deploy / migrate (one SQL script)
    #   - emit_validation — runtime constraint checks (per-rule SELECTs)
    # Per-entity runtime methods live on the entity:
    #   - ``query.sql(schema=…)``               read (Query AST node)
    #   - ``binding.write(rows)``               ingest (SourceBinding)
    #   - ``binding.assign_canonical_sql()``    ER stamp (SourceBinding)
    #   - ``binding.recanonicalize_sql()``      ER reassign (SourceBinding)
    #   - ``binding.close_out_sql()``           retract a claim (SourceBinding)
    # ------------------------------------------------------------------

    def init_sql(
        self,
        query_fn: Any = None,
        *,
        schema: str = "knot_data",
        allow_destructive: bool = False,
    ) -> str:
        """Return a single SQL script that brings the target schema
        into alignment with this spec.

        - ``query_fn=None`` → full from-scratch DDL (assumes empty schema).
        - ``query_fn=callable`` → introspect the live DB; emit only
          the migration ops needed. ``callable`` matches the
          ``diff_against_db`` query interface:
          ``(sql, params) -> list[tuple]``.

        Validates the spec first. Statements are blank-line separated
        and ``;``-terminated; the host runs the whole thing as one
        multi-statement script.
        """
        self.validate()
        if query_fn is None:

            def query_fn(_sql: str, _params: tuple) -> list:  # empty DB
                return []

        from knot.compile.migrate import diff_against_db

        ops = diff_against_db(
            self, query_fn, schema=schema, allow_destructive=allow_destructive
        )
        return "\n\n".join(op.sql for op in ops)

    def emit_validation(self, **kwargs: Any) -> Any:
        """List of ``(constraint_name, validation_sql)`` pairs. Validates
        the spec first. See ``knot.compile.constraints.emit_validation``."""
        self.validate()
        from knot.compile.constraints import emit_validation

        return emit_validation(self, **kwargs)


class SpecError(ValueError):
    """Raised by ``Spec.validate`` when well-formedness fails."""


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
