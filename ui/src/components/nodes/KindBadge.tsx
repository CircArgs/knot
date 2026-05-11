import type { SpecEntityKind } from "../../types/spec";

/**
 * Tiny uppercase monospace pill, anchored top-left of a node header, that
 * names the entity kind. Lives in the same color family as the node bg so
 * it reads as a label, not a state badge (state badges anchor top-right).
 */
const TONE: Record<SpecEntityKind, string> = {
  class: "bg-blue-100 text-blue-800 border-blue-300",
  slot: "bg-green-100 text-green-800 border-green-300",
  type: "bg-amber-100 text-amber-800 border-amber-300",
  source: "bg-purple-100 text-purple-800 border-purple-300",
  constraint: "bg-rose-100 text-rose-800 border-rose-300",
};

export default function KindBadge({ kind }: { kind: SpecEntityKind }) {
  return (
    <span
      className={`text-[9px] font-mono uppercase tracking-wide px-1.5 py-0.5 rounded-full border ${TONE[kind]}`}
    >
      {kind}
    </span>
  );
}
