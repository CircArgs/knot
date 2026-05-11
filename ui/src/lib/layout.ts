import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk.bundled.js";

import type { SpecEdge, SpecNode } from "./buildGraph";

const elk = new ELK();

/**
 * Run elkjs layered layout once. Caller stores the result and lets React Flow
 * handle drag/manual positioning thereafter.
 */
export async function layoutGraph(
  nodes: SpecNode[],
  edges: SpecEdge[],
  opts: { width?: number; height?: number } = {},
): Promise<SpecNode[]> {
  if (nodes.length === 0) return nodes;

  const nodeWidth = opts.width ?? 220;
  const nodeHeight = opts.height ?? 110;

  const graph: ElkNode = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "80",
      "elk.spacing.nodeNode": "40",
      "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
      "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
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
