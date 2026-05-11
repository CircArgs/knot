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
import { BUILTIN_TYPES } from "../types/spec";

/**
 * The default class-card-centric view keeps a class card as the ONLY node
 * kind. Slots / sources / constraints live as rows / chips inside the card
 * and become selectable via their own click handlers. Edges between class
 * cards represent only structural relationships: cross-class slot ranges,
 * is_a, and mixin inclusion.
 *
 * The "details" mode brings back the old graph where every named entity has
 * its own node and there are edges for every pointer (has / range / of /
 * identifier / primary / is_a / mixin). It's gated behind the "show
 * ontology details" toggle in the toolbar.
 */
export interface SpecNodeData extends Record<string, unknown> {
  label: string;
  entity: SpecEntity;
  /** Class-card render bundle — only set on class nodes in default mode. */
  card?: ClassCard;
}

/**
 * Full data needed to paint a class card. Built from the spec once per class
 * so the React Flow node component doesn't need to walk the spec itself.
 */
export interface ClassCard {
  cls: SpecClass;
  /** Resolved slot objects in the order they appear in `cls.slotNames`. */
  slots: SpecSlot[];
  /** Sources that reference this class as `entityClassName`. */
  sources: SpecSource[];
  /** Constraints that reference this class as `primaryClassName`. */
  constraints: SpecConstraint[];
}

export type SpecNode = Node<SpecNodeData>;
export type SpecEdge = Edge;

export interface BuildGraphOptions {
  /** Include the six standard primitives (string/integer/...). Default: true.
   *  Only meaningful in `details` mode. */
  includeBuiltinTypes?: boolean;
  /** Default `false`: one card per class + edges between cards.
   *  When `true`: the old graph with every entity as a node. */
  showOntologyDetails?: boolean;
}

/**
 * Map an entity kind to the custom React Flow node-type key registered in
 * `pages/SpecGraph.tsx`'s `nodeTypes` table.
 */
export const NODE_TYPE_BY_KIND: Record<SpecEntityKind, string> = {
  type: "specType",
  slot: "specSlot",
  class: "specClass",
  source: "specSource",
  constraint: "specConstraint",
};

/** Per-entity-kind ID prefix; entity name space is per-kind, names collide across kinds. */
const ID_PREFIX: Record<SpecEntityKind, string> = {
  type: "t",
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

export function buildGraph(
  spec: PublishedSpec,
  opts: BuildGraphOptions = {},
): { nodes: SpecNode[]; edges: SpecEdge[] } {
  if (opts.showOntologyDetails) {
    return buildDetailGraph(spec, opts.includeBuiltinTypes ?? true);
  }
  return buildClassCardGraph(spec);
}

// ──────────────────────────────────────────────────────────────────────────
// Default: class-card-centric graph
// ──────────────────────────────────────────────────────────────────────────

function buildClassCardGraph(
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

  // — Cross-class slot ranges (FK edges anchored at slot rows) —
  for (const cls of spec.classes) {
    for (const slotName of cls.slotNames) {
      const slot = slotByName.get(slotName);
      if (!slot) continue;
      if (slot.rangeKind !== "class" || !slot.rangeName) continue;
      if (!classNames.has(slot.rangeName)) continue;
      edges.push({
        id: `edge:fk:${cls.name}.${slotName}->${slot.rangeName}`,
        source: nodeId("class", cls.name),
        sourceHandle: slotHandleId(slotName),
        target: nodeId("class", slot.rangeName),
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

// ──────────────────────────────────────────────────────────────────────────
// Ontology-details mode: every entity is its own node (old behaviour)
// ──────────────────────────────────────────────────────────────────────────

function buildDetailGraph(
  spec: PublishedSpec,
  includeBuiltins: boolean,
): { nodes: SpecNode[]; edges: SpecEdge[] } {
  const nodes: SpecNode[] = [];
  const edges: SpecEdge[] = [];

  for (const t of spec.types) {
    if (!includeBuiltins && BUILTIN_TYPES.has(t.name)) continue;
    nodes.push({
      id: nodeId("type", t.name),
      type: NODE_TYPE_BY_KIND.type,
      position: { x: 0, y: 0 },
      data: { label: t.name, entity: { kind: "type", value: t } },
    });
  }
  for (const s of spec.slots) {
    nodes.push({
      id: nodeId("slot", s.name),
      type: NODE_TYPE_BY_KIND.slot,
      position: { x: 0, y: 0 },
      data: { label: s.name, entity: { kind: "slot", value: s } },
    });
  }
  for (const c of spec.classes) {
    nodes.push({
      id: nodeId("class", c.name),
      type: NODE_TYPE_BY_KIND.class,
      position: { x: 0, y: 0 },
      data: { label: c.name, entity: { kind: "class", value: c } },
    });
  }
  for (const src of spec.sources) {
    nodes.push({
      id: nodeId("source", src.name),
      type: NODE_TYPE_BY_KIND.source,
      position: { x: 0, y: 0 },
      data: { label: src.name, entity: { kind: "source", value: src } },
    });
  }
  for (const k of spec.constraints) {
    nodes.push({
      id: nodeId("constraint", k.name),
      type: NODE_TYPE_BY_KIND.constraint,
      position: { x: 0, y: 0 },
      data: { label: k.name, entity: { kind: "constraint", value: k } },
    });
  }

  const nodeIds = new Set(nodes.map((n) => n.id));
  const has = (kind: SpecEntityKind, name: string) =>
    nodeIds.has(nodeId(kind, name));

  for (const c of spec.classes) {
    for (const slotName of c.slotNames) {
      if (!has("slot", slotName)) continue;
      edges.push({
        id: `edge:has:${c.name}->${slotName}`,
        source: nodeId("class", c.name),
        target: nodeId("slot", slotName),
        label: "has",
        style: { stroke: "#94a3b8", strokeWidth: 1 },
        labelStyle: { fontSize: 10, fill: "#64748b" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
      });
    }
  }
  for (const s of spec.slots) {
    if (!s.rangeKind || !s.rangeName) continue;
    const targetKind: SpecEntityKind = s.rangeKind === "type" ? "type" : "class";
    if (!has(targetKind, s.rangeName)) continue;
    edges.push({
      id: `edge:range:${s.name}->${s.rangeName}`,
      source: nodeId("slot", s.name),
      target: nodeId(targetKind, s.rangeName),
      label: "range",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#0f766e" },
      style: { stroke: "#0f766e", strokeWidth: 1.25 },
      labelStyle: { fontSize: 10, fill: "#0f766e" },
      labelBgPadding: [2, 2],
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
    });
  }
  for (const src of spec.sources) {
    if (has("class", src.entityClassName)) {
      edges.push({
        id: `edge:of:${src.name}->${src.entityClassName}`,
        source: nodeId("source", src.name),
        target: nodeId("class", src.entityClassName),
        label: "of",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#7c3aed" },
        style: { stroke: "#7c3aed", strokeWidth: 1.25 },
        labelStyle: { fontSize: 10, fill: "#7c3aed" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
      });
    }
    if (has("slot", src.identifierSlotName)) {
      edges.push({
        id: `edge:identifier:${src.name}->${src.identifierSlotName}`,
        source: nodeId("source", src.name),
        target: nodeId("slot", src.identifierSlotName),
        label: "identifier",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#7c3aed" },
        style: { stroke: "#7c3aed", strokeWidth: 1, strokeDasharray: "3 3" },
        labelStyle: { fontSize: 10, fill: "#7c3aed" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
      });
    }
  }
  for (const k of spec.constraints) {
    if (!has("class", k.primaryClassName)) continue;
    edges.push({
      id: `edge:primary:${k.name}->${k.primaryClassName}`,
      source: nodeId("constraint", k.name),
      target: nodeId("class", k.primaryClassName),
      label: "primary",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#e11d48" },
      style: { stroke: "#e11d48", strokeWidth: 1.25 },
      labelStyle: { fontSize: 10, fill: "#e11d48" },
      labelBgPadding: [2, 2],
      labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
    });
  }
  for (const c of spec.classes) {
    if (c.isAName && has("class", c.isAName)) {
      edges.push({
        id: `edge:isa:${c.name}->${c.isAName}`,
        source: nodeId("class", c.name),
        target: nodeId("class", c.isAName),
        label: "is_a",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#1e40af" },
        style: { stroke: "#1e40af", strokeWidth: 1, strokeDasharray: "4 2" },
        labelStyle: { fontSize: 10, fill: "#1e40af" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
      });
    }
    for (const mx of c.mixinNames) {
      if (!has("class", mx)) continue;
      edges.push({
        id: `edge:mixin:${c.name}->${mx}`,
        source: nodeId("class", c.name),
        target: nodeId("class", mx),
        label: "mixin",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#1e40af" },
        style: { stroke: "#1e40af", strokeWidth: 1, strokeDasharray: "2 4" },
        labelStyle: { fontSize: 10, fill: "#1e40af" },
        labelBgPadding: [2, 2],
        labelBgStyle: { fill: "#ffffff", fillOpacity: 0.85 },
      });
    }
  }

  return { nodes, edges };
}
