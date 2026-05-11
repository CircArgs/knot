import { MarkerType } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";

import type { PublishedSpec, SpecEntity, SpecEntityKind } from "../types/spec";
import { BUILTIN_TYPES } from "../types/spec";

/**
 * React Flow `node.data` payload. `entity` carries the typed source object
 * so the right-side property panel + double-click form can switch on `kind`.
 */
export interface SpecNodeData extends Record<string, unknown> {
  label: string;
  entity: SpecEntity;
}

export type SpecNode = Node<SpecNodeData>;
export type SpecEdge = Edge;

export interface BuildGraphOptions {
  /** Include the six standard primitives (string/integer/...). Default: true. */
  includeBuiltinTypes?: boolean;
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

/**
 * Build React Flow nodes + edges from a spec payload.
 *
 * Positions are seeded to (0, 0) — the caller is expected to feed the result
 * through `lib/layout.ts` to get real positions.
 */
export function buildGraph(
  spec: PublishedSpec,
  opts: BuildGraphOptions = {},
): { nodes: SpecNode[]; edges: SpecEdge[] } {
  const includeBuiltins = opts.includeBuiltinTypes ?? true;

  const nodes: SpecNode[] = [];
  const edges: SpecEdge[] = [];

  // — TYPES —
  for (const t of spec.types) {
    if (!includeBuiltins && BUILTIN_TYPES.has(t.name)) continue;
    nodes.push({
      id: nodeId("type", t.name),
      type: NODE_TYPE_BY_KIND.type,
      position: { x: 0, y: 0 },
      data: { label: t.name, entity: { kind: "type", value: t } },
    });
  }

  // — SLOTS —
  for (const s of spec.slots) {
    nodes.push({
      id: nodeId("slot", s.name),
      type: NODE_TYPE_BY_KIND.slot,
      position: { x: 0, y: 0 },
      data: { label: s.name, entity: { kind: "slot", value: s } },
    });
  }

  // — CLASSES —
  for (const c of spec.classes) {
    nodes.push({
      id: nodeId("class", c.name),
      type: NODE_TYPE_BY_KIND.class,
      position: { x: 0, y: 0 },
      data: { label: c.name, entity: { kind: "class", value: c } },
    });
  }

  // — SOURCES —
  for (const src of spec.sources) {
    nodes.push({
      id: nodeId("source", src.name),
      type: NODE_TYPE_BY_KIND.source,
      position: { x: 0, y: 0 },
      data: { label: src.name, entity: { kind: "source", value: src } },
    });
  }

  // — CONSTRAINTS —
  for (const k of spec.constraints) {
    nodes.push({
      id: nodeId("constraint", k.name),
      type: NODE_TYPE_BY_KIND.constraint,
      position: { x: 0, y: 0 },
      data: { label: k.name, entity: { kind: "constraint", value: k } },
    });
  }

  // Track which nodes exist so edges to filtered-out builtins are skipped.
  const nodeIds = new Set(nodes.map((n) => n.id));
  const has = (kind: SpecEntityKind, name: string) =>
    nodeIds.has(nodeId(kind, name));

  // — CLASS → SLOT edges ("has") —
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

  // — SLOT → TYPE / CLASS edges ("range") —
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

  // — SOURCE → CLASS ("of") + SOURCE → SLOT ("identifier") —
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

  // — CONSTRAINT → CLASS ("primary") —
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

  // — CLASS → CLASS ("is_a") + CLASS → CLASS ("mixin"), both dashed —
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
