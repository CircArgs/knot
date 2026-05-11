import { useEffect, useRef, useState } from "react";

type ActionKind =
  | "property"
  | "merge"
  | "split"
  | "add"
  | "tombstone"
  | "reject_contribution";

interface Props {
  onPick: (kind: ActionKind) => void;
}

const ITEMS: { kind: ActionKind; label: string }[] = [
  { kind: "property", label: "Edit property…" },
  { kind: "merge", label: "Merge into…" },
  { kind: "split", label: "Split…" },
  { kind: "add", label: "Add new…" },
  { kind: "tombstone", label: "Tombstone…" },
  { kind: "reject_contribution", label: "Reject contribution…" },
];

/**
 * Dropdown ⋯ button. Closes on outside click + escape.
 * Stops propagation on every click so React Flow doesn't grab the event
 * and treat the card itself as the click target.
 */
export default function ActionMenu({ onPick }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          setOpen((b) => !b);
        }}
        className="px-1.5 py-0.5 text-slate-500 hover:text-slate-800 hover:bg-slate-200 rounded font-mono text-sm leading-none"
        title="Actions"
        aria-label="Actions"
      >
        ⋯
      </button>
      {open && (
        <div
          className="absolute right-0 top-full mt-1 z-40 bg-white border border-slate-300 rounded shadow-lg w-48 py-1 text-xs"
          onClick={(e) => e.stopPropagation()}
        >
          {ITEMS.map(({ kind, label }) => (
            <button
              key={kind}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                setOpen(false);
                onPick(kind);
              }}
              className="block w-full text-left px-3 py-1.5 hover:bg-slate-100 text-slate-700"
            >
              {label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
