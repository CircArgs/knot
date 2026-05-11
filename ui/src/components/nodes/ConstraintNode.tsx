import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";

const SEVERITY_TONE: Record<string, string> = {
  error: "bg-red-600 text-white",
  warning: "bg-yellow-400 text-yellow-900",
};

export default function ConstraintNode({
  data,
  selected,
}: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "constraint") return null;
  const k = data.entity.value;
  const ring = selected ? "ring-2 ring-rose-500" : "";
  const sevTone = SEVERITY_TONE[String(k.severity).toLowerCase()] ?? "bg-rose-200 text-rose-900";
  return (
    <div
      className={`rounded-lg border border-rose-300 bg-rose-50 shadow-sm hover:shadow-md transition-shadow w-[220px] ${ring}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-rose-500" />
      <div className="px-3 py-2 border-b border-rose-200 flex items-center justify-between gap-2">
        <span className="font-medium text-rose-900 truncate" title={k.name}>
          {k.name}
        </span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded font-mono uppercase ${sevTone}`}
        >
          {k.severity}
        </span>
      </div>
      <div className="px-3 py-2 text-xs text-rose-900/80 space-y-0.5">
        <div className="truncate">
          primary: <span className="font-mono">{k.primaryClassName || "—"}</span>
        </div>
        {k.message && (
          <div className="text-[10px] text-rose-900/60 italic truncate" title={k.message}>
            {k.message}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-rose-500" />
    </div>
  );
}
