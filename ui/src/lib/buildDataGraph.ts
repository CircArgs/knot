/**
 * Build React Flow node + edge lists for the /data-graph page.
 *
 * Each canonical entity becomes one card-shaped node keyed
 * ``entity:{class}:{canonical_id}``. Cross-class FK edges anchor at a
 * per-slot source handle on the originating card and connect to the target
 * card's left side; if the FK target is outside the current view, no edge
 * is emitted (the card renders the dangling value inline instead).
 */
import { MarkerType } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";

import type { ContributionRow, ResolvedRecord } from "./dataApi";
import type { PublishedSpec, SpecClass, SpecSlot } from "../types/spec";
import { isArrayKind, isClassKind } from "../types/spec";

export interface EntityNodeData extends Record<string, unknown> {
  className: string;
  canonicalId: string;
  cls: SpecClass;
  slots: SpecSlot[];
  /** Per-source contribution rows for this canonical_id. */
  contributions: ContributionRow[];
  /** Trust-resolved view (or null while loading). */
  resolved: ResolvedRecord | null;
  /** True when resolved view is in flight. */
  resolvedLoading: boolean;
  /** True when this entity has been tombstoned. */
  tombstoned: boolean;
  /** Bubble: open action menu / select / submit corrections. */
  onAction?: (action: EntityAction) => void;
}

export type EntityAction =
  | { kind: "property"; canonicalId: string; className: string }
  | { kind: "merge"; canonicalId: string; className: string }
  | { kind: "split"; canonicalId: string; className: string }
  | { kind: "add"; canonicalId: string; className: string }
  | { kind: "tombstone"; canonicalId: string; className: string }
  | { kind: "reject_contribution"; canonicalId: string; className: string }
  | { kind: "select"; canonicalId: string; className: string };

export type EntityNode = Node<EntityNodeData>;
export type EntityEdge = Edge;

export function entityNodeId(className: string, canonicalId: string): string {
  return `entity:${className}:${canonicalId}`;
}

export function slotHandleId(slotName: string): string {
  return `slot-${slotName}`;
}

/**
 * Per-entity input bundle assembled by the DataGraph page from its async
 * fetches. The builder doesn't fetch anything — pure layout function.
 */
export interface EntityInput {
  className: string;
  canonicalId: string;
  contributions: ContributionRow[];
  resolved: ResolvedRecord | null;
  resolvedLoading: boolean;
  tombstoned: boolean;
}

export function buildDataGraph(
  spec: PublishedSpec,
  entities: EntityInput[],
): { nodes: EntityNode[]; edges: EntityEdge[] } {
  const classByName = new Map(spec.classes.map((c) => [c.name, c]));
  const slotByName = new Map(spec.slots.map((s) => [s.name, s]));
  // Set of `${className}:${canonicalId}` for FK target presence lookups.
  const presentSet = new Set(
    entities.map((e) => `${e.className}:${e.canonicalId}`),
  );

  const nodes: EntityNode[] = [];
  const edges: EntityEdge[] = [];

  for (const e of entities) {
    const cls = classByName.get(e.className);
    if (!cls) continue;
    const slots = cls.slotNames
      .map((n) => slotByName.get(n))
      .filter((s): s is SpecSlot => !!s);
    nodes.push({
      id: entityNodeId(e.className, e.canonicalId),
      type: "entityCard",
      position: { x: 0, y: 0 },
      data: {
        className: e.className,
        canonicalId: e.canonicalId,
        cls,
        slots,
        contributions: e.contributions,
        resolved: e.resolved,
        resolvedLoading: e.resolvedLoading,
        tombstoned: e.tombstoned,
      },
    });

    // Cross-class FK edges: walk the class-range slots; if the resolved value
    // is a non-null canonical_id whose target card is in the view, draw a
    // solid blue edge anchored at the slot row's source handle.
    if (!e.resolved) continue;
    for (const slot of slots) {
      if (!isClassKind(slot.typeKind) || !slot.typeName) continue;
      const targetClass = slot.typeName;
      const raw = e.resolved[slot.name];
      if (raw == null) continue;
      const targetIds = isArrayKind(slot.typeKind)
        ? (Array.isArray(raw) ? raw : []).map((v) => String(v))
        : [String(raw)];
      for (const targetId of targetIds) {
        if (!presentSet.has(`${targetClass}:${targetId}`)) continue;
        edges.push({
          id: `edge:fk:${e.className}:${e.canonicalId}.${slot.name}->${targetClass}:${targetId}`,
          source: entityNodeId(e.className, e.canonicalId),
          sourceHandle: slotHandleId(slot.name),
          target: entityNodeId(targetClass, targetId),
          label: slot.name,
          markerEnd: { type: MarkerType.ArrowClosed, color: "#2563eb" },
          style: { stroke: "#2563eb", strokeWidth: 1.5 },
          labelStyle: { fontSize: 10, fill: "#2563eb" },
          labelBgPadding: [2, 2],
          labelBgStyle: { fill: "#ffffff", fillOpacity: 0.9 },
        });
      }
    }
  }

  return { nodes, edges };
}
