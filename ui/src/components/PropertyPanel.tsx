import type { SpecEntity } from "../types/spec";

interface Props {
  entity: SpecEntity | null;
  onClose?: () => void;
}

export default function PropertyPanel({ entity, onClose }: Props) {
  if (!entity) {
    return (
      <aside className="w-80 border-l border-slate-200 bg-white p-4 text-sm text-slate-500">
        <div className="italic">Select a node to inspect its properties.</div>
      </aside>
    );
  }
  return (
    <aside className="w-80 border-l border-slate-200 bg-white overflow-y-auto">
      <header className="p-4 border-b border-slate-200 flex items-center justify-between sticky top-0 bg-white">
        <div>
          <div className="text-[10px] uppercase tracking-wide text-slate-500">
            {entity.kind}
          </div>
          <div className="font-semibold text-slate-900 font-mono break-all">
            {entity.value.name}
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

function PropertyTable({ entity }: { entity: SpecEntity }) {
  const rows: [string, unknown][] = Object.entries(entity.value);
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
    return v.join(", ");
  }
  if (typeof v === "object") return JSON.stringify(v, null, 2);
  return String(v);
}
