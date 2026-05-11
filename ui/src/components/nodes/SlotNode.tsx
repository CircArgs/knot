import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";

export default function SlotNode({ data, selected }: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "slot") return null;
  const s = data.entity.value;
  const ring = selected ? "ring-2 ring-green-500" : "";
  const chips: { label: string; tone: string }[] = [];
  if (s.identifier) chips.push({ label: "id", tone: "bg-emerald-700 text-white" });
  if (s.required) chips.push({ label: "req", tone: "bg-emerald-200 text-emerald-900" });
  if (s.multivalued) chips.push({ label: "many", tone: "bg-emerald-200 text-emerald-900" });
  return (
    <div
      className={`rounded-lg border border-green-300 bg-green-50 shadow-sm hover:shadow-md transition-shadow w-[220px] ${ring}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-green-500" />
      <div className="px-3 py-2 border-b border-green-200">
        <span className="font-medium text-green-900 truncate block" title={s.name}>
          {s.name}
        </span>
      </div>
      <div className="px-3 py-2 text-xs text-green-900/80 space-y-1.5">
        <div className="font-mono truncate" title={s.rangeName ?? ""}>
          → {s.rangeName ? `${s.rangeName}` : <span className="italic">unranged</span>}
          {s.rangeKind && (
            <span className="ml-1 text-[9px] text-green-700/70">({s.rangeKind})</span>
          )}
        </div>
        {chips.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {chips.map((c) => (
              <span
                key={c.label}
                className={`text-[9px] uppercase font-medium px-1.5 py-0.5 rounded ${c.tone}`}
              >
                {c.label}
              </span>
            ))}
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-green-500" />
    </div>
  );
}
