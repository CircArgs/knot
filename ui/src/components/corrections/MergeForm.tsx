import { useState } from "react";

import { Label } from "../forms/fields";
import CanonicalIdPicker from "./CanonicalIdPicker";

interface Props {
  className: string;
  /** The canonical_id that will REMAIN after the merge. */
  keepCanonicalId: string;
  onSubmit: (vals: { mergeCanonicalIds: string[] }) => Promise<void>;
}

/**
 * Merge form — collects ≥ 1 other canonical_id to fold INTO keepCanonicalId.
 *
 * Uses the debounce-searching CanonicalIdPicker, excluding the keep_id from
 * its options, plus a chip list of accumulated picks the operator can drop.
 */
export default function MergeForm({
  className,
  keepCanonicalId,
  onSubmit,
}: Props) {
  const [picks, setPicks] = useState<string[]>([]);
  const [pickerValue, setPickerValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const addPick = (id: string) => {
    if (!id || id === keepCanonicalId || picks.includes(id)) return;
    setPicks([...picks, id]);
    setPickerValue("");
  };
  const removePick = (id: string) => {
    setPicks(picks.filter((p) => p !== id));
  };

  const submit = async () => {
    if (picks.length === 0) {
      setErr("Pick at least one entity to merge in.");
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      await onSubmit({ mergeCanonicalIds: picks });
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-3 text-xs text-slate-500">
        These entities will be merged{" "}
        <span className="font-semibold text-slate-700">into</span>{" "}
        <span className="font-mono text-slate-800">{keepCanonicalId}</span>.
        Cross-class FK references will be rewritten automatically.
      </div>

      <div className="mb-3">
        <Label>add canonical_id to merge in</Label>
        <CanonicalIdPicker
          className={className}
          value={pickerValue}
          onChange={(v) => {
            setPickerValue(v);
            addPick(v);
          }}
          exclude={[keepCanonicalId, ...picks]}
        />
      </div>

      <div className="mb-4">
        <Label>queued for merge ({picks.length})</Label>
        {picks.length === 0 ? (
          <p className="text-xs text-slate-400 italic">none yet</p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {picks.map((id) => (
              <span
                key={id}
                className="font-mono text-xs px-1.5 py-0.5 rounded bg-blue-50 text-blue-800 flex items-center gap-1"
              >
                {id}
                <button
                  type="button"
                  onClick={() => removePick(id)}
                  className="text-blue-500 hover:text-blue-800 ml-0.5"
                  aria-label={`remove ${id}`}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
      </div>

      {err && <p className="text-xs text-rose-700 mb-2">{err}</p>}

      <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded p-2 mb-3">
        This will collapse {picks.length} entit{picks.length === 1 ? "y" : "ies"}{" "}
        into <span className="font-mono">{keepCanonicalId}</span>. The merged
        IDs disappear; FK refs to them across other classes are rewritten.
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={submit}
          disabled={busy || picks.length === 0}
          className="px-4 py-1.5 rounded bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? "Merging…" : "Merge"}
        </button>
      </div>
    </div>
  );
}
