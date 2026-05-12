import { useEffect, useRef, useState } from "react";

import type { PublishedSpec, SpecEntity, SpecEntityKind } from "../types/spec";
import { useLocalStorage } from "../lib/useLocalStorage";

const MIN_WIDTH = 280;
const MAX_WIDTH = 900;
const DEFAULT_WIDTH = 480;

/**
 * What the panel can be focused on. In the class-card view, slot rows /
 * source chips / constraint chips aren't standalone React Flow nodes, so
 * we carry an entity-kind + name and resolve against the spec.
 */
export interface SpecSelection {
  kind: SpecEntityKind;
  name: string;
  /** For slot selections, the owning class — slot names are no longer
   *  globally unique under by-copy. Ignored for other kinds. */
  className?: string;
}

interface Props {
  selection: SpecSelection | null;
  spec: PublishedSpec | null;
  onClose?: () => void;
}

export default function PropertyPanel({ selection, spec, onClose }: Props) {
  const entity = selection && spec ? resolveSelection(spec, selection) : null;
  const [width, setWidth] = useLocalStorage<number>(
    "knot:property-panel:width",
    DEFAULT_WIDTH,
  );
  const startRef = useRef<{ x: number; w: number } | null>(null);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!startRef.current) return;
      // The panel sits on the RIGHT side; dragging the left-edge handle to
      // the LEFT (negative deltaX) should widen it.
      const delta = startRef.current.x - e.clientX;
      const next = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startRef.current.w + delta));
      setWidth(next);
    };
    const onUp = () => {
      setDragging(false);
      startRef.current = null;
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging, setWidth]);

  const onDragStart = (e: React.MouseEvent) => {
    startRef.current = { x: e.clientX, w: width };
    setDragging(true);
    e.preventDefault();
  };

  // Resize handle: a 4px-wide draggable strip on the left edge.
  const dragHandle = (
    <div
      onMouseDown={onDragStart}
      className={`absolute left-0 top-0 bottom-0 w-1 cursor-col-resize hover:bg-blue-300 transition-colors ${
        dragging ? "bg-blue-400" : "bg-transparent"
      }`}
      title="Drag to resize"
    />
  );

  const asideStyle = { width: `${width}px`, flexShrink: 0 } as const;

  if (!selection) {
    return (
      <aside
        style={asideStyle}
        className="relative border-l border-slate-200 bg-white p-4 text-sm text-slate-500"
      >
        {dragHandle}
        <div className="italic">
          Select a class, property, source, or constraint to inspect its properties.
        </div>
      </aside>
    );
  }
  if (!entity) {
    return (
      <aside
        style={asideStyle}
        className="relative border-l border-slate-200 bg-white p-4 text-sm text-slate-500"
      >
        {dragHandle}
        <div className="italic">
          {selection.kind} "{selection.name}" not found in spec.
        </div>
      </aside>
    );
  }

  return (
    <aside
      style={asideStyle}
      className="relative border-l border-slate-200 bg-white overflow-y-auto"
    >
      {dragHandle}
      <header className="p-4 border-b border-slate-200 flex items-center justify-between sticky top-0 bg-white">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-slate-500">
            {entity.kind}
          </div>
          <div className="font-semibold text-slate-900 font-mono break-all">
            {entityDisplayName(entity)}
          </div>
        </div>
        {onClose && (
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-lg leading-none"
            aria-label="close"
          >
            ×
          </button>
        )}
      </header>
      <div className="p-4 text-sm">
        <PropertyTable entity={entity} />
      </div>
    </aside>
  );
}

export function resolveSelection(
  spec: PublishedSpec,
  sel: SpecSelection,
): SpecEntity | null {
  switch (sel.kind) {
    case "class": {
      const v = spec.classes.find((c) => c.name === sel.name);
      return v ? { kind: "class", value: v } : null;
    }
    case "property": {
      // Slots are owned by their class (by-copy model). Resolve via the
      // owning class's effectiveProperties so mixin/is_a-inherited slots also
      // surface in the panel.
      if (!sel.className) return null;
      const cls = spec.classes.find((c) => c.name === sel.className);
      if (!cls) return null;
      const pool = cls.effectiveProperties?.length ? cls.effectiveProperties : cls.properties;
      const v = pool.find((s) => s.name === sel.name);
      return v ? { kind: "property", value: v } : null;
    }
    case "source": {
      const v = spec.sources.find((src) => src.name === sel.name);
      return v ? { kind: "source", value: v } : null;
    }
    case "sourceBinding": {
      // sel.name for a binding is "{sourceName}__{className}"
      const v = (spec.sourceBindings ?? []).find(
        (b) => `${b.sourceName}__${b.className}` === sel.name,
      );
      return v ? { kind: "sourceBinding", value: v } : null;
    }
    case "constraint": {
      const v = spec.constraints.find((k) => k.name === sel.name);
      return v ? { kind: "constraint", value: v } : null;
    }
    default:
      return null;
  }
}

function entityDisplayName(entity: SpecEntity): string {
  if (entity.kind === "sourceBinding") {
    return `${entity.value.sourceName}__${entity.value.className}`;
  }
  return (entity.value as { name: string }).name;
}

function PropertyTable({ entity }: { entity: SpecEntity }) {
  const rows: [string, unknown][] = Object.entries(entity.value).filter(
    ([k]) => !k.startsWith("__"),  // hide GraphQL internals like __typename
  );
  return (
    <dl className="grid grid-cols-[100px_1fr] gap-x-3 gap-y-1.5">
      {rows.map(([k, v]) => (
        <Row key={k} label={k} value={v} />
      ))}
    </dl>
  );
}

function Row({ label, value }: { label: string; value: unknown }) {
  return (
    <>
      <dt className="text-[11px] uppercase tracking-wide text-slate-500 pt-1">
        {label}
      </dt>
      <dd className="text-slate-800 break-words font-mono text-xs whitespace-pre-wrap">
        {renderValue(value)}
      </dd>
    </>
  );
}

function renderValue(v: unknown): React.ReactNode {
  if (v === null || v === undefined) return <span className="italic text-slate-400">—</span>;
  if (typeof v === "boolean") return v ? "true" : "false";
  if (Array.isArray(v)) {
    if (v.length === 0) return <span className="italic text-slate-400">[]</span>;
    // Array of objects (e.g. SourceBinding.mappings) — render each item on
    // its own line as pretty JSON, dropping GraphQL internals.
    const hasObjects = v.some((x) => x !== null && typeof x === "object" && !Array.isArray(x));
    if (hasObjects) {
      return (
        <div className="space-y-1">
          {v.map((item, i) => (
            <pre
              key={i}
              className="bg-slate-50 border border-slate-200 rounded px-1.5 py-1 text-[10px] overflow-x-auto"
            >
              {JSON.stringify(stripInternals(item), null, 2)}
            </pre>
          ))}
        </div>
      );
    }
    return v.join(", ");
  }
  if (typeof v === "object") return JSON.stringify(stripInternals(v), null, 2);
  return String(v);
}

function stripInternals(v: unknown): unknown {
  if (v === null || typeof v !== "object") return v;
  if (Array.isArray(v)) return v.map(stripInternals);
  return Object.fromEntries(
    Object.entries(v as Record<string, unknown>)
      .filter(([k]) => !k.startsWith("__"))
      .map(([k, val]) => [k, stripInternals(val)]),
  );
}
