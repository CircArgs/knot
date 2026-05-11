import { Handle, Position } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";
import { useMemo, useState } from "react";

import type {
  EntityAction,
  EntityNodeData,
} from "../../lib/buildDataGraph";
import { slotHandleId } from "../../lib/buildDataGraph";
import type { ContributionRow } from "../../lib/dataApi";
import type { SpecSlot } from "../../types/spec";
import ActionMenu from "../corrections/ActionMenu";

/**
 * Card-shaped node for one canonical entity.
 *
 *   Header   — class:canonical_id + tombstone badge + action menu
 *   Resolved — one row per stored slot showing the trust-resolved value
 *   Contribs — expandable section: per-source contribution rows with
 *              ✓ / ✗ / – markers vs the resolved value
 */
export default function EntityCard({
  data,
  selected,
}: NodeProps & { data: EntityNodeData }) {
  const [expanded, setExpanded] = useState(false);
  const {
    className,
    canonicalId,
    slots,
    contributions,
    resolved,
    resolvedLoading,
    tombstoned,
    onAction,
  } = data;

  const ring = selected ? "ring-2 ring-blue-500" : "";
  const opacity = tombstoned ? "opacity-60" : "";
  // Slots that should appear on the card: all stored slots of the class.
  // Derived slots have rangeKind=null and never get a value back from
  // either the resolved view or contributions, so we skip them.
  const storedSlots = useMemo(
    () => slots.filter((s) => s.rangeKind !== null),
    [slots],
  );

  return (
    <div
      className={`rounded-lg border border-slate-300 bg-white shadow-sm hover:shadow-md transition-shadow w-[320px] text-xs ${ring} ${opacity}`}
      data-entity-class={className}
      data-canonical-id={canonicalId}
    >
      {/* Target handle for incoming FK edges */}
      <Handle
        type="target"
        position={Position.Left}
        className="!bg-slate-400"
      />

      {/* Header */}
      <div className="px-3 py-1.5 border-b border-slate-200 bg-slate-50 rounded-t-lg flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="text-[9px] uppercase font-mono text-slate-500">
            {className}
          </span>
          <span
            className={`font-mono text-slate-900 truncate ${tombstoned ? "line-through" : "font-semibold"}`}
            title={canonicalId}
          >
            {canonicalId}
          </span>
          {tombstoned && (
            <span className="text-[9px] uppercase px-1 py-px rounded font-mono bg-rose-100 text-rose-800">
              tomb
            </span>
          )}
        </div>
        {onAction && (
          <ActionMenu
            onPick={(kind) =>
              onAction({ kind, canonicalId, className } as EntityAction)
            }
          />
        )}
      </div>

      {/* Resolved view */}
      <div className="py-1">
        {storedSlots.length === 0 ? (
          <div className="px-3 py-2 italic text-slate-400">no stored slots</div>
        ) : (
          storedSlots.map((slot) => (
            <ResolvedRow
              key={slot.name}
              slot={slot}
              value={resolved?.[slot.name]}
              loading={resolvedLoading && !resolved}
            />
          ))
        )}
      </div>

      {/* Contributions */}
      <div className="border-t border-slate-200">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((b) => !b);
          }}
          className="w-full text-left px-3 py-1.5 text-[11px] uppercase tracking-wide text-slate-600 hover:bg-slate-50 flex items-center gap-1.5"
        >
          <span>{expanded ? "▾" : "▸"}</span>
          <span>contributions ({contributions.length})</span>
        </button>
        {expanded && contributions.length > 0 && (
          <ContributionsTable
            contributions={contributions}
            slots={storedSlots}
            resolved={resolved}
          />
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Resolved row — one per stored slot of the class
// ────────────────────────────────────────────────────────────────────────────

function ResolvedRow({
  slot,
  value,
  loading,
}: {
  slot: SpecSlot;
  value: unknown;
  loading: boolean;
}) {
  const isClassRange = slot.rangeKind === "class";
  const icon = slot.identifier ? "◆" : isClassRange ? "→" : "◇";
  // The card prominently shows ID slots; render others slightly muted.
  const nameTone = slot.identifier ? "text-slate-900 font-semibold" : "text-slate-800";

  return (
    <div className="relative px-3 py-1 flex items-center gap-2">
      <span className="text-slate-400 w-3 inline-block text-center font-mono">
        {icon}
      </span>
      <span
        className={`font-mono ${nameTone} flex-1 truncate`}
        title={slot.name}
      >
        {slot.name}
      </span>
      <ResolvedValue
        value={value}
        loading={loading}
        slot={slot}
      />
      {/* Source handle for cross-class FK edges */}
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

function ResolvedValue({
  value,
  loading,
  slot,
}: {
  value: unknown;
  loading: boolean;
  slot: SpecSlot;
}) {
  if (loading) {
    return (
      <span className="font-mono text-slate-400 italic">loading…</span>
    );
  }
  if (value === undefined || value === null) {
    return <span className="font-mono text-slate-400 italic">—</span>;
  }
  if (slot.rangeKind === "class") {
    const arr = slot.multivalued
      ? (Array.isArray(value) ? value : []).map((v) => String(v))
      : [String(value)];
    return (
      <span className="flex flex-wrap gap-0.5 justify-end max-w-[170px]">
        {arr.map((v) => (
          <span
            key={v}
            className="font-mono text-blue-700 bg-blue-50 px-1.5 rounded truncate"
            title={v}
          >
            {v}
          </span>
        ))}
      </span>
    );
  }
  if (slot.multivalued && Array.isArray(value)) {
    return (
      <span
        className="font-mono text-slate-700 truncate max-w-[170px]"
        title={value.map(String).join(", ")}
      >
        {value.map(String).join(", ")}
      </span>
    );
  }
  return (
    <span
      className="font-mono text-slate-700 truncate max-w-[170px]"
      title={String(value)}
    >
      {String(value)}
    </span>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Contributions table — per-source rows with disagreement markers
// ────────────────────────────────────────────────────────────────────────────

function ContributionsTable({
  contributions,
  slots,
  resolved,
}: {
  contributions: ContributionRow[];
  slots: SpecSlot[];
  resolved: Record<string, unknown> | null;
}) {
  return (
    <div className="px-3 pb-2 pt-0.5 space-y-1.5">
      {contributions.map((row, i) => (
        <ContributionRowView
          key={`${row._source}:${row._source_row_id}:${i}`}
          row={row}
          slots={slots}
          resolved={resolved}
        />
      ))}
    </div>
  );
}

function ContributionRowView({
  row,
  slots,
  resolved,
}: {
  row: ContributionRow;
  slots: SpecSlot[];
  resolved: Record<string, unknown> | null;
}) {
  return (
    <div className="border border-slate-100 rounded p-1.5 bg-slate-50/60">
      <div className="flex items-center justify-between mb-1 gap-2">
        <span className="font-mono text-[10px] font-semibold text-purple-800 bg-purple-50 px-1.5 rounded">
          {row._source}
        </span>
        <span
          className="font-mono text-[9px] text-slate-400 truncate"
          title={row._source_row_id}
        >
          row={row._source_row_id}
        </span>
      </div>
      <div className="grid grid-cols-[14px_90px_1fr] gap-x-1 gap-y-0.5">
        {slots.map((slot) => {
          const contrib = row[slot.name];
          const resv = resolved?.[slot.name];
          const marker = disagreementMarker(contrib, resv, resolved);
          return (
            <ContribSlotRow
              key={slot.name}
              slotName={slot.name}
              value={contrib}
              marker={marker}
            />
          );
        })}
      </div>
    </div>
  );
}

function ContribSlotRow({
  slotName,
  value,
  marker,
}: {
  slotName: string;
  value: unknown;
  marker: "match" | "disagree" | "null" | "no-resolved";
}) {
  const display =
    value === undefined || value === null
      ? "—"
      : Array.isArray(value)
        ? value.map(String).join(", ")
        : String(value);
  const isNull = value === undefined || value === null;
  const tone =
    marker === "match"
      ? "text-emerald-700"
      : marker === "disagree"
        ? "text-rose-700"
        : isNull
          ? "text-slate-400"
          : "text-slate-700";
  const sigil =
    marker === "match"
      ? "✓"
      : marker === "disagree"
        ? "✗"
        : marker === "null"
          ? "–"
          : " ";
  return (
    <>
      <span className={`font-mono ${tone}`}>{sigil}</span>
      <span className="font-mono text-[10px] text-slate-500 truncate" title={slotName}>
        {slotName}
      </span>
      <span
        className={`font-mono text-[10px] ${tone} truncate`}
        title={display}
      >
        {display}
      </span>
    </>
  );
}

function disagreementMarker(
  contrib: unknown,
  resolved: unknown,
  resolvedRecord: Record<string, unknown> | null,
): "match" | "disagree" | "null" | "no-resolved" {
  if (resolvedRecord === null) return "no-resolved";
  if (contrib === undefined || contrib === null) return "null";
  if (resolved === undefined || resolved === null) return "disagree";
  if (Array.isArray(contrib) && Array.isArray(resolved)) {
    const a = [...contrib.map(String)].sort();
    const b = [...resolved.map(String)].sort();
    if (a.length !== b.length) return "disagree";
    for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return "disagree";
    return "match";
  }
  return String(contrib) === String(resolved) ? "match" : "disagree";
}
