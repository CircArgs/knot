import type { SpecEntityKind } from "../types/spec";

export type AddKind = SpecEntityKind;

interface Props {
  mode: "view" | "edit";
  draftRevision: number | null;
  onToggleMode: () => void;
  onRefetch: () => void;
  onAdd?: (kind: AddKind) => void;
  onPublish?: () => void;
  onDiscard?: () => void;
  publishing?: boolean;
}

// "slot" is intentionally absent — slots are created inline inside ClassForm.
const KIND_LABELS: Partial<Record<AddKind, string>> = {
  class: "Class",
  source: "Source",
  sourceBinding: "Binding",
  constraint: "Constraint",
};

export default function Toolbar({
  mode,
  draftRevision,
  onToggleMode,
  onRefetch,
  onAdd,
  onPublish,
  onDiscard,
  publishing,
}: Props) {
  const editing = mode === "edit";
  return (
    <div className="flex items-center gap-2 flex-wrap p-2 border-b border-slate-200 bg-slate-50 text-sm">
      <button
        onClick={onToggleMode}
        className={`px-3 py-1 rounded font-medium border ${
          editing
            ? "bg-amber-100 border-amber-400 text-amber-900"
            : "bg-white border-slate-300 hover:bg-slate-100"
        }`}
        title="Cmd/Ctrl+E"
      >
        {editing ? `Editing draft #${draftRevision ?? "?"}` : "Edit"}
      </button>

      <button
        onClick={onRefetch}
        className="px-2 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100"
        title="Refetch"
      >
        ↻
      </button>

      {editing && onAdd && (
        <>
          <div className="border-l border-slate-300 h-6 mx-1" />
          {(Object.keys(KIND_LABELS) as AddKind[]).map((kind) => (
            <button
              key={kind}
              onClick={() => onAdd(kind)}
              className="px-2 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100 text-xs"
            >
              + {KIND_LABELS[kind]}
            </button>
          ))}
        </>
      )}

      <div className="flex-1" />

      {editing && (
        <>
          <button
            onClick={onPublish}
            disabled={publishing}
            className="px-3 py-1 rounded bg-emerald-600 text-white font-medium hover:bg-emerald-700 disabled:opacity-50"
          >
            {publishing ? "Publishing…" : "Publish"}
          </button>
          <button
            onClick={onDiscard}
            className="px-3 py-1 rounded border border-rose-300 text-rose-700 bg-white hover:bg-rose-50"
          >
            Discard draft
          </button>
        </>
      )}
    </div>
  );
}
