"""Impact analysis — which workflows recompile when the spec changes.

Per core-design.md commitments 4 and 10: one unified expression tree,
one impact-analysis visitor; no parallel meta-structure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from knot.metaschema import OntologyClass, Slot, Source
from knot.protocols import DataContext
from knot.walk import Ref, walk_refs

if TYPE_CHECKING:
    from tests.test_env import Impact, SpecEdit


def datacontext_refs(dc: DataContext) -> list[Ref]:
    """All spec refs a DataContext touches (identity-deduplicated list).

    OntologyClass / Slot / Source are mutable Pydantic models (frozen=False)
    and are not hashable; dedup is by object identity (id()).
    """
    seen: set[int] = set()
    result: list[Ref] = []

    def _add(items):
        for ref in items:
            oid = id(ref)
            if oid not in seen:
                seen.add(oid)
                result.append(ref)

    primary = dc.primary
    if isinstance(primary, (OntologyClass, Slot)):
        _add(walk_refs(primary))
    elif isinstance(primary, list):
        for item in primary:
            if isinstance(item, (OntologyClass, Slot)):
                _add(walk_refs(item))
    if dc.where is not None:
        _add(walk_refs(dc.where))
    if dc.project is not None:
        for item in dc.project:
            _add(walk_refs(item))
    return result


class BoundImpl:
    """Lightweight binding record: impl class, name, and workflow."""

    def __init__(self, impl_class: type, impl_name: str, workflow: str) -> None:
        self.impl_class = impl_class
        self.impl_name = impl_name
        self.workflow = workflow


def _impl_datacontexts(impl_class: type) -> list[DataContext]:
    dcs = []
    for name in dir(impl_class):
        try:
            val = getattr(impl_class, name)
        except Exception:
            continue
        if isinstance(val, DataContext):
            dcs.append(val)
    return dcs


def _edit_targets(spec_edit: "SpecEdit") -> tuple[set[str], set[str]]:
    """Extract (target_class_names, target_slot_names) from a SpecEdit."""
    cls: set[str] = set()
    slots: set[str] = set()
    et, p = spec_edit.edit_type, spec_edit.payload

    if et == "rename_slot":
        parts = p["from_path"].rsplit(".", 1)
        cls.add(parts[0]); slots.add(parts[1])
    elif et in ("delete_slot", "change_resolution_policy"):
        parts = p["path"].rsplit(".", 1)
        cls.add(parts[0]); slots.add(parts[1])
    elif et == "add_slot":
        cls.add(p["class_"])
        if isinstance(p.get("slot"), dict) and "name" in p["slot"]:
            slots.add(p["slot"]["name"])
    elif et in ("remove_class", "change_superclass"):
        if "class_name" in p:
            cls.add(p["class_name"])
        if p.get("new_parent"):
            cls.add(p["new_parent"])
    elif et == "rename_class":
        cls.add(p["from_name"])
    elif et == "add_class":
        cd = p.get("class_def", {})
        if isinstance(cd, dict) and "name" in cd:
            cls.add(cd["name"])
        if p.get("is_a"):
            cls.add(p["is_a"])
    elif et == "add_source":
        sd = p.get("source_def", {})
        if isinstance(sd, dict) and "entity_class" in sd:
            cls.add(sd["entity_class"])
    elif et == "add_derivation":
        cls.add(p["class_name"]); slots.add(p["slot_name"])

    return cls, slots


def affected_workflows(
    spec_edit: "SpecEdit",
    registered_impls: list[BoundImpl],
) -> "Impact":
    """Walk every bound impl's DataContexts; flag any that reference the edit target."""
    target_cls, target_slots = _edit_targets(spec_edit)
    affected_cls: set[str] = set(target_cls)
    affected_impl_names: set[str] = set()
    affected_wf: set[str] = set()

    for binding in registered_impls:
        for dc in _impl_datacontexts(binding.impl_class):
            hit = False
            for ref in datacontext_refs(dc):
                if isinstance(ref, OntologyClass) and ref.name in target_cls:
                    hit = True; affected_cls.add(ref.name)
                elif isinstance(ref, Slot) and ref.name in target_slots:
                    hit = True
                elif isinstance(ref, Source) and ref.entity_class.name in target_cls:
                    hit = True; affected_cls.add(ref.entity_class.name)
            if hit:
                affected_impl_names.add(binding.impl_name)
                affected_wf.add(binding.workflow)

    from tests.test_env import Impact  # type: ignore[import]
    return Impact(
        affected_classes=affected_cls,
        affected_impls=affected_impl_names,
        affected_workflows=affected_wf,
    )
