import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";
import { slotHandleId } from "../../lib/buildGraph";
import { BUILTIN_TYPES } from "../../types/spec";
import type {
  SpecConstraint,
  SpecSlot,
  SpecSource,
} from "../../types/spec";

/**
 * Class card — the only node kind in the default class-card-centric view.
 * Renders header + per-slot rows + sources / constraints chip lines.
 * Click handlers stop propagation so clicking a slot row / source chip /
 * constraint chip routes a "select non-node entity" event to the page via
 * `data.onSelect` (set by the page wrapping `nodeTypes`).
 */
export default function ClassNode({
  data,
  selected,
}: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "class") return null;
  const cls = data.entity.value;
  const card = data.card;

  const slots = card?.slots ?? [];
  const sources = card?.sources ?? [];
  const constraints = card?.constraints ?? [];

  const ring = selected ? "ring-2 ring-blue-500" : "";
  const hasMeta = sources.length > 0 || constraints.length > 0;

  const onSelect = (data as { onSelect?: SelectFn }).onSelect;

  return (
    <div
      className={`rounded-lg border border-slate-300 bg-white shadow-sm hover:shadow-md transition-shadow w-[280px] text-xs ${ring}`}
    >
      {/* Incoming edges (is_a / mixin / FK) anchor on the card's left side. */}
      <Handle
        type="target"
        position={Position.Left}
        className="!bg-slate-400"
      />

      {/* Header */}
      <div className="px-3 py-1.5 border-b border-slate-200 bg-slate-50 rounded-t-lg flex items-center justify-between gap-2">
        <span
          className="font-semibold text-slate-900 truncate"
          title={cls.name}
        >
          {cls.name}
        </span>
        <span
          className={`text-[9px] uppercase px-1.5 py-0.5 rounded font-mono tracking-wide ${
            cls.abstract
              ? "bg-slate-200 text-slate-700"
              : "bg-emerald-100 text-emerald-800"
          }`}
        >
          {cls.abstract ? "abstract" : "concrete"}
        </span>
      </div>

      {/* Slot rows */}
      {slots.length === 0 ? (
        <div className="px-3 py-2 italic text-slate-400">no slots</div>
      ) : (
        <div className="py-1">
          {slots.map((s) => (
            <SlotRow key={s.name} slot={s} onSelect={onSelect} />
          ))}
        </div>
      )}

      {/* Meta line (sources + constraints) */}
      {hasMeta && (
        <>
          <div className="border-t border-slate-200" />
          <div className="px-3 py-1.5 space-y-1">
            {sources.length > 0 && (
              <MetaRow label="sources">
                {sources.map((src) => (
                  <SourceChip key={src.name} src={src} onSelect={onSelect} />
                ))}
              </MetaRow>
            )}
            {constraints.length > 0 && (
              <MetaRow label="constraints">
                {constraints.map((k) => (
                  <ConstraintChip key={k.name} k={k} onSelect={onSelect} />
                ))}
              </MetaRow>
            )}
          </div>
        </>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Slot row
// ──────────────────────────────────────────────────────────────────────────

function SlotRow({
  slot,
  onSelect,
}: {
  slot: SpecSlot;
  onSelect?: SelectFn;
}) {
  const icon = slotIcon(slot);
  const isClassRange = slot.rangeKind === "class";

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.({ kind: "slot", name: slot.name });
      }}
      className="relative px-3 py-1 hover:bg-slate-50 cursor-pointer flex items-center gap-2"
    >
      <span className="text-slate-400 w-3 inline-block text-center font-mono">
        {icon}
      </span>
      <span
        className="font-mono text-slate-800 flex-1 truncate"
        title={slot.name}
      >
        {slot.name}
      </span>
      <SlotRange slot={slot} />
      <SlotChips slot={slot} />

      {/* Per-row source handle for cross-class FK edges. Always present so
          React Flow can match `sourceHandle: slot-${name}` regardless of
          whether the slot currently has a class range — the buildGraph
          filter decides when an edge actually leaves this handle. */}
      {isClassRange && (
        <Handle
          type="source"
          position={Position.Right}
          id={slotHandleId(slot.name)}
          className="!bg-blue-500 !w-1.5 !h-1.5"
        />
      )}
    </div>
  );
}

function SlotRange({ slot }: { slot: SpecSlot }) {
  if (!slot.rangeKind || !slot.rangeName) {
    return <span className="text-slate-400 italic">derived</span>;
  }
  if (slot.rangeKind === "type") {
    const isBuiltin = BUILTIN_TYPES.has(slot.rangeName);
    return (
      <span
        className={`font-mono truncate ${
          isBuiltin ? "text-slate-500" : "text-amber-700"
        }`}
        title={slot.rangeName}
      >
        {slot.rangeName}
      </span>
    );
  }
  // class range — clickable badge; the edge anchors to this row's handle.
  return (
    <span
      className="font-mono text-blue-700 bg-blue-50 px-1.5 rounded truncate"
      title={slot.rangeName}
    >
      {slot.rangeName}
    </span>
  );
}

function SlotChips({ slot }: { slot: SpecSlot }) {
  const chips: { label: string; tone: string }[] = [];
  if (slot.identifier)
    chips.push({ label: "ID", tone: "bg-emerald-600 text-white" });
  if (slot.required)
    chips.push({ label: "REQ", tone: "bg-amber-100 text-amber-800" });
  if (slot.multivalued)
    chips.push({ label: "MV", tone: "bg-violet-100 text-violet-800" });
  if (chips.length === 0) return null;
  return (
    <span className="flex gap-0.5">
      {chips.map((c) => (
        <span
          key={c.label}
          className={`text-[8px] font-semibold px-1 py-px rounded ${c.tone}`}
        >
          {c.label}
        </span>
      ))}
    </span>
  );
}

function slotIcon(slot: SpecSlot): string {
  if (slot.identifier) return "◆"; // ◆
  if (slot.rangeKind === "class") return "→"; // →
  if (!slot.rangeKind) return "λ"; // λ derived
  return "◇"; // ◇
}

// ──────────────────────────────────────────────────────────────────────────
// Meta rows: sources + constraints
// ──────────────────────────────────────────────────────────────────────────

function MetaRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-1.5">
      <span className="text-[9px] uppercase text-slate-500 tracking-wide w-[60px] pt-0.5">
        {label}
      </span>
      <span className="flex flex-wrap gap-1">{children}</span>
    </div>
  );
}

function SourceChip({
  src,
  onSelect,
}: {
  src: SpecSource;
  onSelect?: SelectFn;
}) {
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.({ kind: "source", name: src.name });
      }}
      className="text-[10px] font-mono px-1.5 py-px rounded bg-purple-100 text-purple-800 hover:bg-purple-200"
      title={`source: ${src.name}`}
    >
      {src.name}
    </button>
  );
}

function ConstraintChip({
  k,
  onSelect,
}: {
  k: SpecConstraint;
  onSelect?: SelectFn;
}) {
  const sev = String(k.severity).toLowerCase();
  const tone =
    sev === "error"
      ? "bg-red-100 text-red-800 hover:bg-red-200"
      : "bg-yellow-100 text-yellow-900 hover:bg-yellow-200";
  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.({ kind: "constraint", name: k.name });
      }}
      className={`text-[10px] font-mono px-1.5 py-px rounded ${tone}`}
      title={k.message ?? k.name}
    >
      {k.name}{" "}
      <span className="uppercase text-[8px] opacity-70">({sev})</span>
    </button>
  );
}

// ──────────────────────────────────────────────────────────────────────────
// Internal type — duplicated rather than imported from PropertyPanel to keep
// this leaf component decoupled.
// ──────────────────────────────────────────────────────────────────────────

type SelectFn = (sel: {
  kind: "class" | "slot" | "source" | "constraint" | "type";
  name: string;
}) => void;
