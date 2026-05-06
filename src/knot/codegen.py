"""Knot SDK codegen — generates typed Python classes from a Spec instance.

Produces one Python source string (or file) per ProtocolKind:

  RESOLVED            -> Movie.year: Resolved[int]    — __gt__ etc. work
  DISAGREEMENT_AWARE  -> Movie.year: MultiValued[int] — bare comparisons absent;
                         .from_source(), .all_(), .winner(), .contributions() required
  ER_DECISION_LENS    -> opaque accessor class (entity_bindings / lineage shape)
  TARGET_DIRECT       -> opaque accessor class (published artifact shape)

The generated module is human-readable Python.  Bound impls import it:

    from knot.ontology import Movie, Person, Credit

See design/staging/auto-generated-sdk.md and staging/multi-valued-semantics.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from knot.metaschema import OntologyClass, Slot, Spec, TypeDefinition
from knot.protocols import ProtocolKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PRIMITIVE_MAP: dict[str, str] = {
    "str": "str",
    "int": "int",
    "float": "float",
    "bool": "bool",
    "date": "date",
    "datetime": "datetime",
    "bytes": "bytes",
}


def _slot_python_type(slot: Slot) -> str:
    """Return the Python primitive type name for a scalar slot range."""
    if slot.range is None:
        return "Any"
    if isinstance(slot.range, OntologyClass):
        return slot.range.name
    base = slot.range.base or "str"
    return _PRIMITIVE_MAP.get(base, base)


def _needs_date_import(spec: Spec) -> bool:
    for cls in spec.classes:
        for slot in cls.slots:
            if isinstance(slot.range, TypeDefinition) and slot.range.base in ("date", "datetime"):
                return True
    return False


def _spec_hash(spec: Spec) -> str:
    raw = json.dumps({"id": spec.id, "version": spec.version}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def _wrapper_for(protocol_kind: ProtocolKind) -> str:
    if protocol_kind == ProtocolKind.RESOLVED:
        return "Resolved"
    if protocol_kind == ProtocolKind.DISAGREEMENT_AWARE:
        return "MultiValued"
    if protocol_kind == ProtocolKind.ER_DECISION_LENS:
        return "ERDecision"
    return "TargetDirect"


# ---------------------------------------------------------------------------
# Line-oriented code emitter
# ---------------------------------------------------------------------------

class _Lines:
    """Simple line accumulator — avoids textwrap.dedent pitfalls."""

    def __init__(self) -> None:
        self._lines: list[str] = []

    def add(self, line: str = "") -> None:
        self._lines.append(line)

    def extend(self, lines: list[str]) -> None:
        self._lines.extend(lines)

    def blank(self) -> None:
        self._lines.append("")

    def get(self) -> str:
        return "\n".join(self._lines) + "\n"


# ---------------------------------------------------------------------------
# Descriptor emitters
# ---------------------------------------------------------------------------

def _emit_scalar_descriptor(
    out: _Lines,
    class_name: str,
    slot: Slot,
    wrapper: str,
) -> None:
    python_type = _slot_python_type(slot)
    attr = slot.name
    desc_cls = f"_{class_name}_{attr}_Descriptor"
    has_comparison = wrapper in ("Resolved", "ERDecision", "TargetDirect")

    out.add(f"class {desc_cls}:")
    out.add(f'    """Descriptor for {class_name}.{attr}: {wrapper}[{python_type}]."""')
    out.add(f"    __slot_ref__: ClassVar[tuple[str, str]] = ({class_name!r}, {attr!r})")
    out.add(f"    _slot: ClassVar[Slot] = _SLOT_REGISTRY[{class_name!r}][{attr!r}]")
    out.add(f"    _from_class: ClassVar[OntologyClass] = _CLASS_REGISTRY[{class_name!r}]")
    out.blank()
    out.add("    def __hash__(self) -> int:")
    out.add("        return id(self)")
    out.blank()

    if has_comparison:
        out.add(f"    def __gt__(self, other: {python_type}) -> Compare:")
        out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
        out.add("        return Compare(op=CompareOp.GT, left=path, right=Literal_(value=other))")
        out.blank()
        out.add(f"    def __ge__(self, other: {python_type}) -> Compare:")
        out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
        out.add("        return Compare(op=CompareOp.GTE, left=path, right=Literal_(value=other))")
        out.blank()
        out.add(f"    def __lt__(self, other: {python_type}) -> Compare:")
        out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
        out.add("        return Compare(op=CompareOp.LT, left=path, right=Literal_(value=other))")
        out.blank()
        out.add(f"    def __le__(self, other: {python_type}) -> Compare:")
        out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
        out.add("        return Compare(op=CompareOp.LTE, left=path, right=Literal_(value=other))")
        out.blank()
        out.add("    def __eq__(self, other: object) -> Compare:  # type: ignore[override]")
        out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
        out.add("        return Compare(op=CompareOp.EQ, left=path, right=Literal_(value=other))")
        out.blank()
        out.add("    def __ne__(self, other: object) -> Compare:  # type: ignore[override]")
        out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
        out.add("        return Compare(op=CompareOp.NEQ, left=path, right=Literal_(value=other))")
        out.blank()

    # Multi-valued escape hatches — available on both RESOLVED and DISAGREEMENT_AWARE
    out.add(f"    def from_source(self, source: Any) -> _ResolvedAccessor:")
    out.add('        """Pin to a specific source\'s contribution."""')
    out.add("        return _ResolvedAccessor(self._slot, self._from_class, source=source)")
    out.blank()
    out.add(f"    def all_(self) -> _ResolvedAccessor:")
    out.add('        """Forall — every source must satisfy the predicate."""')
    out.add('        return _ResolvedAccessor(self._slot, self._from_class, reduction="all")')
    out.blank()
    out.add(f"    def any_(self) -> _ResolvedAccessor:")
    out.add('        """Exists — any source satisfies the predicate."""')
    out.add('        return _ResolvedAccessor(self._slot, self._from_class, reduction="any")')
    out.blank()
    out.add(f"    def winner(self) -> _ResolvedAccessor:")
    out.add('        """Explicit opt-in to RESOLVED behavior under DISAGREEMENT_AWARE lens."""')
    out.add('        return _ResolvedAccessor(self._slot, self._from_class, reduction="winner")')
    out.blank()
    out.add(f"    def contributions(self) -> _ResolvedAccessor:")
    out.add('        """Raw bag of all source contributions."""')
    out.add('        return _ResolvedAccessor(self._slot, self._from_class, reduction="contributions")')
    out.blank()

    # Range predicates
    out.add(f"    def between(self, lower: {python_type}, upper: {python_type}, *, inclusive: bool = True) -> Between:")
    out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.add("        return Between(left=path, lower=Literal_(value=lower), upper=Literal_(value=upper), inclusive=inclusive)")
    out.blank()
    out.add(f"    def within(self, values: list[{python_type}]) -> Within:")
    out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.add("        return Within(left=path, values=[Literal_(value=v) for v in values])")
    out.blank()
    out.add("    def matches(self, pattern: str) -> Matches:")
    out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.add("        return Matches(left=path, pattern=pattern)")
    out.blank()
    out.add("    def is_null(self) -> Compare:")
    out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.add("        return Compare(op=CompareOp.IS_NULL, left=path)")
    out.blank()
    out.add("    def is_not_null(self) -> Compare:")
    out.add("        path = SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.add("        return Compare(op=CompareOp.IS_NOT_NULL, left=path)")
    out.blank()
    out.blank()


def _emit_relation_descriptor(out: _Lines, class_name: str, slot: Slot) -> None:
    target_name = slot.range.name  # type: ignore[union-attr]
    attr = slot.name
    desc_cls = f"_{class_name}_{attr}_Descriptor"

    out.add(f"class {desc_cls}:")
    out.add(f'    """Relation descriptor for {class_name}.{attr} -> {target_name}."""')
    out.add(f"    __slot_ref__: ClassVar[tuple[str, str]] = ({class_name!r}, {attr!r})")
    out.add(f"    _slot: ClassVar[Slot] = _SLOT_REGISTRY[{class_name!r}][{attr!r}]")
    out.add(f"    _from_class: ClassVar[OntologyClass] = _CLASS_REGISTRY[{class_name!r}]")
    out.blank()
    out.add("    def __hash__(self) -> int:")
    out.add("        return id(self)")
    out.blank()
    out.add("    def where(self, predicate: Compare | BoolExpr) -> FilteredRelation:")
    out.add("        rel = RelationRef(from_class=self._from_class, slot=self._slot)")
    out.add("        return FilteredRelation(relation=rel, filter=predicate)")
    out.blank()
    out.add("    def collect(self, *fields: Any, order_by: Any = None, distinct: bool = False) -> RelationAggregate:")
    out.add("        rel = RelationRef(from_class=self._from_class, slot=self._slot)")
    out.add("        return RelationAggregate(")
    out.add("            relation=rel,")
    out.add("            func=AggFunc.COLLECT,")
    out.add("            operand=fields[0] if fields else None,")
    out.add("            distinct=distinct,")
    out.add("        )")
    out.blank()
    out.add("    def count(self, *, distinct: bool = False) -> RelationCount:")
    out.add("        rel = RelationRef(from_class=self._from_class, slot=self._slot)")
    out.add("        return RelationCount(relation=rel, distinct=distinct)")
    out.blank()
    out.add("    def group_by_source(self) -> RelationAggregate:")
    out.add("        rel = RelationRef(from_class=self._from_class, slot=self._slot)")
    out.add("        return RelationAggregate(relation=rel, func=AggFunc.COLLECT, group_by=GroupByMode.SOURCE)")
    out.blank()
    out.add("    def transitive(self, *, until: BoolExpr | None = None, max_depth: int | None = None) -> RecursiveTraversal:")
    out.add("        rel = RelationRef(from_class=self._from_class, slot=self._slot)")
    out.add("        step = SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.add("        return RecursiveTraversal(start=rel, step=step, until=until, max_depth=max_depth)")
    out.blank()
    out.blank()


def _emit_sdk_class(out: _Lines, cls: OntologyClass, wrapper: str) -> None:
    name = cls.name
    out.add(f"class {name}:")
    out.add(f'    """SDK class for ontology entity {name} ({wrapper} lens).')
    out.blank()
    out.add(f"    Slot wrapper: {wrapper}[T].")
    out.add(f"    Use class-level attributes to build expression trees:")
    if wrapper == "Resolved":
        out.add(f"        {name}.<slot> > value")
    else:
        out.add(f"        {name}.<slot>.from_source(s) > value  (bare comparison not available)")
    out.add(f'    """')
    out.add(f"    __spec_ref__: ClassVar[str] = {name!r}")
    out.add(f"    _spec: ClassVar[OntologyClass] = _CLASS_REGISTRY[{name!r}]")
    for slot in cls.slots:
        attr = slot.name
        desc_cls = f"_{name}_{attr}_Descriptor"
        out.add(f"    {attr}: ClassVar[{desc_cls}] = {desc_cls}()")
    out.blank()
    out.blank()


# ---------------------------------------------------------------------------
# Registry builder emitter
# ---------------------------------------------------------------------------

def _emit_registries(out: _Lines, spec: Spec) -> None:
    """Emit _CLASS_REGISTRY and _SLOT_REGISTRY population code."""
    out.add("_CLASS_REGISTRY: dict[str, OntologyClass] = {}")
    out.add("_SLOT_REGISTRY: dict[str, dict[str, Slot]] = {}")
    out.blank()
    out.blank()
    out.add("def _build_registries() -> None:")
    out.add("    from knot.metaschema import (")
    out.add("        OntologyClass, Slot, TypeDefinition, ResolutionPolicy,")
    out.add("    )")
    out.blank()

    # TypeDefinitions
    type_var: dict[str, str] = {}
    for i, td in enumerate(spec.types):
        var = f"_td_{i}"
        type_var[td.name] = var
        base_repr = repr(td.base) if td.base else "None"
        out.add(f"    {var} = TypeDefinition(name={td.name!r}, base={base_repr})")
    out.blank()

    # OntologyClass stubs
    cls_var: dict[str, str] = {}
    for i, cls in enumerate(spec.classes):
        var = f"_cls_{i}"
        cls_var[cls.name] = var
        out.add(f"    {var} = OntologyClass(name={cls.name!r})")
    out.blank()

    # Slots per class
    slot_var: dict[str, dict[str, str]] = {}
    for ci, cls in enumerate(spec.classes):
        slot_var[cls.name] = {}
        for si, slot in enumerate(cls.slots):
            var = f"_slot_{ci}_{si}"
            slot_var[cls.name][slot.name] = var
            if slot.range is None:
                range_repr = "None"
            elif isinstance(slot.range, OntologyClass):
                range_repr = cls_var[slot.range.name]
            else:
                range_repr = type_var.get(slot.range.name, "None")
            rp = slot.resolution_policy
            rp_name = rp.name if hasattr(rp, "name") else str(rp).split(".")[-1].upper()
            out.add(
                f"    {var} = Slot("
                f"name={slot.name!r}, "
                f"range={range_repr}, "
                f"identifier={slot.identifier!r}, "
                f"required={slot.required!r}, "
                f"multivalued={slot.multivalued!r}, "
                f"resolution_policy=ResolutionPolicy.{rp_name})"
            )
    out.blank()

    # Backfill slots onto OntologyClass stubs
    for ci, cls in enumerate(spec.classes):
        cv = cls_var[cls.name]
        svars = [slot_var[cls.name][s.name] for s in cls.slots]
        if svars:
            out.add(f"    {cv}.slots = [{', '.join(svars)}]")
    out.blank()

    # Populate global registries
    out.add("    global _CLASS_REGISTRY, _SLOT_REGISTRY")
    for cls in spec.classes:
        out.add(f"    _CLASS_REGISTRY[{cls.name!r}] = {cls_var[cls.name]}")
    out.blank()
    for cls in spec.classes:
        out.add(f"    _SLOT_REGISTRY[{cls.name!r}] = {{")
        for slot in cls.slots:
            out.add(f"        {slot.name!r}: {slot_var[cls.name][slot.name]},")
        out.add("    }")
    out.blank()
    out.blank()
    out.add("_build_registries()")
    out.blank()
    out.blank()


# ---------------------------------------------------------------------------
# _ResolvedAccessor helper emitter
# ---------------------------------------------------------------------------

def _emit_resolved_accessor(out: _Lines) -> None:
    """Emit the _ResolvedAccessor class used by .from_source(), .all_(), etc."""
    out.add("class _ResolvedAccessor:")
    out.add('    """Intermediate from .from_source() / .all_() / .winner() / .any_().')
    out.blank()
    out.add("    Supports comparison operators so `Movie.year.from_source(imdb) > 1900`")
    out.add("    produces a Compare node regardless of the protocol lens.")
    out.add('    """')
    out.blank()
    out.add("    def __init__(self, slot: Slot, from_class: OntologyClass, source: Any = None, reduction: str = 'winner') -> None:")
    out.add("        self._slot = slot")
    out.add("        self._from_class = from_class")
    out.add("        self._source = source")
    out.add("        self._reduction = reduction")
    out.blank()
    out.add("    def _path(self) -> SlotPath:")
    out.add("        return SlotPath(from_class=self._from_class, slots=[self._slot])")
    out.blank()
    out.add("    def __hash__(self) -> int:")
    out.add("        return id(self)")
    out.blank()
    out.add("    def __gt__(self, other: Any) -> Compare:")
    out.add("        return Compare(op=CompareOp.GT, left=self._path(), right=Literal_(value=other))")
    out.blank()
    out.add("    def __ge__(self, other: Any) -> Compare:")
    out.add("        return Compare(op=CompareOp.GTE, left=self._path(), right=Literal_(value=other))")
    out.blank()
    out.add("    def __lt__(self, other: Any) -> Compare:")
    out.add("        return Compare(op=CompareOp.LT, left=self._path(), right=Literal_(value=other))")
    out.blank()
    out.add("    def __le__(self, other: Any) -> Compare:")
    out.add("        return Compare(op=CompareOp.LTE, left=self._path(), right=Literal_(value=other))")
    out.blank()
    out.add("    def __eq__(self, other: object) -> Compare:  # type: ignore[override]")
    out.add("        return Compare(op=CompareOp.EQ, left=self._path(), right=Literal_(value=other))")
    out.blank()
    out.add("    def __ne__(self, other: object) -> Compare:  # type: ignore[override]")
    out.add("        return Compare(op=CompareOp.NEQ, left=self._path(), right=Literal_(value=other))")
    out.blank()
    out.add("    def between(self, lower: Any, upper: Any, *, inclusive: bool = True) -> Between:")
    out.add("        return Between(left=self._path(), lower=Literal_(value=lower), upper=Literal_(value=upper), inclusive=inclusive)")
    out.blank()
    out.add("    def within(self, values: list[Any]) -> Within:")
    out.add("        return Within(left=self._path(), values=[Literal_(value=v) for v in values])")
    out.blank()
    out.blank()


# ---------------------------------------------------------------------------
# Main entry points
# ---------------------------------------------------------------------------

def generate_sdk(spec: Spec, protocol_kind: ProtocolKind) -> str:
    """Generate Python source code for the typed SDK classes for one ProtocolKind.

    Produces:
    - class Movie:
        year: <Resolved[int] | MultiValued[int]>
        title: <Resolved[str] | MultiValued[str]>
        director: <derived slot accessor>
        ...
    - class Person: ...
    - class Credit: ...

    Plus _CLASS_REGISTRY / _SLOT_REGISTRY populated at import time,
    and a module-level __spec_classes__ dict for impact-analysis tooling.
    """
    wrapper = _wrapper_for(protocol_kind)
    spec_hash = _spec_hash(spec)

    out = _Lines()

    # -- Header / imports -----------------------------------------------------
    out.add("# AUTO-GENERATED by knot.codegen — do not edit.")
    out.add(f"# spec_id={spec.id!r}  spec_hash={spec_hash}  protocol={protocol_kind.value}")
    out.add(f'"""Knot typed SDK for spec {spec.id!r} under {protocol_kind.value} lens.')
    out.blank()
    out.add("Import the classes you need:")
    class_names = [c.name for c in spec.classes]
    out.add(f"    from knot.ontology import {', '.join(class_names)}")
    out.add('"""')
    out.add("from __future__ import annotations")
    out.blank()
    out.add("from typing import Any, ClassVar")
    if _needs_date_import(spec):
        out.add("from datetime import date, datetime")
    out.blank()
    out.add("from knot.metaschema import (")
    out.add("    AggFunc,")
    out.add("    Between,")
    out.add("    BoolExpr,")
    out.add("    Compare,")
    out.add("    CompareOp,")
    out.add("    FilteredRelation,")
    out.add("    GroupByMode,")
    out.add("    Literal_,")
    out.add("    Matches,")
    out.add("    OntologyClass,")
    out.add("    RecursiveTraversal,")
    out.add("    RelationAggregate,")
    out.add("    RelationCount,")
    out.add("    RelationRef,")
    out.add("    Slot,")
    out.add("    SlotPath,")
    out.add("    Within,")
    out.add(")")
    out.blank()
    out.blank()

    # -- Registries -----------------------------------------------------------
    _emit_registries(out, spec)

    # -- _ResolvedAccessor ----------------------------------------------------
    _emit_resolved_accessor(out)

    # -- Descriptors ----------------------------------------------------------
    for cls in spec.classes:
        for slot in cls.slots:
            if isinstance(slot.range, OntologyClass):
                _emit_relation_descriptor(out, cls.name, slot)
            else:
                _emit_scalar_descriptor(out, cls.name, slot, wrapper)

    # -- SDK classes ----------------------------------------------------------
    for cls in spec.classes:
        _emit_sdk_class(out, cls, wrapper)

    # -- Module-level exports -------------------------------------------------
    out.add("# Impact-analysis hook: AST-readable without executing generated code.")
    out.add("__spec_classes__: dict[str, type] = {")
    for name in class_names:
        out.add(f"    {name!r}: {name},")
    out.add("}")
    out.blank()
    out.add(f"__all__ = {class_names!r}")
    out.blank()

    return out.get()


def write_sdk_module(spec: Spec, protocol_kind: ProtocolKind, dst: Path) -> None:
    """Write generated SDK to a .py file at dst.

    The file is importable as a knot ontology module.  Overwrites dst if it
    already exists.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    source = generate_sdk(spec, protocol_kind)
    dst.write_text(source, encoding="utf-8")
