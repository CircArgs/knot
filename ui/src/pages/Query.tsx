/**
 * /query — GraphQL playground for both knot surfaces.
 *
 * Layout (Tailwind grid):
 *   ┌─────────────────────────────────────────────────────────────────┐
 *   │ toolbar: endpoint switcher | Run | Reset                        │
 *   ├──────────────┬──────────────────────────────────────────────────┤
 *   │ schema       │ editor (top)                                     │
 *   │ explorer     ├──────────────────────────────────────────────────┤
 *   │              │ result pane (bottom): JSON / Graph tabs          │
 *   └──────────────┴──────────────────────────────────────────────────┘
 *
 * State that survives reloads (via localStorage):
 *   - selected endpoint
 *   - last query text per endpoint
 *
 * Introspection results are cached in component state (re-introspect on
 * first switch to each endpoint, then keep the cached schema).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GraphQLSchema, IntrospectionQuery } from "graphql";

import EndpointSwitcher from "../components/query/EndpointSwitcher";
import QueryEditor from "../components/query/QueryEditor";
import SchemaExplorer from "../components/query/SchemaExplorer";
import ResultPane from "../components/query/ResultPane";
import { useLocalStorage } from "../lib/useLocalStorage";
import {
  ENDPOINTS,
  type EndpointKey,
} from "../lib/queryEndpoints";
import { introspectEndpoint, runGraphQL } from "../lib/introspect";

interface EndpointState {
  introspection: IntrospectionQuery | null;
  schema: GraphQLSchema | null;
  /** Last fetch error if introspection failed; cleared on success. */
  error: string | null;
  /** True while introspection is in flight. */
  loading: boolean;
}

const EMPTY_STATE: EndpointState = {
  introspection: null,
  schema: null,
  error: null,
  loading: false,
};

export default function Query() {
  const [endpoint, setEndpoint] = useLocalStorage<EndpointKey>(
    "knot:query:endpoint",
    "spec",
  );
  const [querySpec, setQuerySpec] = useLocalStorage<string>(
    "knot:query:editor:spec",
    ENDPOINTS.spec.defaultQuery,
  );
  const [queryData, setQueryData] = useLocalStorage<string>(
    "knot:query:editor:data",
    ENDPOINTS.data.defaultQuery,
  );
  const [sidebarOpen, setSidebarOpen] = useLocalStorage<boolean>(
    "knot:query:sidebar",
    true,
  );

  const setQueryFor = (key: EndpointKey, text: string) =>
    key === "spec" ? setQuerySpec(text) : setQueryData(text);
  const queryFor = (key: EndpointKey) =>
    key === "spec" ? querySpec : queryData;

  // Per-endpoint introspection cache.
  const [specState, setSpecState] = useState<EndpointState>(EMPTY_STATE);
  const [dataState, setDataState] = useState<EndpointState>(EMPTY_STATE);
  const stateFor = (key: EndpointKey) =>
    key === "spec" ? specState : dataState;
  const setStateFor = (key: EndpointKey, next: EndpointState) =>
    key === "spec" ? setSpecState(next) : setDataState(next);

  const currentState = stateFor(endpoint);
  const currentQuery = queryFor(endpoint);

  // Result pane state — single shared object; switching endpoints clears it.
  const [response, setResponse] = useState<
    { data?: unknown; errors?: unknown[] } | null
  >(null);
  const [running, setRunning] = useState(false);

  // Introspect once per endpoint, on first activation.
  useEffect(() => {
    if (currentState.introspection || currentState.loading) return;
    let cancelled = false;
    setStateFor(endpoint, { ...EMPTY_STATE, loading: true });
    introspectEndpoint(ENDPOINTS[endpoint].path)
      .then((r) => {
        if (cancelled) return;
        setStateFor(endpoint, {
          introspection: r.introspection,
          schema: r.schema,
          error: null,
          loading: false,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setStateFor(endpoint, {
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
  }, [endpoint]);

  // Reset response when endpoint changes (the schemas are unrelated).
  useEffect(() => {
    setResponse(null);
  }, [endpoint]);

  const handleRun = useCallback(async () => {
    setRunning(true);
    try {
      const body = await runGraphQL(ENDPOINTS[endpoint].path, queryFor(endpoint));
      setResponse(body);
    } catch (e) {
      setResponse({ errors: [{ message: String(e) }] });
    } finally {
      setRunning(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint, querySpec, queryData]);

  // Used by SchemaExplorer to insert a field name at the end of the editor.
  // We keep it dumb: append-with-newline. Wiring it to Monaco's caret is a
  // nice-to-have we don't need today.
  const handleInsert = useCallback(
    (text: string) => {
      const current = queryFor(endpoint);
      const sep = current.endsWith("\n") ? "" : "\n";
      setQueryFor(endpoint, `${current}${sep}${text}\n`);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [endpoint, querySpec, queryData],
  );

  const handleReset = useCallback(() => {
    setQueryFor(endpoint, ENDPOINTS[endpoint].defaultQuery);
    setResponse(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint]);

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
        <EndpointSwitcher
          value={endpoint}
          onChange={setEndpoint}
          disabled={running}
        />
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
        {currentState.error && (
          <span className="text-xs text-red-700 ml-2">
            schema: {currentState.error}
          </span>
        )}
        {currentState.loading && (
          <span className="text-xs text-knot-muted ml-2">
            introspecting…
          </span>
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
                introspection={currentState.introspection}
                onInsert={handleInsert}
              />
            </div>
          )}
        </aside>

        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 min-h-0 border-b">
            <QueryEditor
              value={currentQuery}
              onChange={(v) => setQueryFor(endpoint, v)}
              onRun={handleRun}
              schema={currentState.schema}
              endpointKey={endpoint}
            />
          </div>
          <div className="flex-1 min-h-0">
            <ResultPane response={response} loading={running} />
          </div>
        </div>
      </div>
    </div>
  );
}
