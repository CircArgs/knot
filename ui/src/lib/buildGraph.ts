import { MarkerType } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";

import type {
  PublishedSpec,
  SpecClass,
  SpecConstraint,
  SpecEntity,
  SpecEntityKind,
  SpecProperty,
} from "../types/spec";
import { isClassKind } from "../types/spec";

/**
 * Spec graph: one node shape (Class card), plus Source cards and
 * SourceBinding junction nodes. Three edge kinds on class cards (ClassRef,
 * is_a, mixin). Slots and constraints live inline inside the class card.
 *
 * Reified relations (a class with ≥2 ClassRef slots — the Credit-between-Movie-
 * and-Person pattern) are rendered with the same card shape but get a
 * cut-corner outline + violet accent so junctions are scannable in dense
 * graphs. See `isJunctionClass`.
 */
export interface SpecNodeData extends Record<string, unknown> {
  label: string;
  entity: SpecEntity;
  /** Class-card render bundle — set on every class node. */
  card?: ClassCard;
}

export interface ClassCard {
  cls: SpecClass;
  /** The class's own slots (inline on the class). */
  properties: SpecProperty[];
  /** Constraints that reference this class as `primaryClassName`. */
  constraints: SpecConstraint[];
  /** True when this class has ≥2 ClassRef slots whose targets exist on the
   *  spec — i.e. it's a reified relation (an "associative entity" in ERD
   *  terms). Rendered with a distinct cut-corner shape so junction nodes
   *  are scannable in a dense graph. */
  isJunction?: boolean;
}

export type SpecNode = Node<SpecNodeData>;
export type SpecEdge = Edge;

/** Map a class entity to its custom React Flow node-type key. */
export const NODE_TYPE_BY_KIND: Record<SpecEntityKind, string> = {
  property: "specSlot",              // not currently rendered as a standalone node
  class: "specClass",
  source: "specSource",
  sourceBinding: "specSourceBinding",
  constraint: "specConstraint",  // not currently rendered as a standalone node
};

/** Per-entity-kind ID prefix; entity name space is per-kind, names collide across kinds. */
const ID_PREFIX: Record<SpecEntityKind, string> = {
  property: "s",
  class: "c",
  source: "src",
  sourceBinding: "sb",
  constraint: "k",
};

export function nodeId(kind: SpecEntityKind, name: string): string {
  return `${ID_PREFIX[kind]}:${name}`;
}

/** Stable node id for a source binding: sb:{sourceName}__{className} */
export function bindingNodeId(sourceName: string, className: string): string {
  return `sb:${sourceName}__${className}`;
}

/** Stable handle id for the per-slot-row source handle in a class card. */
export function propertyHandleId(slotName: string): string {
  return `slot-${slotName}`;
}

/**
 * A class is a "junction" / reified relation when it has ≥2 ClassRef slots
 * whose targets exist on the spec. The classic example: `Credit` connecting
 * `Movie` ←→ `Person` with extra slots (`role`, `billing_order`).
 *
 * Identifier / Primitive slots are ignored — only cross-class FKs count.
 */
export function isJunctionClass(
  properties: SpecProperty[],
  classNames: Set<string>,
): boolean {
  let classRefs = 0;
  for (const s of slots) {
    if (isClassKind(s.typeKind) && s.typeName && classNames.has(s.typeName)) {
      classRefs += 1;
      if (classRefs >= 2) return true;
    }
  }
  return false;
}

export function buildGraph(
  spec: PublishedSpec,
): { nodes: SpecNode[]; edges: SpecEdge[] } {
  const nodes: SpecNode[] = [];
  const edges: SpecEdge[] = [];

  const classNames = new Set(spec.classes.map((c) => c.name));

  // Pre-bucket constraints by their owning class.
  const constraintsByClass = new Map<string, SpecConstraint[]>();
  for (const k of spec.constraints) {
    const bucket = constraintsByClass.get(k.primaryClassName) ?? [];
    bucket.push(k);
    constraintsByClass.set(k.primaryClassName, bucket);
  }

  // — CLASS CARDS —
  for (const cls of spec.classes) {
    const slots = cls.properties;
    const card: ClassCard = {
      cls,
      slots,
      constraints: constraintsByClass.get(cls.name) ?? [],
      isJunction: isJunctionClass(slots, classNames),
    };
    nodes.push({
      id: nodeId("class", cls.name),
      type: NODE_TYPE_BY_KIND.class,
      position: { x: 0, y: 0 },
      data: {
        label: cls.name,
        entity: { kind: "class", value: cls },
        card,
      },
    });
  }

  // — SOURCE CARDS —
  for (const src of spec.sources) {
    nodes.push({
      id: nodeId("source", src.name),
      type: NODE_TYPE_BY_KIND.source,
      position: { x: 0, y: 0 },
      data: {
        label: src.name,
        entity: { kind: "source", value: src },
      },
    });
  }

  // — SOURCE BINDING NODES + EDGES —
  for (const binding of spec.sourceBindings ?? []) {
    const bNodeId = bindingNodeId(binding.sourceName, binding.className);
    nodes.push({
      id: bNodeId,
      type: NODE_TYPE_BY_KIND.sourceBinding,
      position: { x: 0, y: 0 },
      data: {
        label: `${binding.sourceName}→${binding.className}`,
        entity: { kind: "sourceBinding", value: binding },
      },
    });

    // Edge: Source → SourceBinding (reversed from the original sb-src
    // direction so the layered layout reads as the natural data flow:
    // Source → Binding → Class → … with mixin / is_a parents trailing
    // at the right. The edge label still reads "source" — it describes
    // the binding's source side regardless of arrow direction.
    edges.push({
      id: `edge:sb-src:${binding.sourceName}__${binding.className}`,
      type: "smoothstep",
      source: nodeId("source", binding.sourceName),
      target: bNodeId,
      label: "feeds",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#7c3aed" },
      style: { stroke: "#7c3aed", strokeWidth: 1.25 },
      labelStyle: { fontSize: 10, fill: "#7c3aed" },
      labelBgPadding: [2, 2],
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
    });

    // Edge: SourceBinding → Class
    if (classNames.has(binding.className)) {
      edges.push({
        id: `edge:sb-cls:${binding.sourceName}__${binding.className}`,
        type: "smoothstep",
        source: bNodeId,
        target: nodeId("class", binding.className),
        label: "binds",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#7c3aed" },
        style: { stroke: "#7c3aed", strokeWidth: 1.25 },
        labelStyle: { fontSize: 10, fill: "#7c3aed" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      });
    }

    // Note: a third edge (SourceBinding → identifier slot row) is intentionally
    // not drawn. The slot row's handle is type="source" (used for outgoing FK
    // edges) and React Flow won't terminate an incoming edge on a source-only
    // handle. The identifier slot name is already shown inside the binding
    // card's body, and the sb-cls edge above conveys the class connection, so
    // this third edge would be visual noise + a render error in the console.
  }

  // — Cross-class ClassRef edges (FKs, anchored at slot rows) —
  for (const cls of spec.classes) {
    for (const slot of cls.properties) {
      if (!isClassKind(slot.typeKind) || !slot.typeName) continue;
      if (!classNames.has(slot.typeName)) continue;
      edges.push({
        id: `edge:fk:${cls.name}.${slot.name}->${slot.typeName}`,
        type: "smoothstep",
        source: nodeId("class", cls.name),
        sourceHandle: propertyHandleId(slot.name),
        target: nodeId("class", slot.typeName),
        label: slot.name,
        markerEnd: { type: MarkerType.ArrowClosed, color: "#2563eb" },
        style: { stroke: "#2563eb", strokeWidth: 1.5 },
        labelStyle: { fontSize: 10, fill: "#2563eb" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      });
    }
  }

  // — is_a / mixin edges (parent / mixin → child) —
  // Direction is reversed from the natural "child IS-A parent" reading so
  // the layered layout places abstract parents + mixin contributors in
  // earlier layers (to the LEFT) than the concrete classes that consume
  // them. The arrow + label still reads correctly because we flip the
  // marker: the visual arrowhead lands at the child, with "is_a" / "mixin"
  // labels next to it. Defined classes (which themselves have is_a) trail
  // to the rightmost layer.
  for (const cls of spec.classes) {
    if (cls.isAName && classNames.has(cls.isAName)) {
      edges.push({
        id: `edge:isa:${cls.isAName}->${cls.name}`,
        type: "smoothstep",
        source: nodeId("class", cls.isAName),
        sourceHandle: "class-source",
        target: nodeId("class", cls.name),
        label: "is_a",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#475569" },
        style: { stroke: "#475569", strokeWidth: 1.25 },
        labelStyle: { fontSize: 10, fill: "#475569" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      });
    }
    for (const mx of cls.mixinNames) {
      if (!classNames.has(mx)) continue;
      edges.push({
        id: `edge:mixin:${mx}->${cls.name}`,
        type: "smoothstep",
        source: nodeId("class", mx),
        sourceHandle: "class-source",
        target: nodeId("class", cls.name),
        label: "mixin",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#94a3b8" },
        style: { stroke: "#94a3b8", strokeWidth: 1, strokeDasharray: "4 3" },
        labelStyle: { fontSize: 10, fill: "#64748b" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      });
    }
  }

  return { nodes, edges };
}
