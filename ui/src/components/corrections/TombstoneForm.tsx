import { useState } from "react";

import { Label, textareaClass } from "../forms/fields";

interface Props {
  className: string;
  canonicalId: string;
  onSubmit: (vals: { reason: string | null }) => Promise<void>;
}

/**
 * Tombstone form — confirm + optional reason. Tombstoned entities are
 * hidden from default views; passing `?include_tombstoned=true` brings
 * them back.
 */
export default function TombstoneForm({
  className,
  canonicalId,
  onSubmit,
}: Props) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = async () => {
    setErr(null);
    setBusy(true);
    try {
      await onSubmit({ reason: reason.trim() || null });
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-3 text-xs text-slate-500">
        Marks{" "}
        <span className="font-mono text-slate-700">
          {className}:{canonicalId}
        </span>{" "}
        as deleted. Hidden from default views; appears with the &ldquo;Show
        tombstoned&rdquo; toggle enabled.
      </div>

      <div className="mb-3">
        <Label>reason (optional)</Label>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className={textareaClass}
          placeholder="e.g. duplicate of inception_2010_v2"
        />
      </div>

      {err && <p className="text-xs text-rose-700 mb-2">{err}</p>}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="px-4 py-1.5 rounded bg-rose-600 text-white font-medium hover:bg-rose-700 disabled:opacity-50"
        >
          {busy ? "Tombstoning…" : "Tombstone"}
        </button>
      </div>
    </div>
  );
}
