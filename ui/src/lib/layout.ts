import ELK from "elkjs/lib/elk.bundled.js";
import type { ElkNode } from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";

const elk = new ELK();

/**
 * Generic horizontal ELK layered layout. Used by views that don't want the
 * spec-graph's two-axis source-tree-above-classes treatment.
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
    layoutOptions: COMMON_OPTIONS,
    children: nodes.map((n) => ({ id: n.id, width: nodeWidth, height: nodeHeight })),
    edges: edges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };

  const result = await elk.layout(graph);
  const positioned: Record<string, { x: number; y: number }> = {};
  for (const child of result.children ?? []) {
    if (child.id && typeof child.x === "number" && typeof child.y === "number") {
      positioned[child.id] = { x: child.x, y: child.y };
    }
  }
  return nodes.map((n) => ({ ...n, position: positioned[n.id] ?? n.position }));
}

const COMMON_OPTIONS: Record<string, string> = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.layered.spacing.nodeNodeBetweenLayers": "160",
  "elk.spacing.nodeNode": "80",
  "elk.spacing.edgeNode": "40",
  "elk.spacing.edgeEdge": "20",
  "elk.layered.spacing.edgeNodeBetweenLayers": "40",
  "elk.layered.spacing.edgeEdgeBetweenLayers": "20",
  "elk.edgeRouting": "ORTHOGONAL",
  "elk.layered.nodePlacement.strategy": "LINEAR_SEGMENTS",
  "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
  "elk.layered.crossingMinimization.semiInteractive": "true",
};

/**
 * Two-axis layout for the spec graph:
 *
 *  - **Classes flow left-to-right** along the ontology axis
 *    (abstract → concrete → defined). ELK lays them out using the class-to-class
 *    edges only (is_a / mixin / ClassRef).
 *
 *  - **Sources + SourceBindings hang above** their target class along the
 *    provenance axis. Each binding sits directly above its target class;
 *    each source sits above its bindings, centred over them. The
 *    `Source → SourceBinding` and `SourceBinding → Class` edges run vertically.
 *
 * The split makes the two distinct concerns (ontology vs. provenance) read
 * on independent axes — no more bindings and abstract classes competing for
 * the same vertical lane.
 */
export async function layoutSpecGraph<N extends Node>(
  nodes: N[],
  edges: Edge[],
  opts: { classWidth?: number; classHeight?: number; sourceWidth?: number; sourceHeight?: number; bindingWidth?: number; bindingHeight?: number } = {},
): Promise<N[]> {
  if (nodes.length === 0) return nodes;

  const classW = opts.classWidth ?? 300;
  const classH = opts.classHeight ?? 240;
  const sourceW = opts.sourceWidth ?? 200;
  const sourceH = opts.sourceHeight ?? 60;
  const bindingW = opts.bindingWidth ?? 220;
  const bindingH = opts.bindingHeight ?? 140;

  // Partition nodes by kind.
  const kindOf = (n: N): "class" | "source" | "binding" | "other" => {
    const k = (n.data as { entity?: { kind?: string } } | undefined)?.entity?.kind;
    if (k === "class") return "class";
    if (k === "source") return "source";
    if (k === "sourceBinding") return "binding";
    return "other";
  };

  const classNodes = nodes.filter((n) => kindOf(n) === "class");
  const sourceNodes = nodes.filter((n) => kindOf(n) === "source");
  const bindingNodes = nodes.filter((n) => kindOf(n) === "binding");

  if (classNodes.length === 0) {
    // No classes — fall back to the generic layout so users still see something.
    return layoutGraph(nodes, edges, { width: classW, height: classH });
  }

  // Pass 1: ELK lays out classes horizontally using ONLY class↔class edges.
  const classIds = new Set(classNodes.map((n) => n.id));
  const classEdges = edges.filter(
    (e) => classIds.has(e.source) && classIds.has(e.target),
  );

  // Pre-bucket bindings by target class so we can size class slots wide
  // enough to fit the binding row above each one. Without this, a class
  // with 3 bindings overhead (~800px wide) gets ELK-spaced as if it were
  // 300px and adjacent binding rows collide.
  const BINDING_GAP_X = 40;
  const bindingsByClassNameByName: Record<string, N[]> = {};
  for (const b of bindingNodes) {
    const bv = (b.data as { entity: { value: { className: string } } }).entity.value;
    (bindingsByClassNameByName[bv.className] = bindingsByClassNameByName[bv.className] ?? []).push(b);
  }
  const effectiveClassWidth = (className: string): number => {
    const nBindings = (bindingsByClassNameByName[className] ?? []).length;
    if (nBindings === 0) return classW;
    return Math.max(classW, nBindings * bindingW + (nBindings - 1) * BINDING_GAP_X);
  };

  const classGraph: ElkNode = {
    id: "classes",
    layoutOptions: COMMON_OPTIONS,
    children: classNodes.map((n) => {
      const clsName = (n.data as { entity: { value: { name: string } } }).entity.value.name;
      return { id: n.id, width: effectiveClassWidth(clsName), height: classH };
    }),
    edges: classEdges.map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  };

  const classResult = await elk.layout(classGraph);
  const classPositions: Record<string, { x: number; y: number }> = {};
  let classMaxY = 0;
  for (const child of classResult.children ?? []) {
    if (child.id && typeof child.x === "number" && typeof child.y === "number") {
      classPositions[child.id] = { x: child.x, y: child.y };
      if (child.y > classMaxY) classMaxY = child.y;
    }
  }

  // Pass 2: position sources + bindings ABOVE the class row.
  // Layout from top to bottom:
  //   - Source row at y = -SOURCE_Y_OFFSET
  //   - Binding row at y = -BINDING_Y_OFFSET
  //   - Classes start at y = 0
  // Shift classes DOWN so we don't render in negative space.
  const BINDING_GAP = 160;   // space between binding row and class row
  const SOURCE_GAP = 160;    // space between source row and binding row
  const bindingY = 0;
  const sourceY = -SOURCE_GAP - sourceH;
  const classY = bindingY + bindingH + BINDING_GAP;
  const yShift = -sourceY;  // make sourceY into 0 in final coords

  // Build a lookup: for each binding, the position of its target class.
  // Bindings have entity.value.className.
  const bindingTargetClass: Record<string, string> = {};
  for (const b of bindingNodes) {
    const bData = b.data as { entity: { value: { className: string } } };
    bindingTargetClass[b.id] = bData.entity.value.className;
  }

  // For each class, list of bindings that target it.
  const bindingsByClassName: Record<string, N[]> = {};
  for (const b of bindingNodes) {
    const cls = bindingTargetClass[b.id];
    (bindingsByClassName[cls] = bindingsByClassName[cls] ?? []).push(b);
  }

  // Place bindings: each binding sits directly above its target class. If a
  // class has multiple bindings, fan them horizontally above the class. The
  // fan is centered on the class's ELK-reserved width (which already accounts
  // for the fan, see effectiveClassWidth above), so adjacent classes' bindings
  // never collide.
  const bindingPositions: Record<string, { x: number; y: number }> = {};
  for (const cls of classNodes) {
    const clsData = cls.data as { entity: { value: { name: string } } };
    const className = clsData.entity.value.name;
    const cp = classPositions[cls.id];
    if (!cp || !className) continue;
    const bs = bindingsByClassName[className] ?? [];
    if (bs.length === 0) continue;
    const effW = effectiveClassWidth(className);
    const totalW = bs.length * bindingW + (bs.length - 1) * 40;
    const startX = cp.x + effW / 2 - totalW / 2;
    bs.forEach((b, i) => {
      bindingPositions[b.id] = {
        x: startX + i * (bindingW + 40),
        y: bindingY,
      };
    });
  }

  // For each source, find its bindings (those whose entity.value.sourceName === source.name).
  // Place source centred above its bindings.
  const sourcePositions: Record<string, { x: number; y: number }> = {};
  for (const src of sourceNodes) {
    const srcName = (src.data as { entity: { value: { name: string } } }).entity.value.name;
    const ownBindings = bindingNodes.filter((b) => {
      const bv = (b.data as { entity: { value: { sourceName: string } } }).entity.value;
      return bv.sourceName === srcName;
    });
    if (ownBindings.length === 0) {
      // Orphan source — drop it at the top-left.
      sourcePositions[src.id] = { x: 0, y: sourceY };
      continue;
    }
    const xs = ownBindings
      .map((b) => bindingPositions[b.id]?.x)
      .filter((x): x is number => x !== undefined);
    const avgX = xs.length > 0 ? xs.reduce((a, b) => a + b, 0) / xs.length : 0;
    sourcePositions[src.id] = {
      x: avgX + bindingW / 2 - sourceW / 2,
      y: sourceY,
    };
  }

  // Shift everything by yShift so the topmost row sits at y=0.
  // Also re-center each class card within its ELK-reserved width so it sits
  // visually under the centre of its bindings instead of at the left edge.
  const shiftedClass: Record<string, { x: number; y: number }> = {};
  for (const cls of classNodes) {
    const clsData = cls.data as { entity: { value: { name: string } } };
    const className = clsData.entity.value.name;
    const p = classPositions[cls.id];
    if (!p) continue;
    const effW = effectiveClassWidth(className);
    // Render class card centered within its reserved width.
    const xCentered = p.x + (effW - classW) / 2;
    shiftedClass[cls.id] = { x: xCentered, y: p.y + classY + yShift };
  }
  const shiftedBinding: Record<string, { x: number; y: number }> = {};
  for (const [id, p] of Object.entries(bindingPositions)) {
    shiftedBinding[id] = { x: p.x, y: p.y + yShift };
  }
  const shiftedSource: Record<string, { x: number; y: number }> = {};
  for (const [id, p] of Object.entries(sourcePositions)) {
    shiftedSource[id] = { x: p.x, y: p.y + yShift };
  }

  return nodes.map((n) => {
    let pos = n.position;
    if (shiftedClass[n.id]) pos = shiftedClass[n.id];
    else if (shiftedBinding[n.id]) pos = shiftedBinding[n.id];
    else if (shiftedSource[n.id]) pos = shiftedSource[n.id];
    return { ...n, position: pos };
  });
}
