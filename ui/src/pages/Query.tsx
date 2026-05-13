/**
 * /query — GraphQL playground for the data plane.
 *
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ toolbar: Run | Reset                                            │
 *   ├──────────────┬──────────────────────────────────────────────────┤
 *   │ schema       │ editor (top)                                     │
 *   │ explorer     ├──────────────────────────────────────────────────┤
 *   │              │ result pane (bottom): JSON / Graph tabs          │
 *   └──────────────┴──────────────────────────────────────────────────┘
 *
 * State that survives reloads (via localStorage):
 *   - last query text
 *
 * Introspection result is cached in component state.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GraphQLSchema, IntrospectionQuery } from "graphql";

import QueryEditor from "../components/query/QueryEditor";
import SchemaExplorer from "../components/query/SchemaExplorer";
import ResultPane from "../components/query/ResultPane";
import { useLocalStorage } from "../lib/useLocalStorage";
import { ENDPOINTS } from "../lib/queryEndpoints";
import { introspectEndpoint, runGraphQL } from "../lib/introspect";

interface EndpointState {
  introspection: IntrospectionQuery | null;
  schema: GraphQLSchema | null;
  error: string | null;
  loading: boolean;
}

const EMPTY_STATE: EndpointState = {
  introspection: null,
  schema: null,
  error: null,
  loading: false,
};

export default function Query() {
  const [query, setQuery] = useLocalStorage<string>(
    "knot:query:editor:data",
    ENDPOINTS.data.defaultQuery,
  );
  const [sidebarOpen, setSidebarOpen] = useLocalStorage<boolean>(
    "knot:query:sidebar",
    true,
  );

  const [state, setState] = useState<EndpointState>(EMPTY_STATE);
  const [response, setResponse] = useState<
    { data?: unknown; errors?: unknown[] } | null
  >(null);
  const [running, setRunning] = useState(false);

  useEffect(() => {
    if (state.introspection || state.loading) return;
    let cancelled = false;
    setState({ ...EMPTY_STATE, loading: true });
    introspectEndpoint(ENDPOINTS.data.path)
      .then((r) => {
        if (cancelled) return;
        setState({
          introspection: r.introspection,
          schema: r.schema,
          error: null,
          loading: false,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setState({
          introspection: null,
          schema: null,
          error: String(e),
          loading: false,
        });
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRun = useCallback(async () => {
    setRunning(true);
    try {
      const body = await runGraphQL(ENDPOINTS.data.path, query);
      setResponse(body);
    } catch (e) {
      setResponse({ errors: [{ message: String(e) }] });
    } finally {
      setRunning(false);
    }
  }, [query]);

  const handleInsert = useCallback(
    (text: string) => {
      const sep = query.endsWith("\n") ? "" : "\n";
      setQuery(`${query}${sep}${text}\n`);
    },
    [query, setQuery],
  );

  const handleReset = useCallback(() => {
    setQuery(ENDPOINTS.data.defaultQuery);
    setResponse(null);
  }, [setQuery]);

  // Page-level Cmd/Ctrl+Enter — works even when focus is outside the editor.
  const runRef = useRef(handleRun);
  runRef.current = handleRun;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        runRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const sidebarClass = useMemo(
    () => (sidebarOpen ? "w-64" : "w-8"),
    [sidebarOpen],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Toolbar */}
      <div className="border-b px-3 py-2 flex items-center gap-3">
        <span className="text-sm font-semibold text-knot-ink">data graph</span>
        <span className="text-xs text-knot-muted">/graph/query</span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={handleRun}
          disabled={running}
          className="px-3 py-1 rounded bg-knot-accent text-white text-sm font-medium hover:bg-blue-600 disabled:opacity-50"
          title="Run (Cmd/Ctrl+Enter)"
        >
          {running ? "Running…" : "Run"}
          <span className="ml-2 text-xs opacity-80">⌘↵</span>
        </button>
        <button
          type="button"
          onClick={handleReset}
          className="px-2 py-1 rounded border text-sm hover:bg-slate-50"
          title="Reset to default query"
        >
          Reset
        </button>
        {state.error && (
          <span className="text-xs text-red-700 ml-2">
            schema: {state.error}
          </span>
        )}
        {state.loading && (
          <span className="text-xs text-knot-muted ml-2">introspecting…</span>
        )}
      </div>

      {/* Body: sidebar | (editor / result) */}
      <div className="flex-1 flex min-h-0">
        <aside
          className={`border-r flex flex-col ${sidebarClass} transition-[width] duration-150`}
        >
          <div className="flex items-center justify-between px-2 py-1 border-b bg-slate-50">
            {sidebarOpen ? (
              <span className="text-xs uppercase tracking-wide text-knot-muted">
                schema
              </span>
            ) : (
              <span />
            )}
            <button
              type="button"
              className="text-xs px-1 text-knot-muted hover:text-knot-ink"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              title={sidebarOpen ? "Collapse sidebar" : "Expand sidebar"}
            >
              {sidebarOpen ? "‹" : "›"}
            </button>
          </div>
          {sidebarOpen && (
            <div className="flex-1 min-h-0">
              <SchemaExplorer
                introspection={state.introspection}
                onInsert={handleInsert}
              />
            </div>
          )}
        </aside>

        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 min-h-0 border-b">
            <QueryEditor
              value={query}
              onChange={setQuery}
              onRun={handleRun}
              schema={state.schema}
              endpointKey="data"
            />
          </div>
          <div className="flex-1 min-h-0">
            <ResultPane response={response} loading={running} schema={state.schema} />
          </div>
        </div>
      </div>
    </div>
  );
}
