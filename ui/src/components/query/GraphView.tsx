/**
 * Subgraph view of a GraphQL data-plane response.
 *
 * Walks the response tree alongside the introspection schema. Every object
 * that carries a `canonicalId` field is treated as an entity instance; we
 * emit one React Flow node per `(typeName, canonicalId)` pair (deduped, so
 * the same Person referenced from three different Credits surfaces as one
 * node with three incoming edges). Each nested entity field becomes a
 * directed edge labelled by the GraphQL field name.
 *
 * No introspection of __typename is required — the schema lookup (Query →
 * field → return type, recursively) tells us the type at every level.
 *
 * If the response carries no entities (typename has no canonicalId), or if
 * the schema isn't loaded yet, we surface a hint instead of an empty graph.
 */
import { useEffect, useMemo, useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
} from "@xyflow/react";
import type { Edge, Node, NodeProps } from "@xyflow/react";
import type { GraphQLSchema, GraphQLObjectType, GraphQLOutputType } from "graphql";
import { getNamedType, isObjectType } from "graphql";
import "@xyflow/react/dist/style.css";

import { layoutGraph } from "../../lib/layout";

interface Props {
  data: unknown;
  schema: GraphQLSchema | null;
}

interface EntityNodeData {
  typeName: string;
  canonicalId: string;
  // Scalar properties surfaced on the card (everything in the row except
  // nested objects / arrays / canonicalId itself).
  scalars: Record<string, unknown>;
  [key: string]: unknown;
}

export default function GraphView({ data, schema }: Props) {
  const built = useMemo(() => buildGraph(data, schema), [data, schema]);
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);

  useEffect(() => {
    layoutGraph(built.nodes, built.edges, { width: 240, height: 120 })
      .then((laid) => {
        setNodes(laid as Node[]);
        setEdges(built.edges);
      })
      .catch(() => {
        setNodes(built.nodes as Node[]);
        setEdges(built.edges);
      });
  }, [built]);

  if (!schema) {
    return (
      <div className="p-6 text-sm text-knot-muted">
        Loading schema… (graph view needs introspection)
      </div>
    );
  }
  if (built.nodes.length === 0) {
    return (
      <div className="p-6 text-sm text-knot-muted">
        No entity rows in the response. Add <code>canonicalId</code> to your
        selection sets to render a graph (e.g. <code>movie {"{"} canonicalId
        title {"}"}</code>).
      </div>
    );
  }

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={{ entity: EntityNode }}
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

// ─── Response → (nodes, edges) walker ────────────────────────────────────

interface BuiltGraph {
  nodes: Node[];
  edges: Edge[];
}

function buildGraph(raw: unknown, schema: GraphQLSchema | null): BuiltGraph {
  if (!raw || typeof raw !== "object" || !schema) return { nodes: [], edges: [] };
  const body = raw as { data?: Record<string, unknown> };
  const data = body.data;
  if (!data || typeof data !== "object") return { nodes: [], edges: [] };

  const queryType = schema.getQueryType();
  if (!queryType) return { nodes: [], edges: [] };

  const nodeMap = new Map<string, Node>();
  const edges: Edge[] = [];
  const seenEdges = new Set<string>();

  function nodeId(typeName: string, cid: string): string {
    return `${typeName}::${cid}`;
  }

  function emitNode(typeName: string, cid: string, scalars: Record<string, unknown>): string {
    const id = nodeId(typeName, cid);
    if (!nodeMap.has(id)) {
      nodeMap.set(id, {
        id,
        type: "entity",
        position: { x: 0, y: 0 },
        data: { typeName, canonicalId: cid, scalars } satisfies EntityNodeData,
      });
    }
    return id;
  }

  function emitEdge(from: string, to: string, label: string): void {
    const key = `${from}->${to}:${label}`;
    if (seenEdges.has(key)) return;
    seenEdges.add(key);
    edges.push({
      id: key,
      source: from,
      target: to,
      label,
      labelStyle: { fontSize: 10, fill: "#475569" },
      labelBgStyle: { fill: "#f8fafc" },
      labelBgPadding: [3, 1],
      type: "smoothstep",
    });
  }

  /** Walk a JSON value alongside a GraphQL type. Emit node for entity-shaped
   *  values; recurse into nested object/list fields. */
  function walk(value: unknown, gqlType: GraphQLOutputType, parentNodeId: string | null, fieldLabel: string | null): void {
    if (value == null) return;
    const named = getNamedType(gqlType);

    // Arrays: walk each element with the same type
    if (Array.isArray(value)) {
      for (const item of value) {
        walk(item, gqlType, parentNodeId, fieldLabel);
      }
      return;
    }

    if (!isObjectType(named) || typeof value !== "object") return;

    const obj = value as Record<string, unknown>;
    const cidRaw = obj["canonicalId"];
    let myNodeId: string | null = null;
    if (typeof cidRaw === "string" && cidRaw) {
      // Entity: emit a node.
      const scalars: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj)) {
        if (k === "canonicalId") continue;
        if (v != null && typeof v === "object") continue; // nested handled below
        scalars[k] = v;
      }
      myNodeId = emitNode(named.name, cidRaw, scalars);
      if (parentNodeId && fieldLabel) {
        emitEdge(parentNodeId, myNodeId, fieldLabel);
      }
    }

    // Recurse into fields that point to other object types.
    const fields = (named as GraphQLObjectType).getFields();
    for (const [fieldName, childValue] of Object.entries(obj)) {
      const f = fields[fieldName];
      if (!f) continue;
      walk(childValue, f.type, myNodeId ?? parentNodeId, fieldName);
    }
  }

  // Top level: walk each root field.
  const rootFields = queryType.getFields();
  for (const [fieldName, value] of Object.entries(data)) {
    const f = rootFields[fieldName];
    if (!f) continue;
    walk(value, f.type, null, null);
  }

  return { nodes: Array.from(nodeMap.values()), edges };
}

// ─── Minimal node component for entity instances ──────────────────────────

function EntityNode({ data }: NodeProps & { data: EntityNodeData }) {
  const { typeName, canonicalId, scalars } = data;
  // Identifier-ish fields first if present, then any other scalars; cap
  // the rendered list so cards stay compact.
  const entries = Object.entries(scalars).slice(0, 6);
  return (
    <div className="border border-slate-300 rounded bg-white shadow-sm w-[240px] text-xs">
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />
      <div className="px-2 py-1 border-b bg-slate-50 rounded-t flex items-center justify-between gap-2">
        <span className="font-semibold text-slate-900 truncate" title={typeName}>
          {typeName}
        </span>
        <span className="font-mono text-[10px] text-slate-500 truncate" title={canonicalId}>
          {canonicalId}
        </span>
      </div>
      <div className="p-2 space-y-0.5">
        {entries.length === 0 && (
          <div className="italic text-slate-400">no scalar fields selected</div>
        )}
        {entries.map(([k, v]) => (
          <div key={k} className="flex gap-2">
            <span className="text-slate-500 shrink-0 min-w-[5rem] truncate">{k}</span>
            <span className="font-mono truncate" title={String(v)}>
              {v == null ? "—" : String(v)}
            </span>
          </div>
        ))}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
}
