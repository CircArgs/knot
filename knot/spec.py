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
    (``ddl``, ``Query.sql`` etc.) call it automatically.

Body validation (typos in slot references) is caught at construction
time by the builder: ``movie.col.nonexistent`` raises ``KeyError``
before the expression tree is built. No post-hoc SQL parsing required.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from knot.ast.expr import Expr, FkRef, Ref, VectorRef
from knot.ast.select import Layer, Query
from knot.ast.types import ClassRef, TypeExpression, Vector, _coerce_type

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

    def __getitem__(self, name: str) -> Ref | FkRef | VectorRef:
        cls = object.__getattribute__(self, "_cls")
        slot = cls.get_slot(name)
        if isinstance(slot.type, ClassRef):
            return FkRef(
                class_name=cls.name,
                slot_name=name,
                target_class_name=slot.type.target.name,
            )
        if isinstance(slot.type, Vector):
            return VectorRef(
                class_name=cls.name,
                slot_name=name,
                metric=slot.type.metric,
                dim=slot.type.dim,
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
    #   cls.unresolved        bindings still waiting on ER —
    #                         ``canonical_id IS NULL`` across every
    #                         source. The ER worker's work-to-do view.
    #
    # All return ``Query`` and chain the same fluent surface
    # (``.where`` / ``.order_by`` / ``.limit`` / ``.offset`` /
    # ``.select`` / ``.sql``). The cross-source raw bindings stream
    # is intentionally unexposed — internal / admin concern.

    def _query(self, layer: Layer) -> Query:
        if self.kind != ClassKind.CONCRETE:
            raise ValueError(
                f"OntologyClass {self.name!r} is {self.kind.value!r}; "
                f"only concrete classes have a {layer.name} relation"
            )
        return Query(class_name=self.name, layer=layer, _spec=self._spec)

    @property
    def resolved(self) -> Query:
        """Query against ``<class>_resolved`` — the resolver's
        argmax view, one row per canonical_id with the highest-weight
        non-null value per slot. The user-facing "current state"
        read shape."""
        return self._query(Layer.RESOLVED)

    @property
    def all_sources(self) -> Query:
        """Query against ``<class>_all_sources`` — per-source
        provenance view, one row per canonical_id with each slot
        column as a jsonb of ``{source_name: {value, weight}}``.
        Slot columns project as jsonb here, not their underlying
        scalar type."""
        return self._query(Layer.ALL_SOURCES)

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
        return self._query(Layer.BINDINGS).where(Raw(f"source_name = '{src}'"))

    @property
    def unresolved(self) -> Query:
        """Bindings still waiting on entity resolution: rows in
        ``<class>_bindings`` with ``canonical_id IS NULL``, across
        every source. The ER worker's work-to-do view. Chain
        ``.where(...)`` to scope to a particular source or filter on
        slot values."""
        from knot.ast.expr import Raw

        return self._query(Layer.BINDINGS).where(Raw("canonical_id IS NULL"))

    # ------------------------------------------------------------------
    # Name accessors — qualified table / view names hosts use when
    # writing raw SQL (e.g. k-NN candidate generation in the ER
    # notebook). One place to change the suffix convention.
    # ------------------------------------------------------------------

    def _require_spec(self) -> Spec:
        if self._spec is None:
            raise RuntimeError(
                f"OntologyClass {self.name!r} is not attached to a Spec "
                f"(create via spec.add_class(...))"
            )
        return self._spec

    @property
    def canonical_table_name(self) -> str:
        """``"<schema>.<class>"`` — fully qualified name of the
        canonical table. Reads ``schema`` from the owning spec."""
        spec = self._require_spec()
        return f"{spec.schema}.{self.name.lower()}"

    @property
    def bindings_table_name(self) -> str:
        """``"<schema>.<class>_bindings"`` — fully qualified name of
        the SCD2 bindings table."""
        spec = self._require_spec()
        return f"{spec.schema}.{self.name.lower()}_bindings"

    @property
    def resolved_view_name(self) -> str:
        """``"<schema>.<class>_resolved"`` — fully qualified name of
        the resolver's argmax view."""
        spec = self._require_spec()
        return f"{spec.schema}.{self.name.lower()}_resolved"

    @property
    def all_sources_view_name(self) -> str:
        """``"<schema>.<class>_all_sources"`` — fully qualified name
        of the per-source jsonb provenance view."""
        spec = self._require_spec()
        return f"{spec.schema}.{self.name.lower()}_all_sources"

    @property
    def bindings(self) -> list[SourceBinding]:
        """All ``SourceBinding`` rows on the spec that bind THIS
        class. Saves callers from filtering ``spec.source_bindings``
        by hand."""
        spec = self._require_spec()
        return [b for b in spec.source_bindings if b.class_ is self]

    def binding_for(self, source: Source) -> SourceBinding | None:
        """The (at most one) ``SourceBinding`` linking this class to
        ``source``. Returns ``None`` if no binding exists."""
        for b in self.bindings:
            if b.source is source:
                return b
        return None

    @property
    def referrers(self) -> list[tuple[OntologyClass, Slot]]:
        """Every ``(class, slot)`` pair in the owning spec where
        ``slot.type`` is a ``ClassRef`` pointing at this class. Used by
        the ER write path to drive FK fan-out: when a canonical_id is
        stamped on this class's binding, every referencing class's
        bindings need their FK column rewritten from source-id to the
        new canonical_id."""
        spec = self._require_spec()
        out: list[tuple[OntologyClass, Slot]] = []
        for other in spec.concrete_classes():
            for slot in other.effective_slots():
                if isinstance(slot.type, ClassRef) and slot.type.target is self:
                    out.append((other, slot))
        return out

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
        if name in self._spec.classes:
            raise ValueError(f"Spec already has a class named {name!r}")
        vc = VirtualClass(
            name=name, is_a=self, definition=where, description=description
        )
        self._spec.classes[name] = vc
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
                f"(use the correlated-aggregate form: e.g. "
                f"((credit.col.movie == this.Movie) & "
                f"(credit.col.role == 'director')).any()); "
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

    @property
    def bindings(self) -> list[SourceBinding]:
        """All ``SourceBinding`` rows on the spec owned by THIS source.
        Saves callers from filtering ``spec.source_bindings``."""
        if self._spec is None:
            raise RuntimeError(
                f"Source {self.name!r} is not attached to a Spec "
                f"(create via spec.add_source(...))"
            )
        return [b for b in self._spec.source_bindings if b.source is self]

    def bind(
        self,
        cls: OntologyClass,
        *,
        description: str | None = None,
    ) -> SourceBinding:
        """Create a binding from this source to ``cls`` and register it
        on the owning spec.

        Weights are runtime-only — set them via
        ``binding.upsert_weight_sql()`` after deploy. Until then,
        every slot resolves at weight 0 (``COALESCE`` in the resolver
        view); the operator owns calibration end-to-end."""
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
    slot_mappings: dict[str, SlotMapping] = field(default_factory=dict)
    description: str | None = None

    @property
    def identifier_slot(self) -> Slot:
        """The class's identifier slot. Carried as a property (not a
        field) because every binding for a class shares the same one."""
        return self.class_.identifier_slot()

    # ------------------------------------------------------------------
    # Ingest-mapping declaration. Weight is a separate concern owned
    # entirely at runtime — see read_weights_sql / upsert_weight_sql.
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

        No weight kwarg — weights are runtime-only. Use
        ``binding.upsert_weight_sql()`` to set values after deploy.
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
    # Runtime helpers — ingest, ER. Methods on the binding because the
    # binding pins (source, class); the user shouldn't have to repeat
    # those at the call site.
    # ------------------------------------------------------------------

    def write_sql(self, *, bindings_suffix: str = "_bindings") -> str:
        """Return one upsert SQL template for this binding. References a
        single ``%(rows)s::jsonb`` parameter — the host's connector
        binds the rows. Schema comes from the spec the binding's source
        is attached to.

        Run with::

            sql = binding.write_sql()
            with pg.cursor() as cur:
                cur.execute(sql, {"rows": rows})

        Semantics: ``INSERT ... ON CONFLICT (source_name,
        source_identifier) DO UPDATE`` — re-ingesting the same source's
        same source_id upserts in place. ``canonical_id`` and
        ``er_metadata`` are preserved across re-ingests (ER-owned);
        every other slot + ``raw_payload`` gets overwritten from the
        new payload.

        Multi-binding atomic write: call ``binding.write_sql()`` per
        binding, run all the statements in one ``pg.transaction()``.
        """
        spec = self._require_spec()
        # Deliberately no ``spec.validate()`` here — write_sql is a
        # hot path (per-batch ingest) and we trust the spec was
        # validated at deploy time.
        from knot.compile.write import emit_binding_write_sql

        return emit_binding_write_sql(
            self, schema=spec.schema, bindings_suffix=bindings_suffix
        )

    def assign_canonical_sql(self, *, bindings_suffix: str = "_bindings") -> str:
        """Return the SQL template that assigns a ``canonical_id`` to
        one unresolved binding row. Three named placeholders —
        ``%(canonical_id)s``, ``%(source_identifier)s``,
        ``%(er_metadata)s`` (None to leave unchanged, JSON string to
        set). Host binds via ``cur.execute(sql, params)``."""
        spec = self._require_spec()
        from knot.compile.write import emit_assign_canonical_sql

        return emit_assign_canonical_sql(
            self, schema=spec.schema, bindings_suffix=bindings_suffix
        )

    def recanonicalize_sql(self, *, bindings_suffix: str = "_bindings") -> str:
        """Return the SQL template that reassigns a binding row's
        ``canonical_id``, preserving history via SCD2. Three named
        placeholders — ``%(new_canonical_id)s``,
        ``%(source_identifier)s``, ``%(er_metadata)s`` (None inherits
        the closed row's metadata; JSON string overrides)."""
        spec = self._require_spec()
        from knot.compile.write import emit_recanonicalize_sql

        return emit_recanonicalize_sql(
            self, schema=spec.schema, bindings_suffix=bindings_suffix
        )

    def retract_sql(self, *, bindings_suffix: str = "_bindings") -> str:
        """Return the SQL template that retracts (deletes) one binding
        row. Two named placeholders — ``%(canonical_id)s``,
        ``%(source_identifier)s``. Used to withdraw a source's claim
        entirely (DELETE; no row left)."""
        spec = self._require_spec()
        from knot.compile.write import emit_retract_sql

        return emit_retract_sql(
            self, schema=spec.schema, bindings_suffix=bindings_suffix
        )

    def _require_spec(self) -> Spec:
        """Internal — resolve the binding's owning ``Spec`` or raise."""
        if self.source._spec is None:
            raise RuntimeError(
                f"binding {self.source.name!r} → {self.class_.name!r} "
                f"is not attached to a Spec"
            )
        return self.source._spec

    @property
    def bindings_table_name(self) -> str:
        """``"<schema>.<class>_bindings"`` — the table this binding
        writes to. Same as ``self.class_.bindings_table_name``."""
        return self.class_.bindings_table_name

    # ------------------------------------------------------------------
    # Weight runtime — read + upsert SQL for this binding's
    # (source, class, *) rows in ``source_weight``. Weights are
    # runtime-only; the host owns calibration entirely.
    # ------------------------------------------------------------------

    def read_weights_sql(self) -> str:
        """SELECT this binding's currently-stored weights — rows of
        ``(slot_name, weight)``. Empty result means no runtime tuning
        has happened for this (source, class) yet; resolver falls
        back to 0. See ``knot.compile.weight.emit_read_weights_sql``."""
        self._require_spec()
        from knot.compile.weight import emit_read_weights_sql

        return emit_read_weights_sql(self)

    def upsert_weight_sql(self) -> str:
        """Set the weight for ONE slot — INSERT … ON CONFLICT UPDATE.
        Host binds ``%(slot_name)s`` and ``%(weight)s``. See
        ``knot.compile.weight.emit_upsert_weight_sql``."""
        self._require_spec()
        from knot.compile.weight import emit_upsert_weight_sql

        return emit_upsert_weight_sql(self)

    def upsert_weights_sql(self) -> str:
        """Bulk-set N weights in one statement — INSERT … ON CONFLICT
        UPDATE driven by ``jsonb_each(%(weights)s::jsonb)``. Host
        binds a JSON object ``{slot_name: weight, …}``. See
        ``knot.compile.weight.emit_upsert_weights_sql``."""
        self._require_spec()
        from knot.compile.weight import emit_upsert_weights_sql

        return emit_upsert_weights_sql(self)


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
    schema (``ddl(schema=…)``) is the only structural name knot
    cares about. If you want to label the spec for your team, do it
    in the codebase (filename, module name, repo).
    """

    identifier_slot_name: str  # required — no default; e.g. "canonical_id"
    # Schema is set once on the root Spec (or on sub-specs that compile
    # in isolation). Every façade method — Spec.ddl, Query.sql,
    # binding.write_sql, etc. — reads ``self.schema`` (no ``schema=``
    # kwargs). The free emitters in ``knot.compile.*`` still take
    # ``schema=…`` as a kwarg for tests / cases that want to retarget
    # without mutating the spec.
    schema: str = "knot_data"
    # ``classes`` and ``sources`` are dicts keyed by name. Lets the
    # builder enforce uniqueness for free (vs. a separate
    # ``_check_unique_class_name`` pass) and lets ``spec.classes["Movie"]``
    # be the obvious lookup. Insertion order is preserved (Python 3.7+
    # dict guarantee), so iteration order is still declaration order.
    classes: dict[str, OntologyClass | VirtualClass] = field(default_factory=dict)
    sources: dict[str, Source] = field(default_factory=dict)
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
        if name in self.classes:
            raise ValueError(f"Spec already has a class named {name!r}")
        cls = OntologyClass(
            name=name,
            kind=kind if isinstance(kind, ClassKind) else ClassKind(kind),
            is_a=is_a,
            mixins=list(mixins) if mixins else [],
            description=description,
            _spec=self,
        )
        self.classes[name] = cls
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
        if name in self.sources:
            raise ValueError(f"Spec already has a source named {name!r}")
        s = Source(name=name, description=description, _spec=self)
        self.sources[name] = s
        return s

    def enable_corrections(
        self,
        *,
        description: str | None = "human overrides",
    ) -> Source:
        """Register the ``_user_corrections`` synthetic source and bind
        it to every concrete ``OntologyClass`` in the spec.

        Weights are runtime-only: corrections start at weight 0 (the
        resolver's COALESCE fallback). The operator decides how
        dominant corrections should be by upserting weights via the
        usual runtime API:

            cur.execute(
                cls.corrections_binding().upsert_weight_sql(),
                {"slot_name": "title", "weight": 1e6},
            )

        Idempotent: calling again is a no-op if the source already
        exists. Returns the (possibly pre-existing) ``Source`` object.
        """
        existing = self.sources.get(CORRECTIONS_SOURCE_NAME)
        if existing is not None:
            return existing
        source = Source(
            name=CORRECTIONS_SOURCE_NAME, description=description, _spec=self
        )
        self.sources[CORRECTIONS_SOURCE_NAME] = source
        for cls in self.concrete_classes():
            source.bind(cls)
        return source

    def include(self, other: Spec) -> None:
        """Merge another spec's classes, sources, source bindings, and
        constraints into this one — the FastAPI ``app.include_router``
        analogue for knot.

        Each domain module builds a self-contained sub-spec (its own
        ``Spec`` instance, treated as a collector), then ``base.py``
        creates the top-level ``Spec`` and ``.include()``s each one.
        Avoids the shared-singleton pattern where every domain file
        imports the same ``spec`` and mutates it directly.

        Identifier-slot-name must match across the two specs;
        sub-specs would otherwise add identifier slots with a
        different name than the parent expects. Classes and sources
        get re-rooted (``_spec``) to this spec so subsequent
        ``cls.add_virtual`` / ``cls.add_constraint`` calls land on the
        right object.

        Same-named classes / sources across two parts are an error —
        the include is additive only, not a merge.
        """
        if self.identifier_slot_name != other.identifier_slot_name:
            raise ValueError(
                f"Spec.include: identifier_slot_name mismatch "
                f"({self.identifier_slot_name!r} vs "
                f"{other.identifier_slot_name!r})"
            )
        for cls in other.classes.values():
            if cls.name in self.classes:
                raise ValueError(
                    f"Spec.include: class {cls.name!r} already exists "
                    f"in target spec — included parts cannot redeclare "
                    f"classes that the parent already owns"
                )
            # VirtualClass has slots=True and no ``_spec`` field
            # (its parent OntologyClass owns the back-reference). Only
            # OntologyClass needs re-rooting.
            if isinstance(cls, OntologyClass):
                cls._spec = self
            self.classes[cls.name] = cls
        for src in other.sources.values():
            if src.name in self.sources:
                raise ValueError(
                    f"Spec.include: source {src.name!r} already exists in target spec"
                )
            src._spec = self
            self.sources[src.name] = src
        for binding in other.source_bindings:
            self.source_bindings.append(binding)
        for constraint in other.constraints:
            self.constraints.append(constraint)

    # -- well-formedness validation --

    def concrete_classes(self) -> list[OntologyClass]:
        """Concrete ``OntologyClass`` entries — skips abstract and
        ``VirtualClass``. Used everywhere the emitters loop over
        "classes that materialize a table"."""
        return [
            c
            for c in self.classes.values()
            if isinstance(c, OntologyClass) and c.kind == ClassKind.CONCRETE
        ]

    def virtual_classes(self) -> list[VirtualClass]:
        """``VirtualClass`` entries — backed by a view, not a table."""
        return [c for c in self.classes.values() if isinstance(c, VirtualClass)]

    def class_by_name(self, name: str) -> OntologyClass | VirtualClass:
        try:
            return self.classes[name]
        except KeyError:
            raise KeyError(f"Spec has no class named {name!r}") from None

    def _validation_errors(self) -> list[str]:
        """Internal: list cross-entity well-formedness errors.

        Local entity checks (name shape, enum values,
        body Expr typing) have already run in each entity's
        ``__post_init__``. Body slot-ref typos are caught at expression
        construction time by ``movie.col.<slot>`` raising ``KeyError``.
        """
        errs: list[str] = []
        # ``classes`` is a dict keyed by name — duplicates can't happen
        # via the public builder. (Direct ``classes[name] = cls`` with
        # a mismatched key would still slip through; not worth guarding.)
        concrete_or_abstract: dict[str, OntologyClass] = {
            c.name: c for c in self.classes.values() if isinstance(c, OntologyClass)
        }

        for c in self.classes.values():
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

        # SourceBinding references — sources dict already enforces
        # name uniqueness.
        binding_keys: set[tuple[str, str]] = set()
        for b in self.source_bindings:
            key = (b.source.name, b.class_.name)
            if key in binding_keys:
                errs.append(
                    f"duplicate binding source={b.source.name!r} class={b.class_.name!r}"
                )
            binding_keys.add(key)
            if b.source.name not in self.sources:
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
        for c in self.classes.values():
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
    #   - ddl             — canonical target schema (one CREATE script)
    #   - emit_validation — runtime constraint checks (per-rule SELECTs)
    # Per-entity runtime methods live on the entity:
    #   - ``query.sql(schema=…)``               read (Query AST node)
    #   - ``binding.write_sql(schema=…)``       ingest (SourceBinding)
    #   - ``binding.assign_canonical_sql()``    ER stamp (SourceBinding)
    #   - ``binding.recanonicalize_sql()``      ER reassign (SourceBinding)
    #   - ``binding.retract_sql()``             retract a claim (SourceBinding)
    # ------------------------------------------------------------------

    def ddl(self, *, include_views: bool = True) -> str:
        """Return the canonical CREATE script for this spec — schema,
        extension (when needed), tables, indexes, FK constraints, and
        views. Idempotent throughout (``IF NOT EXISTS`` /
        ``CREATE OR REPLACE``); safe to re-run against the live DB.

        For first deploys, execute directly. For migrations against a
        live DB, pipe the output through a schema-diff tool
        (sqldef / Atlas / dbmate / …) — knot doesn't own the diff.
        See CLAUDE.md §"Schema deployment" for rationale and tool
        recommendations.

        Schema name comes from ``self.schema`` (set once on the spec
        at construction time). Set ``include_views=False`` when piping
        through a migration tool whose parser doesn't handle knot's
        view DDL (psqldef v3 trips on ``FILTER (WHERE …)`` in the
        ``_all_sources`` provenance views). The standard two-phase
        recipe:

            sqldef-tool < spec.ddl(include_views=False)   # schema
            pg.execute(spec.ddl())                        # views

        Views are unconditional ``CREATE OR REPLACE`` and depend on
        no live data, so the second call is always safe to run after
        the migration tool finishes.
        """
        self.validate()
        from knot.compile.ddl import emit_ddl

        return "\n\n".join(
            emit_ddl(
                self,
                schema=self.schema,
                if_not_exists=True,
                emit_resolved_views=include_views,
                emit_all_sources_views=include_views,
                emit_virtual_views=include_views,
            )
        )

    def emit_validation(self) -> list[tuple[str, str]]:
        """List of ``(constraint_name, validation_sql)`` pairs. Validates
        the spec first; schema comes from ``self.schema``. See
        ``knot.compile.constraints.emit_validation``."""
        self.validate()
        from knot.compile.constraints import emit_validation

        return emit_validation(self, schema=self.schema)


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
