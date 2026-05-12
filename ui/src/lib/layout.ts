import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";

const elk = new ELK();

/**
 * Run elkjs layered layout once. Caller stores the result and lets React Flow
 * handle drag/manual positioning thereafter.
 *
 * Generic over node data so spec-graph and data-graph can share it; the
 * algorithm only needs id/width/height/edge-endpoints.
 */
export async function layoutGraph<N extends Node>(
  nodes: N[],
  edges: Edge[],
  opts: { width?: number; height?: number } = {},
): Promise<N[]> {
  if (nodes.length === 0) return nodes;

  const nodeWidth = opts.width ?? 220;
  const nodeHeight = opts.height ?? 110;

  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      // Plenty of room between layers so long FK edges have space to bend
      // around the rectangular class cards without crossing them.
      "elk.layered.spacing.nodeNodeBetweenLayers": "160",
      "elk.spacing.nodeNode": "80",
      // Keep edges away from node boundaries — prevents the "edge clips into
      // a class card" effect that happens with tight spacing.
      "elk.spacing.edgeNode": "40",
      "elk.spacing.edgeEdge": "20",
      "elk.layered.spacing.edgeNodeBetweenLayers": "40",
      "elk.layered.spacing.edgeEdgeBetweenLayers": "20",
      // ORTHOGONAL routing gives right-angle Manhattan edges that dodge
      // intermediate nodes cleanly. The default POLYLINE routing tends to
      // cut diagonally through other cards.
      "elk.edgeRouting": "ORTHOGONAL",
      // LINEAR_SEGMENTS aligns nodes within a layer (cleaner verticals)
      // and tends to produce fewer crossings than NETWORK_SIMPLEX on
      // graphs with many parallel edges.
      "elk.layered.nodePlacement.strategy": "LINEAR_SEGMENTS",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
      "elk.layered.crossingMinimization.semiInteractive": "true",
    },
    children: nodes.map((n) => ({
      id: n.id,
      width: nodeWidth,
      height: nodeHeight,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const result = await elk.layout(graph);
  const positioned: Record<string, { x: number; y: number }> = {};
  for (const child of result.children ?? []) {
    if (child.id && typeof child.x === "number" && typeof child.y === "number") {
      positioned[child.id] = { x: child.x, y: child.y };
    }
  }

  return nodes.map((n) => ({
    ...n,
    position: positioned[n.id] ?? n.position,
  }));
}
