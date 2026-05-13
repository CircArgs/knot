import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import type { SpecNodeData } from "../../lib/buildGraph";
import { slotHandleId } from "../../lib/buildGraph";
import { BUILTIN_TYPES, isArrayKind, isClassKind, isPrimitiveKind } from "../../types/spec";
import type {
  SpecConstraint,
  SpecSlot,
} from "../../types/spec";
import KindBadge from "./KindBadge";

/**
 * Class card — the only node kind in the spec graph. Renders header +
 * per-slot rows + sources / constraints chip lines. Click handlers on
 * slot rows / source chips / constraint chips dispatch a "select non-node
 * entity" event via `data.onSelect` (set by the page wrapping `nodeTypes`).
 *
 * Reified-relation classes (`card.isJunction === true` — classes with ≥2
 * ClassRef slots, like a `Credit` between `Movie` and `Person`) get a
 * cut-corner octagon clip + violet accent so they're scannable in dense
 * graphs.
 */
export default function ClassNode({
  data,
  selected,
}: NodeProps & { data: SpecNodeData }) {
  if (data.entity.kind !== "class") return null;
  const cls = data.entity.value;
  const card = data.card;

  const slots = card?.slots ?? [];
  const constraints = card?.constraints ?? [];
  const isJunction = card?.isJunction === true;

  // Inherited slots — anything in effectiveSlots that isn't own. Rendered
  // in a separate, muted section so the user can see the full effective
  // contract of the class without confusing inherited slots with owned ones.
  const ownNames = new Set(slots.map((s) => s.name));
  const inheritedSlots = (cls.effectiveSlots ?? []).filter(
    (s) => !ownNames.has(s.name),
  );

  const ring = selected
    ? "ring-2 ring-blue-500"
    : isJunction
      ? "ring-1 ring-violet-300"
      : "";
  const border = isJunction ? "border-violet-400" : "border-slate-300";
  const hasMeta = constraints.length > 0;

  const onSelect = (data as { onSelect?: SelectFn }).onSelect;

  // Chamfered octagon clip for junctions — cuts each corner at 12px so the
  // shape reads as "associative entity" without breaking axis-aligned content.
  const junctionStyle: React.CSSProperties | undefined = isJunction
    ? {
        clipPath:
          "polygon(12px 0%, calc(100% - 12px) 0%, 100% 12px, 100% calc(100% - 12px), calc(100% - 12px) 100%, 12px 100%, 0% calc(100% - 12px), 0% 12px)",
      }
    : undefined;

  return (
    <div
      style={junctionStyle}
      className={`${isJunction ? "" : "rounded-lg"} border bg-white shadow-sm hover:shadow-md transition-shadow w-[280px] text-xs ${border} ${ring}`}
    >
      {/* Incoming edges (is_a / mixin / FK) anchor on the card's left side. */}
      <Handle
        type="target"
        position={Position.Left}
        className="!bg-slate-400"
      />
      {/* Class-level outgoing edges (is_a, mixin) anchor on the bottom — keeps
          them visually distinct from the per-slot-row FK source handles on the
          right side. */}
      <Handle
        type="source"
        position={Position.Bottom}
        id="class-source"
        className="!bg-slate-400"
      />

      {/* Header — clicking the header selects the class as a whole (not a slot).
          Intentionally NOT stopping propagation so React Flow's onNodeClick
          also fires, which sets the focus state used for graph dimming. The
          inner onSelect call is then redundant but harmless. */}
      <div
        role="button"
        tabIndex={0}
        onClick={() => {
          onSelect?.({ kind: "class", name: cls.name });
        }}
        className={`px-3 py-1.5 border-b border-slate-200 bg-slate-50 ${isJunction ? "" : "rounded-t-lg"} flex items-center justify-between gap-2 cursor-pointer hover:bg-slate-100`}
      >
        <div className="flex items-center gap-1.5 min-w-0">
          <KindBadge kind="class" />
          <span
            className="font-semibold text-slate-900 truncate"
            title={cls.name}
          >
            {cls.name}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {isJunction && (
            <span
              className="text-[9px] uppercase px-1.5 py-0.5 rounded font-mono tracking-wide bg-violet-100 text-violet-800"
              title="Reified relation — ≥2 class-reference slots"
            >
              junction
            </span>
          )}
          {cls.definition !== null ? (
            <span
              className="text-[9px] uppercase px-1.5 py-0.5 rounded font-mono tracking-wide bg-sky-100 text-sky-800"
              title={`Defined class — SQL predicate: ${cls.definition}`}
            >
              defined
            </span>
          ) : (
            <span
              className={`text-[9px] uppercase px-1.5 py-0.5 rounded font-mono tracking-wide ${
                cls.abstract
                  ? "bg-slate-200 text-slate-700"
                  : "bg-emerald-100 text-emerald-800"
              }`}
            >
              {cls.abstract ? "abstract" : "concrete"}
            </span>
          )}
        </div>
      </div>

      {/* Slot rows — own slots first */}
      {slots.length === 0 && inheritedSlots.length === 0 ? (
        <div className="px-3 py-2 italic text-slate-400">no slots</div>
      ) : (
        <div className="py-1">
          {slots.map((s) => (
            <SlotRow
              key={s.name}
              slot={s}
              className={cls.name}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}

      {/* Inherited slots — from mixins or is_a parent. Visually muted so
          they're distinguishable from owned slots without disappearing. */}
      {inheritedSlots.length > 0 && (
        <>
          <div className="border-t border-slate-200" />
          <div className="px-3 pt-1.5 pb-0.5 flex items-center gap-1.5">
            <span className="text-[9px] uppercase tracking-wide text-slate-400">
              inherited
            </span>
            <span className="text-[9px] text-slate-400 italic">
              from is_a / mixins
            </span>
          </div>
          <div className="py-1 opacity-60">
            {inheritedSlots.map((s) => (
              <SlotRow
                key={s.name}
                slot={s}
                className={cls.name}
                onSelect={onSelect}
                inherited
              />
            ))}
          </div>
        </>
      )}

      {/* Meta line (sources + constraints) */}
      {hasMeta && (
        <>
          <div className="border-t border-slate-200" />
          <div className="px-3 py-1.5 space-y-1">
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
  className,
  onSelect,
  inherited = false,
}: {
  slot: SpecSlot;
  className: string;
  onSelect?: SelectFn;
  inherited?: boolean;
}) {
  const icon = slotIcon(slot);
  const isClassRange = isClassKind(slot.typeKind);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.({ kind: "slot", name: slot.name, className });
      }}
      className={`relative px-3 py-1 hover:bg-slate-50 cursor-pointer flex items-center gap-2 ${
        inherited ? "italic" : ""
      }`}
      title={inherited ? "inherited from is_a / mixin" : undefined}
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
  if (!slot.typeKind || !slot.typeName) {
    return <span className="text-slate-400 italic">derived</span>;
  }
  if (isPrimitiveKind(slot.typeKind)) {
    const isBuiltin = BUILTIN_TYPES.has(slot.typeName);
    return (
      <span
        className={`font-mono truncate ${
          isBuiltin ? "text-slate-500" : "text-amber-700"
        }`}
        title={slot.typeName}
      >
        {slot.typeName}
      </span>
    );
  }
  // class range — clickable badge; the edge anchors to this row's handle.
  return (
    <span
      className="font-mono text-blue-700 bg-blue-50 px-1.5 rounded truncate"
      title={slot.typeName}
    >
      {slot.typeName}
    </span>
  );
}

function SlotChips({ slot }: { slot: SpecSlot }) {
  const chips: { label: string; tone: string }[] = [];
  if (slot.identifier)
    chips.push({ label: "ID", tone: "bg-emerald-600 text-white" });
  if (slot.required)
    chips.push({ label: "REQ", tone: "bg-amber-100 text-amber-800" });
  if (isArrayKind(slot.typeKind))
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
  if (slot.identifier) return "◆";
  if (isClassKind(slot.typeKind)) return "→";
  if (!slot.typeKind) return "λ"; // derived
  return "◇";
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
// Internal type
// ──────────────────────────────────────────────────────────────────────────

type SelectFn = (sel: {
  kind: "class" | "slot" | "source" | "constraint";
  name: string;
  className?: string;
}) => void;
