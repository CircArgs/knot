/**
 * /corrections — manual data-plane correction console.
 *
 * Lists rows for a selected class, lets the user pick a canonical_id, and
 * exposes the existing correction-form components (PropertyCorrection,
 * Merge, Split, Add, Tombstone, RejectContribution) wired into the
 * `/graph/corrections` API.
 *
 * Scope: an operator console, not a polished consumer UI. The forms
 * themselves are the prettier parts; this page is the glue that lets you
 * reach them. Use the /query page to find rows / their canonical_ids, then
 * come here to act on them.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@apollo/client";

import AddForm from "../components/corrections/AddForm";
import MergeForm from "../components/corrections/MergeForm";
import PropertyCorrectionForm from "../components/corrections/PropertyCorrectionForm";
import RejectContributionForm from "../components/corrections/RejectContributionForm";
import SplitForm from "../components/corrections/SplitForm";
import TombstoneForm from "../components/corrections/TombstoneForm";
import { PUBLISHED_SPEC } from "../graphql/queries";
import {
  type ContributionRow,
  type CorrectionBody,
  groupByCanonicalId,
  listClassRows,
  submitCorrection,
} from "../lib/dataApi";
import type { PublishedSpec } from "../types/spec";

type ActionKind = "property" | "merge" | "split" | "tombstone" | "reject";

interface QueryResult {
  publishedSpec: PublishedSpec | null;
}

export default function Corrections() {
  const { data, loading, error } = useQuery<QueryResult>(PUBLISHED_SPEC, {
    fetchPolicy: "network-only",
  });
  const spec = data?.publishedSpec ?? null;

  const concreteClasses = useMemo(() => {
    if (!spec) return [];
    // Skip abstract classes (no rows stored).
    return spec.classes.filter((c) => !c.abstract).map((c) => c.name);
  }, [spec]);

  const [className, setClassName] = useState<string>("");
  useEffect(() => {
    if (!className && concreteClasses.length > 0) {
      setClassName(concreteClasses[0]);
    }
  }, [className, concreteClasses]);

  const [rows, setRows] = useState<ContributionRow[]>([]);
  const [rowsLoading, setRowsLoading] = useState(false);
  const [rowsError, setRowsError] = useState<string | null>(null);

  const refreshRows = useCallback(async () => {
    if (!className) return;
    setRowsLoading(true);
    setRowsError(null);
    try {
      const resp = await listClassRows(className, { limit: 200 });
      setRows(resp.rows as ContributionRow[]);
    } catch (e) {
      setRowsError(String(e));
    } finally {
      setRowsLoading(false);
    }
  }, [className]);

  useEffect(() => {
    void refreshRows();
  }, [refreshRows]);

  const grouped = useMemo(() => {
    const byId = groupByCanonicalId(rows);
    return Array.from(byId.entries()).map(([canonical_id, rs]) => ({
      canonical_id,
      rows: rs,
    }));
  }, [rows]);

  const [selectedCid, setSelectedCid] = useState<string | null>(null);
  const [action, setAction] = useState<ActionKind | "add" | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const onSubmit = useCallback(
    async (body: CorrectionBody) => {
      try {
        const resp = await submitCorrection(body);
        setStatus(
          `applied ${body.type} as correction #${resp.id} (revision ${resp.applied_revision})`,
        );
        setAction(null);
        await refreshRows();
      } catch (e) {
        setStatus(`error: ${String(e)}`);
      }
    },
    [refreshRows],
  );

  if (loading || !spec) {
    return (
      <div className="p-6 text-sm text-knot-muted">
        {error ? `error: ${String(error)}` : "loading published spec…"}
      </div>
    );
  }

  const selectedGroup = selectedCid
    ? grouped.find((g) => g.canonical_id === selectedCid)
    : null;
  const selectedContribs = selectedGroup?.rows ?? [];

  return (
    <div className="p-4 max-w-5xl mx-auto space-y-4">
      <header className="flex items-center gap-3">
        <h1 className="text-lg font-semibold">Corrections console</h1>
        <span className="text-xs text-knot-muted">
          POST → <code>/graph/corrections</code>
        </span>
      </header>

      {/* Class picker */}
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium" htmlFor="class-picker">
          Class:
        </label>
        <select
          id="class-picker"
          className="border rounded px-2 py-1 text-sm"
          value={className}
          onChange={(e) => {
            setClassName(e.target.value);
            setSelectedCid(null);
            setAction(null);
          }}
        >
          {concreteClasses.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => {
            setSelectedCid(null);
            setAction("add");
          }}
          className="ml-2 px-2 py-1 text-sm rounded border hover:bg-slate-50"
        >
          Add new…
        </button>
        <button
          type="button"
          onClick={() => void refreshRows()}
          disabled={rowsLoading}
          className="px-2 py-1 text-sm rounded border hover:bg-slate-50 disabled:opacity-50"
        >
          {rowsLoading ? "Refreshing…" : "Refresh"}
        </button>
        {rowsError && (
          <span className="text-xs text-red-700 ml-2">{rowsError}</span>
        )}
      </div>

      {status && (
        <div
          className={`text-sm border rounded px-3 py-2 ${
            status.startsWith("error")
              ? "border-red-300 bg-red-50 text-red-800"
              : "border-green-300 bg-green-50 text-green-800"
          }`}
        >
          {status}
        </div>
      )}

      {/* Row list */}
      <div className="border rounded">
        <div className="px-3 py-2 border-b bg-slate-50 text-xs uppercase tracking-wide text-knot-muted">
          {grouped.length} canonical_ids
        </div>
        <ul className="divide-y">
          {grouped.map((g) => {
            const isOpen = selectedCid === g.canonical_id;
            return (
              <li key={g.canonical_id} className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setSelectedCid(isOpen ? null : g.canonical_id);
                      setAction(null);
                    }}
                    className="font-mono text-sm text-blue-700 hover:underline"
                  >
                    {g.canonical_id}
                  </button>
                  <span className="text-xs text-knot-muted">
                    ({g.rows.length} contribution{g.rows.length === 1 ? "" : "s"})
                  </span>
                  {isOpen && (
                    <div className="ml-auto flex gap-1">
                      <ActionButton onClick={() => setAction("property")}>Edit property</ActionButton>
                      <ActionButton onClick={() => setAction("merge")}>Merge…</ActionButton>
                      <ActionButton onClick={() => setAction("split")}>Split…</ActionButton>
                      <ActionButton onClick={() => setAction("reject")}>Reject src</ActionButton>
                      <ActionButton onClick={() => setAction("tombstone")} danger>Tombstone</ActionButton>
                    </div>
                  )}
                </div>
                {isOpen && (
                  <pre className="mt-2 text-xs bg-slate-50 border rounded p-2 overflow-x-auto">
                    {JSON.stringify(g.rows, null, 2)}
                  </pre>
                )}
              </li>
            );
          })}
          {grouped.length === 0 && !rowsLoading && (
            <li className="px-3 py-4 text-sm italic text-knot-muted">
              no rows for {className}
            </li>
          )}
        </ul>
      </div>

      {/* Active form */}
      {action === "add" && (
        <FormCard title="Add new row">
          <AddForm
            spec={spec}
            className={className}
            onSubmit={async ({ newCanonicalId, values }) => {
              await onSubmit({
                type: "add",
                class_name: className,
                new_canonical_id: newCanonicalId,
                values,
              });
            }}
          />
        </FormCard>
      )}
      {action === "property" && selectedCid && (
        <FormCard title={`Edit property on ${selectedCid}`}>
          <PropertyCorrectionForm
            spec={spec}
            className={className}
            canonicalId={selectedCid}
            onSubmit={async ({ slot, value }) => {
              await onSubmit({
                type: "property",
                class_name: className,
                canonical_id: selectedCid,
                slot,
                value,
              });
            }}
          />
        </FormCard>
      )}
      {action === "merge" && selectedCid && (
        <FormCard title={`Merge other canonical_ids into ${selectedCid}`}>
          <MergeForm
            className={className}
            keepCanonicalId={selectedCid}
            onSubmit={async ({ mergeCanonicalIds }) => {
              await onSubmit({
                type: "merge",
                class_name: className,
                keep_canonical_id: selectedCid,
                merge_canonical_ids: mergeCanonicalIds,
              });
            }}
          />
        </FormCard>
      )}
      {action === "split" && selectedCid && (
        <FormCard title={`Split ${selectedCid}`}>
          <SplitForm
            className={className}
            sourceCanonicalId={selectedCid}
            contributions={selectedContribs}
            onSubmit={async ({ partitions }) => {
              await onSubmit({
                type: "split",
                class_name: className,
                source_canonical_id: selectedCid,
                partitions,
              });
            }}
          />
        </FormCard>
      )}
      {action === "reject" && selectedCid && (
        <FormCard title={`Reject one source on ${selectedCid}`}>
          <RejectContributionForm
            className={className}
            canonicalId={selectedCid}
            contributions={selectedContribs}
            onSubmit={async ({ source }) => {
              await onSubmit({
                type: "reject_contribution",
                class_name: className,
                canonical_id: selectedCid,
                source,
              });
            }}
          />
        </FormCard>
      )}
      {action === "tombstone" && selectedCid && (
        <FormCard title={`Tombstone ${selectedCid}`}>
          <TombstoneForm
            className={className}
            canonicalId={selectedCid}
            onSubmit={async ({ reason }) => {
              await onSubmit({
                type: "tombstone",
                class_name: className,
                canonical_id: selectedCid,
                reason,
              });
            }}
          />
        </FormCard>
      )}
    </div>
  );
}

function ActionButton({
  children,
  onClick,
  danger,
}: {
  children: React.ReactNode;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-xs px-2 py-0.5 rounded border hover:bg-slate-50 ${
        danger ? "border-red-300 text-red-700 hover:bg-red-50" : ""
      }`}
    >
      {children}
    </button>
  );
}

function FormCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border rounded p-4 bg-white">
      <h2 className="text-sm font-semibold mb-3">{title}</h2>
      {children}
    </section>
  );
}
