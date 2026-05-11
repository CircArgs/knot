import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";

export default function ClassNode({ data, selected }: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "class") return null;
  const cls = data.entity.value;
  const ring = selected ? "ring-2 ring-blue-500" : "";
  const slotsPreview = cls.slotNames.slice(0, 5);
  const overflow = cls.slotNames.length - slotsPreview.length;
  return (
    <div
      className={`rounded-lg border border-blue-300 bg-blue-50 shadow-sm hover:shadow-md transition-shadow w-[220px] ${ring}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-blue-500" />
      <div className="px-3 py-2 border-b border-blue-200 flex items-center justify-between">
        <span className="font-medium text-blue-900 truncate" title={cls.name}>
          {cls.name}
        </span>
        <span
          className={`text-[10px] px-1.5 py-0.5 rounded font-mono ${
            cls.abstract ? "bg-blue-200 text-blue-800" : "bg-blue-100 text-blue-700"
          }`}
        >
          {cls.abstract ? "abstract" : "concrete"}
        </span>
      </div>
      <div className="px-3 py-2 text-xs text-blue-900/80">
        {cls.slotNames.length === 0 ? (
          <span className="italic text-blue-900/60">no slots</span>
        ) : (
          <ul className="space-y-0.5">
            {slotsPreview.map((s) => (
              <li key={s} className="font-mono truncate">
                · {s}
              </li>
            ))}
            {overflow > 0 && (
              <li className="italic text-blue-900/60">+{overflow} more…</li>
            )}
          </ul>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-blue-500" />
    </div>
  );
}
