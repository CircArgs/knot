import { useMemo, useState } from "react";

import type { ContributionRow } from "../../lib/dataApi";
import { Label } from "../forms/fields";

interface Props {
  className: string;
  sourceCanonicalId: string;
  contributions: ContributionRow[];
  onSubmit: (vals: {
    partitions: Record<string, [string, string][]>;
  }) => Promise<void>;
}

/**
 * Split form: lets the operator assign each (source, source_row_id)
 * contribution to one of N "buckets" (each bucket gets a new canonical_id).
 *
 * Constraints (also enforced server-side):
 *   - At least 2 buckets must have non-empty membership.
 *   - Every contribution must land in exactly one bucket.
 *   - Bucket names (new canonical_ids) must be non-empty and unique.
 *
 * UI shape: left column = bucket editor, right column = contribution rows
 * with a per-row bucket selector. Start with two empty buckets (k=2).
 */
export default function SplitForm({
  className,
  sourceCanonicalId,
  contributions,
  onSubmit,
}: Props) {
  const initialBuckets = useMemo(
    () => [
      { id: "b1", name: `${sourceCanonicalId}_a` },
      { id: "b2", name: `${sourceCanonicalId}_b` },
    ],
    [sourceCanonicalId],
  );
  const [buckets, setBuckets] = useState<{ id: string; name: string }[]>(
    initialBuckets,
  );
  /** Maps a contribution key `${_source}|${_source_row_id}` -> bucket id. */
  const [assignment, setAssignment] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const contributionKey = (r: ContributionRow): string =>
    `${r._source}|${r._source_row_id}`;

  const updateBucketName = (bucketId: string, name: string) => {
    setBuckets(buckets.map((b) => (b.id === bucketId ? { ...b, name } : b)));
  };

  const addBucket = () => {
    let n = buckets.length + 1;
    let nextId = `b${n}`;
    while (buckets.some((b) => b.id === nextId)) {
      n += 1;
      nextId = `b${n}`;
    }
    setBuckets([...buckets, { id: nextId, name: `${sourceCanonicalId}_${n}` }]);
  };

  const removeBucket = (bucketId: string) => {
    if (buckets.length <= 2) return;
    setBuckets(buckets.filter((b) => b.id !== bucketId));
    setAssignment(
      Object.fromEntries(
        Object.entries(assignment).filter(([, b]) => b !== bucketId),
      ),
    );
  };

  const setAssign = (contribKey: string, bucketId: string) => {
    setAssignment({ ...assignment, [contribKey]: bucketId });
  };

  // Validation (mirrors server logic in graph_corrections.apply_split).
  const validate = (): string | null => {
    const names = buckets.map((b) => b.name.trim());
    if (names.some((n) => !n)) return "Every bucket needs a non-empty new canonical_id.";
    if (new Set(names).size !== names.length) {
      return "Bucket canonical_ids must be unique.";
    }
    if (names.includes(sourceCanonicalId)) {
      return "A bucket can't reuse the source canonical_id.";
    }
    const unassigned = contributions.filter(
      (r) => !assignment[contributionKey(r)],
    );
    if (unassigned.length > 0) {
      return `Assign every contribution to a bucket. ${unassigned.length} unassigned.`;
    }
    const usedBuckets = new Set(Object.values(assignment));
    if (usedBuckets.size < 2) {
      return "At least two buckets must have contributions.";
    }
    return null;
  };

  const submit = async () => {
    const v = validate();
    if (v) {
      setErr(v);
      return;
    }
    setErr(null);
    setBusy(true);
    try {
      // Build the partitions payload: new_canonical_id -> [(src, sid), ...]
      const partitions: Record<string, [string, string][]> = {};
      const bucketName = new Map(buckets.map((b) => [b.id, b.name.trim()]));
      for (const r of contributions) {
        const bucketId = assignment[contributionKey(r)];
        if (!bucketId) continue;
        const name = bucketName.get(bucketId);
        if (!name) continue;
        if (!partitions[name]) partitions[name] = [];
        partitions[name].push([String(r._source), String(r._source_row_id)]);
      }
      await onSubmit({ partitions });
    } catch (e) {
      setErr(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="mb-3 text-xs text-slate-500">
        Splitting{" "}
        <span className="font-mono text-slate-700">
          {className}:{sourceCanonicalId}
        </span>{" "}
        into {buckets.length} new canonical_ids. Each per-source contribution
        below must land in exactly one bucket.
      </div>

      <div className="mb-3">
        <Label>buckets (new canonical_ids)</Label>
        <div className="space-y-1.5">
          {buckets.map((b) => (
            <div key={b.id} className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-slate-500 w-6">
                {b.id}
              </span>
              <input
                type="text"
                value={b.name}
                onChange={(e) => updateBucketName(b.id, e.target.value)}
                className="flex-1 px-2 py-1 border border-slate-300 rounded text-xs font-mono"
                placeholder="new canonical_id"
              />
              <span className="text-[10px] text-slate-400">
                {countAssigned(assignment, b.id)} assigned
              </span>
              <button
                type="button"
                onClick={() => removeBucket(b.id)}
                disabled={buckets.length <= 2}
                className="text-slate-400 hover:text-rose-600 text-sm disabled:opacity-30"
                aria-label="remove bucket"
              >
                ×
              </button>
            </div>
          ))}
          <button
            type="button"
            onClick={addBucket}
            className="px-2 py-1 text-xs rounded border border-slate-300 hover:bg-slate-50"
          >
            + bucket
          </button>
        </div>
      </div>

      <div className="mb-3">
        <Label>contributions ({contributions.length})</Label>
        <div className="border border-slate-200 rounded max-h-72 overflow-y-auto divide-y divide-slate-100">
          {contributions.length === 0 ? (
            <div className="px-2 py-2 text-xs text-slate-400 italic">
              no contributions to split
            </div>
          ) : (
            contributions.map((r) => {
              const k = contributionKey(r);
              return (
                <div
                  key={k}
                  className="flex items-center gap-2 px-2 py-1.5"
                >
                  <span className="text-[10px] font-mono text-purple-800 bg-purple-50 px-1.5 rounded">
                    {r._source}
                  </span>
                  <span
                    className="text-[10px] font-mono text-slate-500 flex-1 truncate"
                    title={String(r._source_row_id)}
                  >
                    {String(r._source_row_id)}
                  </span>
                  <select
                    value={assignment[k] ?? ""}
                    onChange={(e) => setAssign(k, e.target.value)}
                    className="text-xs px-1.5 py-0.5 border border-slate-300 rounded font-mono"
                  >
                    <option value="">— pick bucket —</option>
                    {buckets.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.id}: {b.name.trim() || "(unnamed)"}
                      </option>
                    ))}
                  </select>
                </div>
              );
            })
          )}
        </div>
      </div>

      {err && <p className="text-xs text-rose-700 mb-2">{err}</p>}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={submit}
          disabled={busy || contributions.length === 0}
          className="px-4 py-1.5 rounded bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50"
        >
          {busy ? "Splitting…" : "Split"}
        </button>
      </div>
    </div>
  );
}

function countAssigned(
  assignment: Record<string, string>,
  bucketId: string,
): number {
  let n = 0;
  for (const v of Object.values(assignment)) if (v === bucketId) n += 1;
  return n;
}
