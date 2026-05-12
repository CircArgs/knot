/**
 * Heuristic visual renderer for a GraphQL response.
 *
 * Three patterns recognised:
 *   1. `publishedSpec { classes { ... } }`  → class-card graph (reuse existing builder)
 *   2. `<entity>(limit: N)` returning a JSON-typed list   → one card per row
 *   3. `<entity>ByCanonicalId` / `<entity>Resolved` returning a JSON string
 *      → parse and render each top-level object as a card
 *
 * Anything else falls through to a "graph view not available" hint — the JSON
 * tab is still there for the long tail.
 */
import { useEffect, useMemo, useState } from "react";
import { ReactFlow, Background, Controls } from "@xyflow/react";
import type { Edge, Node } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { PublishedSpec } from "../../types/spec";
import { buildGraph } from "../../lib/buildGraph";
import { layoutGraph } from "../../lib/layout";
import ClassNode from "../nodes/ClassNode";
import SlotNode from "../nodes/SlotNode";
import SourceNode from "../nodes/SourceNode";
import ConstraintNode from "../nodes/ConstraintNode";

interface Props {
  data: unknown;
}

const nodeTypes = {
  specClass: ClassNode,
  specSlot: SlotNode,
  specSource: SourceNode,
  specConstraint: ConstraintNode,
} as const;

export default function GraphView({ data }: Props) {
  const pattern = useMemo(() => detectPattern(data), [data]);

  if (pattern.kind === "published-spec") {
    return <PublishedSpecGraph spec={pattern.spec} />;
  }
  if (pattern.kind === "row-list") {
    return <RowCards title={pattern.field} rows={pattern.rows} />;
  }
  if (pattern.kind === "json-blob") {
    return <RowCards title={pattern.field} rows={pattern.rows} />;
  }
  return (
    <div className="p-6 text-sm text-knot-muted">
      Graph view not available for this query shape. Try the JSON tab.
    </div>
  );
}

// ─── Pattern detection ────────────────────────────────────────────────────

type Pattern =
  | { kind: "none" }
  | { kind: "published-spec"; spec: PublishedSpec }
  | { kind: "row-list"; field: string; rows: Record<string, unknown>[] }
  | { kind: "json-blob"; field: string; rows: Record<string, unknown>[] };

function detectPattern(raw: unknown): Pattern {
  if (!raw || typeof raw !== "object") return { kind: "none" };
  const body = raw as { data?: Record<string, unknown> };
  const data = body.data;
  if (!data || typeof data !== "object") return { kind: "none" };

  // 1) publishedSpec shape
  const ps = data["publishedSpec"];
  if (
    ps &&
    typeof ps === "object" &&
    Array.isArray((ps as { classes?: unknown }).classes)
  ) {
    return { kind: "published-spec", spec: ps as PublishedSpec };
  }

  // Pick the first field on the data object that we recognise.
  for (const [field, value] of Object.entries(data)) {
    // 2) array of row objects — typical `movie(limit: 10)` return
    if (Array.isArray(value)) {
      const rows = value.filter(
        (v) => v && typeof v === "object" && !Array.isArray(v),
      ) as Record<string, unknown>[];
      if (rows.length > 0) return { kind: "row-list", field, rows };
    }
    // 3) JSON string (Resolved blob) or array of strings (ByCanonicalId)
    if (typeof value === "string") {
      const parsed = tryParseJSON(value);
      if (parsed !== undefined) {
        const rows = Array.isArray(parsed) ? parsed : [parsed];
        const objs = rows.filter(
          (r) => r && typeof r === "object" && !Array.isArray(r),
        ) as Record<string, unknown>[];
        if (objs.length > 0) return { kind: "json-blob", field, rows: objs };
      }
    }
  }
  return { kind: "none" };
}

function tryParseJSON(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return undefined;
  }
}

// ─── publishedSpec → class-card graph ─────────────────────────────────────

function PublishedSpecGraph({ spec }: { spec: PublishedSpec }) {
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  useEffect(() => {
    const built = buildGraph(fillMissing(spec));
    layoutGraph(built.nodes, built.edges)
      .then((laid) => {
        setNodes(laid as Node[]);
        setEdges(built.edges);
      })
      .catch(() => {
        setNodes(built.nodes as Node[]);
        setEdges(built.edges);
      });
  }, [spec]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      fitView
      minZoom={0.1}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
    >
      <Background />
      <Controls />
    </ReactFlow>
  );
}

/** Pad a partial publishedSpec response into the shape buildGraph expects. */
function fillMissing(p: Partial<PublishedSpec>): PublishedSpec {
  return {
    id: p.id ?? "",
    version: p.version ?? "",
    revision: p.revision ?? 0,
    contentHash: p.contentHash ?? "",
    slots: p.slots ?? [],
    classes: (p.classes ?? []).map((c) => ({
      name: c.name,
      abstract: c.abstract ?? false,
      description: c.description ?? null,
      isAName: c.isAName ?? null,
      mixinNames: c.mixinNames ?? [],
      slotNames: c.slotNames ?? [],
    })),
    sources: p.sources ?? [],
    constraints: p.constraints ?? [],
  };
}

// ─── Generic row cards ────────────────────────────────────────────────────

function RowCards({
  title,
  rows,
}: {
  title: string;
  rows: Record<string, unknown>[];
}) {
  return (
    <div className="p-4 overflow-auto h-full">
      <div className="text-xs uppercase tracking-wide text-knot-muted mb-2">
        {title} · {rows.length} row{rows.length === 1 ? "" : "s"}
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        {rows.map((row, i) => (
          <RowCard key={i} row={row} index={i} />
        ))}
      </div>
    </div>
  );
}

function RowCard({ row, index }: { row: Record<string, unknown>; index: number }) {
  const entries = Object.entries(row);
  return (
    <div className="border rounded shadow-sm bg-white">
      <div className="px-3 py-1.5 border-b text-xs text-knot-muted bg-slate-50 rounded-t">
        #{index}
      </div>
      <div className="p-3 text-xs space-y-1">
        {entries.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <span className="text-knot-muted shrink-0 min-w-[6rem]">{k}</span>
            <span className="font-mono break-all">{formatValue(v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatValue(v: unknown): string {
  if (v === null) return "null";
  if (v === undefined) return "—";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}
