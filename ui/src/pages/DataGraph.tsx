/**
 * /data-graph — Data plane viewer.
 *
 * Renders canonical entities from the published spec as cards on a React
 * Flow canvas. Each card shows the trust-resolved values plus an expandable
 * per-source contributions table. Cross-class FK relationships draw edges
 * between cards (when both endpoints are loaded). The card's ⋯ menu opens
 * typed-correction forms (property edit, merge, split, add, tombstone,
 * reject contribution).
 *
 * Data flow:
 *   1. Load the published spec via Apollo (cached at the app level).
 *   2. For each selected class, fetch the row list (per-source contributions).
 *      The first page renders immediately with "loading…" in resolved fields.
 *   3. In parallel, fetch the resolved view per canonical_id (Promise.all
 *      with a small concurrency cap to avoid hammering the API). As each
 *      resolved record returns, the corresponding card flips out of loading.
 *   4. After any correction, refresh the affected entity (or the whole canvas
 *      for merge / split / add). On Add, also re-list the class so the new
 *      canonical_id shows up.
 */
import { useQuery } from "@apollo/client";
import {
  ReactFlow,
  Background,
  Controls,
  applyNodeChanges,
  applyEdgeChanges,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast, { Toaster } from "react-hot-toast";

import EntityCard from "../components/nodes/EntityCard";
import AddForm from "../components/corrections/AddForm";
import MergeForm from "../components/corrections/MergeForm";
import PropertyCorrectionForm from "../components/corrections/PropertyCorrectionForm";
import RejectContributionForm from "../components/corrections/RejectContributionForm";
import SplitForm from "../components/corrections/SplitForm";
import TombstoneForm from "../components/corrections/TombstoneForm";
import Modal from "../components/Modal";
import { PUBLISHED_SPEC } from "../graphql/queries";
import {
  buildDataGraph,
  type EntityEdge,
  type EntityInput,
  type EntityNode,
  type EntityNodeData,
} from "../lib/buildDataGraph";
import { ApiError } from "../lib/draftApi";
import * as dataApi from "../lib/dataApi";
import type {
  ContributionRow,
  ResolvedRecord,
} from "../lib/dataApi";
import { layoutGraph } from "../lib/layout";
import { useLocalStorage } from "../lib/useLocalStorage";
import type { PublishedSpec } from "../types/spec";

const NODE_TYPES = { entityCard: EntityCard };

interface PublishedSpecResult {
  publishedSpec: PublishedSpec | null;
}

/** A loaded entity. resolved=null + resolvedLoading=true means in flight. */
interface LoadedEntity {
  className: string;
  canonicalId: string;
  contributions: ContributionRow[];
  resolved: ResolvedRecord | null;
  resolvedLoading: boolean;
  tombstoned: boolean;
}

type CorrectionKind =
  | "property"
  | "merge"
  | "split"
  | "add"
  | "tombstone"
  | "reject_contribution";

interface CorrectionTarget {
  kind: CorrectionKind;
  className: string;
  canonicalId: string;
}

/** Concurrency cap for parallel resolved-view fetches. */
const RESOLVE_CONCURRENCY = 6;

export default function DataGraph() {
  // ── Settings (sticky via localStorage) ────────────────────────────────────
  const [selectedClasses, setSelectedClasses] = useLocalStorage<string[]>(
    "knot:data:classes",
    [],
  );
  const [perClassLimit, setPerClassLimit] = useLocalStorage<number>(
    "knot:data:limit",
    50,
  );
  const [showTombstoned, setShowTombstoned] = useLocalStorage<boolean>(
    "knot:data:show-tombstoned",
    false,
  );

  // ── Spec ──────────────────────────────────────────────────────────────────
  const specQ = useQuery<PublishedSpecResult>(PUBLISHED_SPEC, {
    fetchPolicy: "cache-and-network",
  });
  const spec: PublishedSpec | null = specQ.data?.publishedSpec ?? null;

  // Default selectedClasses to all non-abstract once spec is loaded.
  useEffect(() => {
    if (!spec || selectedClasses.length > 0) return;
    const ns = spec.classes.filter((c) => !c.abstract).map((c) => c.name);
    if (ns.length > 0) setSelectedClasses(ns);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec]);

  // ── Loaded entities + node positions ──────────────────────────────────────
  const [entities, setEntities] = useState<LoadedEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<{ className: string; canonicalId: string } | null>(null);
  const [correction, setCorrection] = useState<CorrectionTarget | null>(null);
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});

  // ── Load class rows + spin up resolved-view fetches ───────────────────────
  const loadAll = useCallback(async () => {
    if (!spec) return;
    setLoading(true);
    setEntities([]);
    positionsRef.current = {};
    try {
      // Step 1: list contributions for each selected class in parallel.
      const lists = await Promise.all(
        selectedClasses.map((cn) =>
          dataApi
            .listClassRows(cn, {
              limit: perClassLimit,
              includeTombstoned: showTombstoned,
            })
            .then(
              (resp) => ({ className: cn, resp, error: null as string | null }),
              (e) => ({
                className: cn,
                resp: null as dataApi.ListResponse | null,
                error: formatErr(e),
              }),
            ),
        ),
      );

      const initial: LoadedEntity[] = [];
      for (const { className, resp, error } of lists) {
        if (error) {
          toast.error(`Failed to load ${className}: ${error}`);
          continue;
        }
        if (!resp) continue;
        const grouped = dataApi.groupByCanonicalId(resp.rows);
        for (const [canonicalId, rows] of grouped) {
          // valid_to-null filter is already applied server-side. We surface
          // tombstones (when included) by checking that all rows for the
          // canonical_id have been closed — but the API hides this state,
          // so when `includeTombstoned` is on we trust the server's filter
          // and just mark tombstoned=false here. Server-side, tombstoned
          // entities only appear when include_tombstoned=true, so we use
          // that as a proxy.
          initial.push({
            className,
            canonicalId,
            contributions: rows,
            resolved: null,
            resolvedLoading: true,
            tombstoned: false,
          });
        }
      }
      setEntities(initial);
      setLoading(false);

      // Step 2: kick off resolved-view fetches with bounded concurrency.
      await fetchResolvedConcurrent(initial, (className, canonicalId, resolved, err) => {
        if (err) {
          // 404 is common when the resolution policy zeroes out every slot;
          // don't toast every one. Just mark loading=false.
          setEntities((cur) =>
            cur.map((e) =>
              e.className === className && e.canonicalId === canonicalId
                ? { ...e, resolved: null, resolvedLoading: false }
                : e,
            ),
          );
          return;
        }
        setEntities((cur) =>
          cur.map((e) =>
            e.className === className && e.canonicalId === canonicalId
              ? { ...e, resolved, resolvedLoading: false }
              : e,
          ),
        );
      });
    } catch (e) {
      toast.error(`Load failed: ${formatErr(e)}`);
      setLoading(false);
    }
  }, [spec, selectedClasses, perClassLimit, showTombstoned]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  // ── React Flow nodes & edges (rebuilt when entities change) ───────────────
  const [nodes, setNodes] = useState<EntityNode[]>([]);
  const [edges, setEdges] = useState<EntityEdge[]>([]);

  const onAction = useCallback(
    (action: { kind: CorrectionKind | "select"; className: string; canonicalId: string }) => {
      if (action.kind === "select") {
        setSelected({ className: action.className, canonicalId: action.canonicalId });
        return;
      }
      setCorrection({
        kind: action.kind,
        className: action.className,
        canonicalId: action.canonicalId,
      });
    },
    [],
  );

  useEffect(() => {
    if (!spec) {
      setNodes([]);
      setEdges([]);
      return;
    }
    let cancelled = false;
    const inputs: EntityInput[] = entities.map((e) => ({
      className: e.className,
      canonicalId: e.canonicalId,
      contributions: e.contributions,
      resolved: e.resolved,
      resolvedLoading: e.resolvedLoading,
      tombstoned: e.tombstoned,
    }));
    const { nodes: ns, edges: es } = buildDataGraph(spec, inputs);
    // Inject the action handler into each node's data so EntityCard can call it.
    const withHandler = ns.map((n) => ({
      ...n,
      data: { ...n.data, onAction } as EntityNodeData,
    }));
    // Preserve any saved positions across rebuilds.
    const preserved = withHandler.map((n) => {
      const saved = positionsRef.current[n.id];
      return saved ? { ...n, position: saved } : n;
    });
    layoutGraph(preserved, es, { width: 320, height: 240 }).then((laid) => {
      if (cancelled) return;
      const merged = laid.map((n) => {
        const saved = positionsRef.current[n.id];
        return saved ? { ...n, position: saved } : n;
      });
      setNodes(merged);
      setEdges(es);
    });
    return () => {
      cancelled = true;
    };
  }, [spec, entities, onAction]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => {
      const next = applyNodeChanges(changes, nds) as EntityNode[];
      for (const n of next) positionsRef.current[n.id] = n.position;
      return next;
    });
  }, []);
  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((es) => applyEdgeChanges(changes, es) as EntityEdge[]);
  }, []);

  const onNodeClick = useCallback(
    (_: unknown, node: Node) => {
      const data = node.data as EntityNodeData;
      setSelected({ className: data.className, canonicalId: data.canonicalId });
    },
    [],
  );
  const onPaneClick = useCallback(() => setSelected(null), []);

  // ── Refresh strategy ──────────────────────────────────────────────────────
  /**
   * Re-fetch a single entity's contributions + resolved view. If the entity
   * no longer exists (404), drop it from the canvas.
   */
  const refreshEntity = useCallback(
    async (className: string, canonicalId: string): Promise<boolean> => {
      try {
        const [contribsResp, resolvedResp] = await Promise.all([
          dataApi
            .getEntity(className, canonicalId, { includeTombstoned: showTombstoned })
            .catch((e) => {
              if (e instanceof ApiError && e.status === 404) return null;
              throw e;
            }),
          dataApi.getResolvedEntity(className, canonicalId).catch((e) => {
            if (e instanceof ApiError && e.status === 404) return null;
            throw e;
          }),
        ]);
        if (!contribsResp) {
          setEntities((cur) =>
            cur.filter((e) => !(e.className === className && e.canonicalId === canonicalId)),
          );
          return false;
        }
        setEntities((cur) => {
          const idx = cur.findIndex(
            (e) => e.className === className && e.canonicalId === canonicalId,
          );
          const next: LoadedEntity = {
            className,
            canonicalId,
            contributions: contribsResp.contributions,
            resolved: resolvedResp?.resolved ?? null,
            resolvedLoading: false,
            tombstoned: false,
          };
          if (idx === -1) return [...cur, next];
          const out = [...cur];
          out[idx] = next;
          return out;
        });
        return true;
      } catch (e) {
        toast.error(`Refresh failed for ${className}:${canonicalId}: ${formatErr(e)}`);
        return false;
      }
    },
    [showTombstoned],
  );

  // ── Correction submission + post-refresh ──────────────────────────────────
  const onCorrectionDone = useCallback(
    async (
      kind: CorrectionKind,
      className: string,
      canonicalId: string,
      result: dataApi.CorrectionResponse,
      payload?: {
        mergeIds?: string[];
        partitionsKeys?: string[];
        newCanonicalId?: string;
      },
    ) => {
      toast.success(`Correction applied (id ${result.id})`);
      setCorrection(null);

      switch (kind) {
        case "property":
        case "reject_contribution":
          await refreshEntity(className, canonicalId);
          break;
        case "tombstone":
          // Will 404 if include_tombstoned is off → drop from canvas.
          await refreshEntity(className, canonicalId);
          break;
        case "merge": {
          // Refresh the keep + each merged-in id (the latter usually 404 out).
          await Promise.all([
            refreshEntity(className, canonicalId),
            ...(payload?.mergeIds ?? []).map((mid) =>
              refreshEntity(className, mid),
            ),
          ]);
          break;
        }
        case "split": {
          // The source canonical_id is gone; the partition ids are new — but
          // we can't reliably learn their canonical_ids without re-listing the
          // class, because the API doesn't surface them in the correction
          // response. Just reload everything.
          await loadAll();
          break;
        }
        case "add": {
          if (payload?.newCanonicalId) {
            await refreshEntity(className, payload.newCanonicalId);
          } else {
            await loadAll();
          }
          break;
        }
      }
    },
    [refreshEntity, loadAll],
  );

  // ── Render ────────────────────────────────────────────────────────────────
  if (specQ.error) {
    return (
      <div className="p-6 text-rose-700">
        Failed to load spec: {String(specQ.error.message)}
      </div>
    );
  }
  if (!spec) {
    return (
      <div className="p-6 text-slate-500 text-sm">Loading spec…</div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <Toaster position="top-right" />
      <DataToolbar
        spec={spec}
        selectedClasses={selectedClasses}
        onClassesChange={setSelectedClasses}
        perClassLimit={perClassLimit}
        onPerClassLimitChange={setPerClassLimit}
        showTombstoned={showTombstoned}
        onShowTombstonedChange={setShowTombstoned}
        onRefresh={loadAll}
        loading={loading}
        selection={selected}
        onAddClass={(cn) => setCorrection({ kind: "add", className: cn, canonicalId: "" })}
      />
      <div className="flex-1 relative">
        {loading && entities.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/60 z-10 text-slate-500 text-sm">
            Loading entities…
          </div>
        )}
        {!loading && entities.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-slate-500 text-sm">
            No entities loaded. Adjust class filter or ingest some data.
          </div>
        )}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          fitView
        >
          <Background gap={20} />
          <Controls />
        </ReactFlow>
      </div>

      {correction && spec && (
        <CorrectionModal
          spec={spec}
          target={correction}
          entities={entities}
          onClose={() => setCorrection(null)}
          onDone={onCorrectionDone}
        />
      )}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Toolbar
// ────────────────────────────────────────────────────────────────────────────

function DataToolbar({
  spec,
  selectedClasses,
  onClassesChange,
  perClassLimit,
  onPerClassLimitChange,
  showTombstoned,
  onShowTombstonedChange,
  onRefresh,
  loading,
  selection,
  onAddClass,
}: {
  spec: PublishedSpec;
  selectedClasses: string[];
  onClassesChange: (next: string[]) => void;
  perClassLimit: number;
  onPerClassLimitChange: (n: number) => void;
  showTombstoned: boolean;
  onShowTombstonedChange: (b: boolean) => void;
  onRefresh: () => void;
  loading: boolean;
  selection: { className: string; canonicalId: string } | null;
  onAddClass: (className: string) => void;
}) {
  const concreteClasses = useMemo(
    () => spec.classes.filter((c) => !c.abstract),
    [spec],
  );

  const toggle = (cn: string) => {
    onClassesChange(
      selectedClasses.includes(cn)
        ? selectedClasses.filter((n) => n !== cn)
        : [...selectedClasses, cn],
    );
  };

  return (
    <div className="flex items-center gap-2 flex-wrap p-2 border-b border-slate-200 bg-slate-50 text-sm">
      <span className="text-xs uppercase text-slate-500 tracking-wide">
        classes
      </span>
      <div className="flex gap-1 flex-wrap max-w-[55%]">
        {concreteClasses.map((c) => (
          <button
            key={c.name}
            type="button"
            onClick={() => toggle(c.name)}
            className={`text-xs px-2 py-0.5 rounded border ${
              selectedClasses.includes(c.name)
                ? "bg-blue-100 border-blue-300 text-blue-900"
                : "bg-white border-slate-300 text-slate-600 hover:bg-slate-100"
            }`}
          >
            {c.name}
          </button>
        ))}
      </div>

      <div className="border-l border-slate-300 h-6 mx-1" />

      <label className="flex items-center gap-1.5 text-xs text-slate-600">
        limit
        <input
          type="number"
          min={1}
          max={1000}
          value={perClassLimit}
          onChange={(e) =>
            onPerClassLimitChange(
              Math.max(1, Math.min(1000, Number(e.target.value) || 50)),
            )
          }
          className="w-16 px-1.5 py-0.5 border border-slate-300 rounded text-xs"
        />
      </label>

      <label className="flex items-center gap-1.5 text-xs text-slate-600 cursor-pointer select-none">
        <input
          type="checkbox"
          checked={showTombstoned}
          onChange={(e) => onShowTombstonedChange(e.target.checked)}
        />
        show tombstoned
      </label>

      <button
        onClick={onRefresh}
        disabled={loading}
        className="px-2 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100 disabled:opacity-50"
        title="Refresh"
      >
        ↻ Refresh
      </button>

      {selectedClasses.length > 0 && (
        <div className="relative">
          <AddButton classes={selectedClasses} onPick={onAddClass} />
        </div>
      )}

      <div className="flex-1" />

      {selection && (
        <div className="text-xs text-slate-600">
          <span className="text-slate-400">selected:</span>{" "}
          <span className="font-mono text-slate-800">
            {selection.className}:{selection.canonicalId}
          </span>
        </div>
      )}
    </div>
  );
}

function AddButton({
  classes,
  onPick,
}: {
  classes: string[];
  onPick: (cn: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((b) => !b)}
        className="px-2 py-1 rounded border border-slate-300 bg-white hover:bg-slate-100 text-xs"
      >
        + Add entity
      </button>
      {open && (
        <div
          className="absolute z-30 mt-1 right-0 bg-white border border-slate-300 rounded shadow-lg w-44"
          onMouseLeave={() => setOpen(false)}
        >
          {classes.map((cn) => (
            <button
              key={cn}
              type="button"
              className="block w-full text-left px-3 py-1 text-xs font-mono hover:bg-slate-100 text-slate-700"
              onClick={() => {
                onPick(cn);
                setOpen(false);
              }}
            >
              {cn}
            </button>
          ))}
        </div>
      )}
    </>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Modal dispatcher
// ────────────────────────────────────────────────────────────────────────────

function CorrectionModal({
  spec,
  target,
  entities,
  onClose,
  onDone,
}: {
  spec: PublishedSpec;
  target: CorrectionTarget;
  entities: LoadedEntity[];
  onClose: () => void;
  onDone: (
    kind: CorrectionKind,
    className: string,
    canonicalId: string,
    result: dataApi.CorrectionResponse,
    payload?: { mergeIds?: string[]; partitionsKeys?: string[]; newCanonicalId?: string },
  ) => Promise<void> | void;
}) {
  const titleFor = () => {
    const cid = target.canonicalId ? `:${target.canonicalId}` : "";
    switch (target.kind) {
      case "property":
        return `Edit property — ${target.className}${cid}`;
      case "merge":
        return `Merge into ${target.canonicalId}`;
      case "split":
        return `Split ${target.canonicalId}`;
      case "add":
        return `Add new ${target.className}`;
      case "tombstone":
        return `Tombstone ${target.canonicalId}`;
      case "reject_contribution":
        return `Reject a contribution to ${target.canonicalId}`;
    }
  };

  const entity = entities.find(
    (e) => e.className === target.className && e.canonicalId === target.canonicalId,
  );

  const body = (() => {
    switch (target.kind) {
      case "property":
        return (
          <PropertyCorrectionForm
            spec={spec}
            className={target.className}
            canonicalId={target.canonicalId}
            onSubmit={async ({ slot, value }) => {
              const r = await dataApi.submitCorrection({
                type: "property",
                class_name: target.className,
                canonical_id: target.canonicalId,
                slot,
                value,
              });
              await onDone("property", target.className, target.canonicalId, r);
            }}
          />
        );
      case "merge":
        return (
          <MergeForm
            className={target.className}
            keepCanonicalId={target.canonicalId}
            onSubmit={async ({ mergeCanonicalIds }) => {
              const r = await dataApi.submitCorrection({
                type: "merge",
                class_name: target.className,
                keep_canonical_id: target.canonicalId,
                merge_canonical_ids: mergeCanonicalIds,
              });
              await onDone("merge", target.className, target.canonicalId, r, {
                mergeIds: mergeCanonicalIds,
              });
            }}
          />
        );
      case "split":
        return (
          <SplitForm
            className={target.className}
            sourceCanonicalId={target.canonicalId}
            contributions={entity?.contributions ?? []}
            onSubmit={async ({ partitions }) => {
              const r = await dataApi.submitCorrection({
                type: "split",
                class_name: target.className,
                source_canonical_id: target.canonicalId,
                partitions,
              });
              await onDone("split", target.className, target.canonicalId, r, {
                partitionsKeys: Object.keys(partitions),
              });
            }}
          />
        );
      case "add":
        return (
          <AddForm
            spec={spec}
            className={target.className}
            onSubmit={async ({ newCanonicalId, values }) => {
              const r = await dataApi.submitCorrection({
                type: "add",
                class_name: target.className,
                new_canonical_id: newCanonicalId,
                values,
              });
              await onDone("add", target.className, newCanonicalId, r, {
                newCanonicalId,
              });
            }}
          />
        );
      case "tombstone":
        return (
          <TombstoneForm
            className={target.className}
            canonicalId={target.canonicalId}
            onSubmit={async ({ reason }) => {
              const r = await dataApi.submitCorrection({
                type: "tombstone",
                class_name: target.className,
                canonical_id: target.canonicalId,
                reason,
              });
              await onDone("tombstone", target.className, target.canonicalId, r);
            }}
          />
        );
      case "reject_contribution":
        return (
          <RejectContributionForm
            className={target.className}
            canonicalId={target.canonicalId}
            contributions={entity?.contributions ?? []}
            onSubmit={async ({ source }) => {
              const r = await dataApi.submitCorrection({
                type: "reject_contribution",
                class_name: target.className,
                canonical_id: target.canonicalId,
                source,
              });
              await onDone(
                "reject_contribution",
                target.className,
                target.canonicalId,
                r,
              );
            }}
          />
        );
    }
  })();

  return (
    <Modal open onClose={onClose} title={titleFor()} widthClass="w-[640px]">
      {body}
    </Modal>
  );
}

// ────────────────────────────────────────────────────────────────────────────
// Helpers
// ────────────────────────────────────────────────────────────────────────────

function formatErr(e: unknown): string {
  if (e instanceof ApiError) {
    const body = typeof e.body === "string" ? e.body : JSON.stringify(e.body);
    return `${e.status} ${body}`;
  }
  return String((e as Error)?.message ?? e);
}

/**
 * Drive resolved-view fetches for every entity with bounded concurrency.
 * The callback fires per entity as soon as its resolved record returns (or
 * errors); the caller is responsible for updating state.
 */
async function fetchResolvedConcurrent(
  entities: LoadedEntity[],
  onOne: (
    className: string,
    canonicalId: string,
    resolved: ResolvedRecord | null,
    err: unknown,
  ) => void,
): Promise<void> {
  let idx = 0;
  const worker = async () => {
    while (idx < entities.length) {
      const i = idx;
      idx += 1;
      const e = entities[i];
      try {
        const r = await dataApi.getResolvedEntity(e.className, e.canonicalId);
        onOne(e.className, e.canonicalId, r.resolved, null);
      } catch (err) {
        onOne(e.className, e.canonicalId, null, err);
      }
    }
  };
  await Promise.all(
    Array.from({ length: Math.min(RESOLVE_CONCURRENCY, entities.length) }, worker),
  );
}

