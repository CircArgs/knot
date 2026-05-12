import type { PublishedSpec, SpecEntity, SpecEntityKind } from "../types/spec";

/**
 * What the panel can be focused on. In the class-card view, slot rows /
 * source chips / constraint chips aren't standalone React Flow nodes, so
 * we carry an entity-kind + name and resolve against the spec.
 */
export interface SpecSelection {
  kind: SpecEntityKind;
  name: string;
}

interface Props {
  selection: SpecSelection | null;
  spec: PublishedSpec | null;
  onClose?: () => void;
}

export default function PropertyPanel({ selection, spec, onClose }: Props) {
  const entity = selection && spec ? resolveSelection(spec, selection) : null;

  if (!selection) {
    return (
      <aside className="w-80 border-l border-slate-200 bg-white p-4 text-sm text-slate-500">
        <div className="italic">
          Select a class, slot, source, or constraint to inspect its properties.
        </div>
      </aside>
    );
  }
  if (!entity) {
    return (
      <aside className="w-80 border-l border-slate-200 bg-white p-4 text-sm text-slate-500">
        <div className="italic">
          {selection.kind} "{selection.name}" not found in spec.
        </div>
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
