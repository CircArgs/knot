import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";
import { BUILTIN_TYPES } from "../../types/spec";
import KindBadge from "./KindBadge";

export default function TypeNode({ data, selected }: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "type") return null;
  const t = data.entity.value;
  const ring = selected ? "ring-2 ring-amber-500" : "";
  const isBuiltin = BUILTIN_TYPES.has(t.name);
  return (
    <div
      className={`rounded-lg border border-amber-300 bg-amber-50 shadow-sm hover:shadow-md transition-shadow w-[220px] ${ring}`}
    >
      <Handle type="target" position={Position.Left} className="!bg-amber-500" />
      <div className="px-3 py-2 border-b border-amber-200 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <KindBadge kind="type" />
          <span className="font-medium text-amber-900 truncate" title={t.name}>
            {t.name}
          </span>
        </div>
        {isBuiltin && (
          <span className="text-[10px] bg-amber-200 text-amber-800 px-1.5 py-0.5 rounded font-mono">
            builtin
          </span>
        )}
      </div>
      <div className="px-3 py-2 text-xs text-amber-900/80">
        {t.base ? (
          <span className="font-mono">
            base: <span className="px-1 rounded bg-amber-100">{t.base}</span>
          </span>
        ) : (
          <span className="italic text-amber-900/60">no base</span>
        )}
        {t.pattern && (
          <div
            className="font-mono text-[10px] mt-1 truncate text-amber-900/60"
            title={t.pattern}
          >
            /{t.pattern}/
          </div>
        )}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-amber-500" />
    </div>
  );
}
