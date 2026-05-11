import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";
import KindBadge from "./KindBadge";

export default function SourceNode({ data, selected }: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "source") return null;
  const src = data.entity.value;
  const ring = selected ? "ring-2 ring-purple-500" : "";
  return (
    <div
      className={`rounded-lg border border-purple-300 bg-purple-50 shadow-sm hover:shadow-md transition-shadow w-[220px] ${ring}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-purple-500" />
      <div className="px-3 py-2 border-b border-purple-200 flex items-center gap-1.5">
        <KindBadge kind="source" />
        <span className="font-medium text-purple-900 truncate block" title={src.name}>
          {src.name}
        </span>
      </div>
      <div className="px-3 py-2 text-xs text-purple-900/80 space-y-1">
        <div className="truncate">
          of <span className="font-mono">{src.entityClassName || "—"}</span>
        </div>
        <div className="truncate text-purple-900/60">
          identifier: <span className="font-mono">{src.identifierSlotName || "—"}</span>
        </div>
      </div>
      <Handle type="source" position={Position.Right} className="!bg-purple-500" />
    </div>
  );
}
