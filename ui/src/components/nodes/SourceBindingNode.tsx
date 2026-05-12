import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";

/**
 * SourceBinding junction node — rendered as a cut-corner octagon
 * (matching junction class cards) in violet to distinguish it from
 * class cards (blue) and source cards (purple).
 *
 * Shows the binding's trust prior as α/(α+β) posterior mean, mapping
 * count, and required slot list.
 *
 * Three outgoing source handles (right side):
 *   - sb-source: to the source card
 *   - sb-class: to the bound class card
 *   - sb-idslot: to the identifier slot row on the class card
 */
export default function SourceBindingNode({
  data,
  selected,
}: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "sourceBinding") return null;
  const b = data.entity.value;

  const [alpha, beta] = b.trustPrior;
  const posteriorMean = alpha / (alpha + beta);
  const posteriorPct = Math.round(posteriorMean * 100);

  const ring = selected ? "ring-2 ring-violet-600" : "ring-1 ring-violet-300";

  // Chamfered octagon clip — same as junction class cards.
  const clipStyle: React.CSSProperties = {
    clipPath:
      "polygon(12px 0%, calc(100% - 12px) 0%, 100% 12px, 100% calc(100% - 12px), calc(100% - 12px) 100%, 12px 100%, 0% calc(100% - 12px), 0% 12px)",
  };

  return (
    <div
      style={clipStyle}
      className={`border border-violet-400 bg-violet-50 shadow-sm hover:shadow-md transition-shadow w-[240px] text-xs ${ring}`}
    >
      {/* Incoming: none expected (bindings are sources of edges) */}
      <Handle type="target" position={Position.Left} className="!bg-violet-500 !opacity-0" />

      {/* Header */}
      <div className="px-3 py-1.5 border-b border-violet-200 bg-violet-100 flex items-center justify-between gap-2">
        <span className="font-semibold text-violet-900 truncate" title={data.label as string}>
          {b.sourceName}
          <span className="text-violet-500 mx-1">→</span>
          {b.className}
        </span>
        <span className="text-[9px] uppercase px-1.5 py-0.5 rounded font-mono tracking-wide bg-violet-200 text-violet-800 shrink-0">
          binding
        </span>
      </div>

      {/* Body */}
      <div className="px-3 py-2 space-y-1.5">
        {/* Trust prior */}
        <div className="flex items-center justify-between gap-2">
          <span className="text-violet-600 w-24 shrink-0">trust prior</span>
          <span className="font-mono text-violet-900">
            Beta({alpha},{beta}) ≈ {posteriorPct}%
          </span>
        </div>

        {/* Identifier slot */}
        <div className="flex items-center justify-between gap-2">
          <span className="text-violet-600 w-24 shrink-0">id slot</span>
          <span className="font-mono text-violet-900 truncate">{b.identifierSlotName}</span>
        </div>

        {/* Mapping count */}
        <div className="flex items-center justify-between gap-2">
          <span className="text-violet-600 w-24 shrink-0">mappings</span>
          <span className="font-mono text-violet-900">{b.mappings.length}</span>
        </div>

        {/* Required slots */}
        {b.requiredSlotNames.length > 0 && (
          <div className="flex items-start justify-between gap-2">
            <span className="text-violet-600 w-24 shrink-0 pt-px">required</span>
            <span className="font-mono text-violet-900 text-right truncate">
              {b.requiredSlotNames.join(", ")}
            </span>
          </div>
        )}
      </div>

      {/* Three outgoing source handles on the right.
          sourceBinding edges reference these by handle id in buildGraph.ts. */}
      <Handle
        type="source"
        position={Position.Right}
        id="sb-source"
        style={{ top: "30%" }}
        className="!bg-violet-500"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="sb-class"
        style={{ top: "55%" }}
        className="!bg-violet-500"
      />
      <Handle
        type="source"
        position={Position.Right}
        id="sb-idslot"
        style={{ top: "80%" }}
        className="!bg-violet-400"
      />
    </div>
  );
}
