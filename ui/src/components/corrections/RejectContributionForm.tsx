import { useState } from "react";

import type { ContributionRow } from "../../lib/dataApi";
import { Label, selectClass } from "../forms/fields";

interface Props {
  className: string;
  canonicalId: string;
  contributions: ContributionRow[];
  onSubmit: (vals: { source: string }) => Promise<void>;
}

/**
 * Reject Contribution — drop one source's view of an entity. Source list
 * comes from the entity's current contributions (deduped); we don't allow
 * picking a source that doesn't currently contribute.
 */
export default function RejectContributionForm({
  className,
  canonicalId,
  contributions,
  onSubmit,
}: Props) {
  const sources = Array.from(
    new Set(contributions.map((r) => String(r._source))),
  );
  const [source, setSource] = useState<string>(sources[0] ?? "");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    if (!source) {
      setErr("Pick a source.");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      await onSubmit({ source });
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-3 text-xs text-slate-500">
        Drops one source&rsquo;s contribution to{" "}
        <span className="font-mono text-slate-700">
          {className}:{canonicalId}
        </span>{" "}
        from the resolved view. The source row is preserved for audit.
      </div>

      <div className="mb-3">
        <Label required>source</Label>
        <select
          value={source}
          onChange={(e) => setSource(e.target.value)}
          className={selectClass}
        >
          {sources.length === 0 ? (
            <option value="">(no current contributions)</option>
          ) : (
            sources.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))
          )}
        </select>
      </div>

      {err && <p className="text-xs text-rose-700 mb-2">{err}</p>}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={submit}
          disabled={busy || sources.length === 0}
          className="px-4 py-1.5 rounded bg-rose-600 text-white font-medium hover:bg-rose-700 disabled:opacity-50"
        >
          {busy ? "Rejecting…" : "Reject"}
        </button>
      </div>
    </div>
  );
}
