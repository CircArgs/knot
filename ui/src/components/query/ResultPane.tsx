/**
 * Tabbed result view. JSON tab is the always-works default; Graph tab tries
 * to render the response visually via `GraphView`.
 *
 * Errors (GraphQL `errors[]` or transport failures) get a red banner on top
 * of whichever tab is active — so you always see them no matter which tab
 * you happen to be on.
 */
import { useState } from "react";

import JsonView from "./JsonView";
import GraphView from "./GraphView";

interface Props {
  /** Whole response object from the server, or `null` before first run. */
  response: { data?: unknown; errors?: unknown[] } | null;
  loading: boolean;
}

type Tab = "json" | "graph";

export default function ResultPane({ response, loading }: Props) {
  const [tab, setTab] = useState<Tab>("json");

  return (
    <div className="flex flex-col h-full">
      <div className="border-b flex items-center gap-1 px-2 py-1 text-sm bg-slate-50">
        <TabButton active={tab === "json"} onClick={() => setTab("json")}>
          JSON
        </TabButton>
        <TabButton active={tab === "graph"} onClick={() => setTab("graph")}>
          Graph
        </TabButton>
        {loading && (
          <span className="ml-auto text-xs text-knot-muted">running…</span>
        )}
      </div>

      {response?.errors && response.errors.length > 0 && (
        <div className="bg-red-50 border-b border-red-200 text-red-800 text-xs px-3 py-2 overflow-auto max-h-32">
          <div className="font-semibold mb-1">
            {response.errors.length} error{response.errors.length === 1 ? "" : "s"}
          </div>
          <pre className="whitespace-pre-wrap">
            {JSON.stringify(response.errors, null, 2)}
          </pre>
        </div>
      )}

      <div className="flex-1 min-h-0">
        {response === null ? (
          <div className="p-6 text-sm text-knot-muted">
            Press Run (or Cmd+Enter) to execute a query.
          </div>
        ) : tab === "json" ? (
          <JsonView value={response} />
        ) : (
          <GraphView data={response} />
        )}
      </div>
    </div>
  );
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={
        "px-3 py-1 rounded text-xs " +
        (active
          ? "bg-white border border-slate-300 font-medium"
          : "text-knot-muted hover:bg-white")
      }
      onClick={onClick}
    >
      {children}
    </button>
  );
}
