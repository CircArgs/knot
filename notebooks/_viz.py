"""Spec visualization — mermaid generators for the walkthrough notebooks.

Three views — pick based on what you're trying to see:

  class_graph(spec)    classes + FK relationships only.
                       Best for "what's the data model?"
  binding_graph(spec)  sources + which classes they bind to.
                       Best for "where does this entity's data come from?"
  to_mermaid(spec)     combined; busy on big specs but complete.

All three return mermaid markup. Renders inline anywhere mermaid is
supported: ```mermaid``` fences in marimo markdown cells, GitHub
README, mermaid.live, etc.

Notebook helper rather than a library method on purpose —
visualization is "external surface" (every team wants different
orientation, what to hide, color-by-domain). If a stable shape
emerges across teams, promote to a ``spec.to_mermaid()`` method.
"""

from __future__ import annotations

from knot import OntologyClass, Spec, VirtualClass
from knot.ast.types import ClassRef


def _concrete_or_virtual_node(cls: OntologyClass | VirtualClass) -> str | None:
    """Mermaid node declaration for a class. Concrete → rectangle,
    virtual → hexagon, abstract → None (no table, hide from graph)."""
    if isinstance(cls, VirtualClass):
        return f"  {cls.name}{{{{{cls.name}}}}}"
    if cls.kind.value == "concrete":
        return f"  {cls.name}[{cls.name}]"
    return None


def _fk_edges(spec: Spec) -> list[str]:
    """Solid arrows for every ClassRef slot, labelled with the slot name."""
    out: list[str] = []
    for cls in spec.classes.values():
        if not isinstance(cls, OntologyClass):
            continue
        for slot in cls.slots:
            if isinstance(slot.type, ClassRef):
                out.append(f"  {cls.name} -->|{slot.name}| {slot.type.target.name}")
    return out


def class_graph(spec: Spec, *, direction: str = "LR") -> str:
    """Classes + FK relationships only. Sources hidden."""
    lines = [f"graph {direction}"]
    for cls in spec.classes.values():
        node = _concrete_or_virtual_node(cls)
        if node is not None:
            lines.append(node)
    lines.extend(_fk_edges(spec))
    return "\n".join(lines)


def binding_graph(spec: Spec, *, direction: str = "LR") -> str:
    """Sources + binding edges only. Classes appear only if at least
    one source binds them. FK structure hidden."""
    lines = [f"graph {direction}"]
    for source in spec.sources.values():
        lines.append(f"  {source.name}([{source.name}])")
    bound_classes = {b.class_.name for b in spec.source_bindings}
    for cls_name in sorted(bound_classes):
        lines.append(f"  {cls_name}[{cls_name}]")
    for b in spec.source_bindings:
        lines.append(f"  {b.source.name} -.-> {b.class_.name}")
    return "\n".join(lines)


def to_mermaid(spec: Spec, *, direction: str = "LR") -> str:
    """Combined view — classes + FKs + source bindings in one graph.
    Visually busy on big specs (the cross-domain media_spec gets
    crowded fast). Prefer class_graph() / binding_graph() for
    legibility once the spec passes ~10 classes."""
    lines = [f"graph {direction}"]
    for cls in spec.classes.values():
        node = _concrete_or_virtual_node(cls)
        if node is not None:
            lines.append(node)
    lines.extend(_fk_edges(spec))
    for source in spec.sources.values():
        lines.append(f"  {source.name}([{source.name}])")
    for b in spec.source_bindings:
        lines.append(f"  {b.source.name} -.-> {b.class_.name}")
    return "\n".join(lines)
