import { useEffect, useMemo, useState } from "react";

import { listClassRows } from "../../lib/dataApi";
import { inputClass } from "../forms/fields";

interface Props {
  className: string;
  value: string;
  onChange: (v: string) => void;
  /** Filter these canonical_ids out (e.g. the entity being merged into). */
  exclude?: string[];
  placeholder?: string;
}

/**
 * Debounced combobox for picking an existing canonical_id of a target class.
 *
 * Class tables can have thousands of rows, so we never load the full list —
 * we pull the first page on focus and re-fetch on each typed query
 * (debounced). Results are deduped by `_canonical_id`.
 *
 * The current value is shown as a chip when not focused so the operator can
 * see what was picked at a glance.
 */
export default function CanonicalIdPicker({
  className,
  value,
  onChange,
  exclude,
  placeholder,
}: Props) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [opts, setOpts] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);

  const excludeSet = useMemo(() => new Set(exclude ?? []), [exclude]);

  // Debounced fetch — re-runs whenever query changes.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setLoading(true);
    const handle = window.setTimeout(async () => {
      try {
        const resp = await listClassRows(className, { limit: 100 });
        if (cancelled) return;
        const seen = new Set<string>();
        const ids: string[] = [];
        const q = query.toLowerCase().trim();
        for (const row of resp.rows) {
          const cid = String(row._canonical_id);
          if (seen.has(cid) || excludeSet.has(cid)) continue;
          if (q && !cid.toLowerCase().includes(q)) continue;
          seen.add(cid);
          ids.push(cid);
          if (ids.length >= 50) break;
        }
        setOpts(ids);
      } catch {
        setOpts([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [open, query, className, excludeSet]);

  return (
    <div className="relative">
      <input
        type="text"
        value={open ? query : value}
        onChange={(e) => {
          setOpen(true);
          setQuery(e.target.value);
        }}
        onFocus={() => {
          setOpen(true);
          setQuery("");
        }}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        className={inputClass}
        placeholder={placeholder ?? `pick a ${className} canonical_id…`}
      />
      {open && (
        <div className="absolute z-50 left-0 right-0 mt-1 max-h-48 overflow-y-auto bg-white border border-slate-300 rounded shadow-lg">
          {loading ? (
            <div className="px-2 py-1 text-xs text-slate-500">searching…</div>
          ) : opts.length === 0 ? (
            <div className="px-2 py-1 text-xs text-slate-500 italic">
              no matches
            </div>
          ) : (
            opts.map((id) => (
              <button
                type="button"
                key={id}
                className="block w-full text-left px-2 py-1 text-xs font-mono hover:bg-slate-100 text-slate-700"
                onMouseDown={(e) => {
                  // onMouseDown beats onBlur so the picker doesn't close before
                  // we propagate the selection.
                  e.preventDefault();
                  onChange(id);
                  setQuery(id);
                  setOpen(false);
                }}
              >
                {id}
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
