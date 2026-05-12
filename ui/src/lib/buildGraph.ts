import { MarkerType } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";

import type {
  PublishedSpec,
  SpecClass,
  SpecConstraint,
  SpecEntity,
  SpecEntityKind,
  SpecSlot,
  SpecSource,
} from "../types/spec";
import { isClassKind } from "../types/spec";

/**
 * Spec graph: one node shape (Class card), three edge kinds (ClassRef, is_a,
 * mixin). Slots, sources, and constraints live inline inside the class card
 * — a slot is a property of the class table, not a separately-joinable entity.
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
  /** Resolved slot objects in the order they appear in `cls.slotNames`. */
  slots: SpecSlot[];
  /** Sources that reference this class as `entityClassName`. */
  sources: SpecSource[];
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

/** Map a class entity to its custom React Flow node-type key.
 *
 *  Today only Class nodes are rendered. The map shape is preserved so the
 *  React Flow `nodeTypes` registration on the page can index by kind without
 *  ad-hoc switching. */
export const NODE_TYPE_BY_KIND: Record<SpecEntityKind, string> = {
  slot: "specSlot",        // not currently rendered as a standalone node
  class: "specClass",
  source: "specSource",    // not currently rendered as a standalone node
  constraint: "specConstraint", // not currently rendered as a standalone node
};

/** Per-entity-kind ID prefix; entity name space is per-kind, names collide across kinds. */
const ID_PREFIX: Record<SpecEntityKind, string> = {
  slot: "s",
  class: "c",
  source: "src",
  constraint: "k",
};

export function nodeId(kind: SpecEntityKind, name: string): string {
  return `${ID_PREFIX[kind]}:${name}`;
}

/** Stable handle id for the per-slot-row source handle in a class card. */
export function slotHandleId(slotName: string): string {
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
  slots: SpecSlot[],
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

  const slotByName = new Map(spec.slots.map((s) => [s.name, s]));
  const classNames = new Set(spec.classes.map((c) => c.name));

  // Pre-bucket sources + constraints by their owning class.
  const sourcesByClass = new Map<string, SpecSource[]>();
  for (const src of spec.sources) {
    const bucket = sourcesByClass.get(src.entityClassName) ?? [];
    bucket.push(src);
    sourcesByClass.set(src.entityClassName, bucket);
  }
  const constraintsByClass = new Map<string, SpecConstraint[]>();
  for (const k of spec.constraints) {
    const bucket = constraintsByClass.get(k.primaryClassName) ?? [];
    bucket.push(k);
    constraintsByClass.set(k.primaryClassName, bucket);
  }

  // — CLASS CARDS —
  for (const cls of spec.classes) {
    const slots = cls.slotNames
      .map((n) => slotByName.get(n))
      .filter((s): s is SpecSlot => !!s);
    const card: ClassCard = {
      cls,
      slots,
      sources: sourcesByClass.get(cls.name) ?? [],
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

  // — Cross-class ClassRef edges (FKs, anchored at slot rows) —
  for (const cls of spec.classes) {
    for (const slotName of cls.slotNames) {
      const slot = slotByName.get(slotName);
      if (!slot) continue;
      if (!isClassKind(slot.typeKind) || !slot.typeName) continue;
      if (!classNames.has(slot.typeName)) continue;
      edges.push({
        id: `edge:fk:${cls.name}.${slotName}->${slot.typeName}`,
        source: nodeId("class", cls.name),
        sourceHandle: slotHandleId(slotName),
        target: nodeId("class", slot.typeName),
        label: slotName,
        markerEnd: { type: MarkerType.ArrowClosed, color: "#2563eb" },
        style: { stroke: "#2563eb", strokeWidth: 1.5 },
        labelStyle: { fontSize: 10, fill: "#2563eb" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
      });
    }
  }

  // — is_a / mixin edges (class → class) —
  for (const cls of spec.classes) {
    if (cls.isAName && classNames.has(cls.isAName)) {
      edges.push({
        id: `edge:isa:${cls.name}->${cls.isAName}`,
        source: nodeId("class", cls.name),
        target: nodeId("class", cls.isAName),
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
        id: `edge:mixin:${cls.name}->${mx}`,
        source: nodeId("class", cls.name),
        target: nodeId("class", mx),
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
