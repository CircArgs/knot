import { useQuery } from "@apollo/client";
import {
  ReactFlow,
  Background,
  Controls,
  applyNodeChanges,
  applyEdgeChanges,
  type Connection,
  type EdgeChange,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import toast, { Toaster } from "react-hot-toast";
import { useNavigate, useParams } from "react-router-dom";

import ClassNode from "../components/nodes/ClassNode";
import SourceNode from "../components/nodes/SourceNode";
import SourceBindingNode from "../components/nodes/SourceBindingNode";
import Legend from "../components/Legend";
import Modal from "../components/Modal";
import PropertyPanel, {
  resolveSelection,
  type SpecSelection,
} from "../components/PropertyPanel";
import Toolbar, { type AddKind } from "../components/Toolbar";
import ClassForm, { type ClassFormValues, type InlineSlotRow } from "../components/forms/ClassForm";
import ConstraintForm from "../components/forms/ConstraintForm";
import SourceForm from "../components/forms/SourceForm";
import SourceBindingForm from "../components/forms/SourceBindingForm";
import { PUBLISHED_SPEC } from "../graphql/queries";
import {
  buildGraph,
  nodeId,
  type SpecEdge,
  type SpecNode,
  type SpecNodeData,
} from "../lib/buildGraph";
import * as api from "../lib/draftApi";
import { ApiError, normalizeDraftSpec } from "../lib/draftApi";
import { autoDetach, editEntityViaDeleteAdd } from "../lib/draftHelpers";
import { layoutGraph } from "../lib/layout";
import type { PublishedSpec, SpecEntity, SpecEntityKind } from "../types/spec";

const NODE_TYPES = {
  specClass: ClassNode,
  specSource: SourceNode,
  specSourceBinding: SourceBindingNode,
};

interface QueryResult {
  publishedSpec: PublishedSpec | null;
}

export default function SpecGraph() {
  const navigate = useNavigate();
  const { draftId: draftIdParam } = useParams<{ draftId?: string }>();
  const draftId = draftIdParam ? Number(draftIdParam) : null;
  const mode: "view" | "edit" = draftId !== null ? "edit" : "view";

  const [selection, setSelection] = useState<SpecSelection | null>(null);
  const [nodes, setNodes] = useState<SpecNode[]>([]);
  const [edges, setEdges] = useState<SpecEdge[]>([]);
  const [draftSpec, setDraftSpec] = useState<PublishedSpec | null>(null);
  const [loadingDraft, setLoadingDraft] = useState(false);
  const [addOpen, setAddOpen] = useState<AddKind | null>(null);
  const [editTarget, setEditTarget] = useState<SpecEntity | null>(null);
  const [publishing, setPublishing] = useState(false);
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({});

  // ── Published spec via Apollo ─────────────────────────────────────────────
  const publishedQ = useQuery<QueryResult>(PUBLISHED_SPEC, {
    fetchPolicy: "cache-and-network",
  });

  // ── Draft spec via REST ───────────────────────────────────────────────────
  const reloadDraft = useCallback(async () => {
    if (draftId === null) return;
    setLoadingDraft(true);
    try {
      const ds = await api.getDraftSpec(draftId);
      setDraftSpec(ds);
    } catch (e) {
      toast.error(`Failed to load draft: ${String(e)}`);
    } finally {
      setLoadingDraft(false);
    }
  }, [draftId]);

  useEffect(() => {
    if (draftId !== null) reloadDraft();
    else setDraftSpec(null);
  }, [draftId, reloadDraft]);

  // ── Active spec (published vs draft) ──────────────────────────────────────
  const spec: PublishedSpec | null = useMemo(() => {
    if (mode === "edit") return draftSpec;
    return publishedQ.data?.publishedSpec ?? null;
  }, [mode, draftSpec, publishedQ.data]);

  // ── Layout: re-run when the spec or mode changes ──────────────────────────
  useEffect(() => {
    let cancelled = false;
    if (!spec) {
      setNodes([]);
      setEdges([]);
      return;
    }
    const { nodes: ns, edges: es } = buildGraph(spec);
    // Inject onSelect into class-card data so slot rows / chips can dispatch
    // selection without bubbling through React Flow's node-click handler.
    const withHandlers = ns.map((n) =>
      n.data.entity.kind === "class"
        ? { ...n, data: { ...n.data, onSelect: setSelection } }
        : n,
    );
    // Preserve drag positions across refetches.
    const preserved = withHandlers.map((n) => {
      const saved = positionsRef.current[n.id];
      return saved ? { ...n, position: saved } : n;
    });
    const sizing = { width: 300, height: 220 };
    layoutGraph(preserved, es, sizing).then((laid) => {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [spec]);

  // ── React Flow change handlers ────────────────────────────────────────────
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => {
      const next = applyNodeChanges(changes, nds) as SpecNode[];
      for (const n of next) {
        positionsRef.current[n.id] = n.position;
      }
      return next;
    });
  }, []);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((es) => applyEdgeChanges(changes, es) as SpecEdge[]);
  }, []);

  // ── Selection ─────────────────────────────────────────────────────────────
  const onNodeClick = useCallback((_: unknown, node: Node) => {
    const data = node.data as SpecNodeData;
    const entity = data.entity;
    const name =
      entity.kind === "sourceBinding"
        ? `${entity.value.sourceName}__${entity.value.className}`
        : (entity.value as { name: string }).name;
    setSelection({ kind: entity.kind, name });
  }, []);
  const onPaneClick = useCallback(() => setSelection(null), []);

  // Resolve the current selection back to a typed entity for handlers that
  // need to act on it (delete, edit modal).
  const selectedEntity: SpecEntity | null =
    selection && spec ? resolveSelection(spec, selection) : null;

  // ── Toggle edit mode ──────────────────────────────────────────────────────
  const onToggleMode = useCallback(async () => {
    if (mode === "view") {
      const ok = confirm(
        "Create a new draft branched from the current published revision?",
      );
      if (!ok) return;
      try {
        const draft = await api.createDraft({});
        navigate(`/spec/draft/${draft.revision}`);
        toast.success(`Draft #${draft.revision} created`);
      } catch (e) {
        toast.error(`Failed to create draft: ${formatErr(e)}`);
      }
    } else {
      navigate("/spec-graph");
    }
  }, [mode, navigate]);

  // ── Add via form modal ────────────────────────────────────────────────────
  const handleAdd = useCallback(
    (kind: AddKind) => {
      if (draftId === null) {
        toast.error("Enter edit mode first.");
        return;
      }
      setAddOpen(kind);
    },
    [draftId],
  );

  // ── Publish / Discard ─────────────────────────────────────────────────────
  const onPublish = useCallback(async () => {
    if (draftId === null) return;
    setPublishing(true);
    try {
      const res = await api.publishDraft(draftId);
      toast.success(`Published revision ${res.revision}`);
      navigate("/spec-graph");
      await publishedQ.refetch();
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 400 && /destructive/i.test(String(err.body))) {
        const ok = confirm(
          "Publish requires destructive migration. Allow destructive and retry?",
        );
        if (ok) {
          try {
            const res = await api.publishDraft(draftId, { allowDestructive: true });
            toast.success(`Published revision ${res.revision} (destructive)`);
            navigate("/spec-graph");
            await publishedQ.refetch();
          } catch (e2) {
            toast.error(`Publish failed: ${formatErr(e2)}`);
          }
        }
      } else {
        toast.error(`Publish failed: ${formatErr(e)}`);
      }
    } finally {
      setPublishing(false);
    }
  }, [draftId, navigate, publishedQ]);

  const onDiscard = useCallback(async () => {
    if (draftId === null) return;
    const ok = confirm(`Discard draft #${draftId}? This cannot be undone.`);
    if (!ok) return;
    try {
      await api.discardDraft(draftId);
      toast.success("Draft discarded");
      navigate("/spec-graph");
    } catch (e) {
      toast.error(`Discard failed: ${formatErr(e)}`);
    }
  }, [draftId, navigate]);

  // ── Double-click to edit a node ───────────────────────────────────────────
  const onNodeDoubleClick = useCallback(
    (_: unknown, node: Node) => {
      if (mode !== "edit") return;
      const data = node.data as SpecNodeData;
      setEditTarget(data.entity);
    },
    [mode],
  );

  // ── Delete on Delete-key or right-click ───────────────────────────────────
  const deleteEntity = useCallback(
    async (entity: SpecEntity) => {
      if (draftId === null || !spec) return;
      const name =
        entity.kind === "sourceBinding"
          ? `${entity.value.sourceName}__${entity.value.className}`
          : (entity.value as { name: string }).name;
      const ok = confirm(`Delete ${entity.kind} "${name}"?`);
      if (!ok) return;
      const attempt = async () => {
        switch (entity.kind) {
          case "class":
            return api.deleteClass(draftId, name);
          case "source":
            return api.deleteSource(draftId, name);
          case "sourceBinding": {
            const b = entity.value as import("../types/spec").SpecSourceBinding;
            return api.deleteSourceBinding(draftId, b.sourceName, b.className);
          }
          case "constraint":
            return api.deleteConstraint(draftId, name);
        }
      };
      try {
        await attempt();
        toast.success(`Deleted ${entity.kind} ${name}`);
        await reloadDraft();
      } catch (e) {
        const err = e as ApiError;
        if (err.status === 409) {
          const ok2 = confirm(
            `Cannot delete ${entity.kind} "${name}" — it's referenced.\n\n${String(err.body)}\n\nAuto-detach references and retry?`,
          );
          if (!ok2) return;
          try {
            await autoDetach(
              draftId,
              spec,
              entity.kind as "class" | "source" | "constraint",
              name,
            );
            await attempt();
            toast.success(`Deleted ${entity.kind} ${name}`);
            await reloadDraft();
          } catch (e2) {
            toast.error(`Cascade delete failed: ${formatErr(e2)}`);
          }
        } else {
          toast.error(`Delete failed: ${formatErr(e)}`);
        }
      }
    },
    [draftId, spec, reloadDraft],
  );

  // ── Keyboard shortcuts ────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      const inField = tag === "input" || tag === "textarea" || tag === "select";
      if (e.key === "Escape" && !inField) setSelection(null);
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "e" && !inField) {
        e.preventDefault();
        onToggleMode();
      }
      if (e.key === "Delete" && selectedEntity && mode === "edit" && !inField) {
        e.preventDefault();
        deleteEntity(selectedEntity);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, selectedEntity, onToggleMode, deleteEntity]);

  // ── Drag-to-connect edges ─────────────────────────────────────────────────
  const onConnect = useCallback(
    async (conn: Connection) => {
      if (draftId === null || !spec) return;
      if (!conn.source || !conn.target) return;
      const src = nodes.find((n) => n.id === conn.source);
      const tgt = nodes.find((n) => n.id === conn.target);
      if (!src || !tgt) return;
      const srcE = (src.data as SpecNodeData).entity;
      const tgtE = (tgt.data as SpecNodeData).entity;
      try {
        await applyConnection(draftId, spec, srcE, tgtE);
        toast.success(`Connected ${srcE.kind} → ${tgtE.kind}`);
        await reloadDraft();
      } catch (e) {
        if (e instanceof InvalidConnectionError) {
          toast.error(e.message);
        } else {
          toast.error(`Connect failed: ${formatErr(e)}`);
        }
      }
    },
    [draftId, spec, nodes, reloadDraft],
  );

  // ── Form submit handlers ──────────────────────────────────────────────────
  const handleFormSubmit = useCallback(
    async (kind: SpecEntityKind, vals: any, editing: SpecEntity | null) => {
      if (draftId === null || !spec) return;
      const performAdd = async () => {
        switch (kind) {
          case "class": {
            const classVals = vals as ClassFormValues;
            return api.addClass(draftId, {
              name: classVals.name,
              slots: classVals.slots.map((r) => ({
                name: r.name,
                type_kind: r.typeKind || null,
                type_name: r.typeName || null,
                identifier: r.identifier,
                required: r.required,
              })),
              is_a_name: classVals.isAName || null,
              mixin_names: classVals.mixinNames,
              abstract: classVals.abstract,
              description: classVals.description || null,
              definition: classVals.definition || null,
            });
          }
          case "source":
            return api.addSource(draftId, {
              name: vals.name,
              description: vals.description || null,
            });
          case "sourceBinding":
            return api.addSourceBinding(draftId, {
              source_name: vals.source_name,
              class_name: vals.class_name,
              identifier_slot_name: vals.identifier_slot_name,
              trust_prior: [
                parseFloat(vals.trust_prior_alpha) || 1,
                parseFloat(vals.trust_prior_beta) || 1,
              ],
              required_slot_names: vals.required_slot_names
                ? vals.required_slot_names
                    .split(",")
                    .map((s: string) => s.trim())
                    .filter(Boolean)
                : [],
              description: vals.description || null,
              mappings: (vals.mappings ?? []).map((m: any) => ({
                slot_name: m.slot_name,
                source_field: m.source_field,
                null_semantics: m.null_semantics ?? "no_claim",
              })),
            });
          case "constraint":
            return api.addConstraint(draftId, {
              name: vals.name,
              primary_class_name: vals.primaryClassName,
              body: vals.body,
              severity: vals.severity,
              message: vals.message || null,
            });
        }
      };
      try {
        if (!editing) {
          await performAdd();
        } else if (kind === "class") {
          const classVals = vals as ClassFormValues;
          const editingName = (editing.value as { name: string }).name;
          // 1. If the class name changed, register a non-destructive rename
          //    first so subsequent PATCH/slot-rename calls address the new name.
          let currentClassName = editingName;
          if (classVals.name && classVals.name !== editingName) {
            await api.renameClass(draftId, editingName, classVals.name);
            currentClassName = classVals.name;
          }
          // 2. Apply any per-slot renames (originalName set + differs from name).
          //    Address each via the class's current (post-rename) name.
          for (const row of classVals.slots) {
            if (row.originalName && row.originalName !== row.name) {
              await api.renameSlot(draftId, currentClassName, row.originalName, row.name);
            }
          }
          // 3. PATCH the class with the final slot list.
          await api.updateClass(draftId, currentClassName, {
            slots: classVals.slots.map((r) => ({
              name: r.name,
              type_kind: r.typeKind || null,
              type_name: r.typeName || null,
              identifier: r.identifier,
              required: r.required,
            })),
            is_a_name: classVals.isAName || null,
            mixin_names: classVals.mixinNames,
            abstract: classVals.abstract,
            description: classVals.description || null,
          });
        } else if (kind !== "sourceBinding") {
          // sourceBinding edits are handled as delete+re-add at the API level;
          // for other named entities use the generic delete+add helper.
          const ok = confirm(
            `Editing a ${kind} via delete + add. References will be temporarily detached. Continue?`,
          );
          if (!ok) return;
          await editEntityViaDeleteAdd(
            draftId,
            spec,
            kind as "source" | "constraint",
            (editing.value as { name: string }).name,
            performAdd,
          );
        } else {
          // sourceBinding: delete the old one, add the new one.
          const b = editing.value as import("../types/spec").SpecSourceBinding;
          await api.deleteSourceBinding(draftId, b.sourceName, b.className);
          await performAdd();
        }
        toast.success(`Saved ${kind}`);
        setAddOpen(null);
        setEditTarget(null);
        await reloadDraft();
      } catch (e) {
        toast.error(`Save failed: ${formatErr(e)}`);
      }
    },
    [draftId, spec, reloadDraft],
  );

  // ── Loading / error ───────────────────────────────────────────────────────
  if (publishedQ.error && mode === "view") {
    return (
      <div className="p-6 text-rose-700">
        Failed to load spec: {String(publishedQ.error.message)}
      </div>
    );
  }
  const loading = mode === "view" ? publishedQ.loading && !spec : loadingDraft && !spec;

  return (
    <div className="flex flex-col h-full">
      <Toaster position="top-right" />
      <Toolbar
        mode={mode}
        draftRevision={draftId}
        onToggleMode={onToggleMode}
        onRefetch={() => (mode === "view" ? publishedQ.refetch() : reloadDraft())}
        onAdd={handleAdd}
        onPublish={onPublish}
        onDiscard={onDiscard}
        publishing={publishing}
      />
      <div className="flex-1 flex min-h-0">
        <div className="flex-1 relative">
          {loading && (
            <div className="absolute inset-0 flex items-center justify-center bg-white/60 z-10 text-slate-500 text-sm">
              Loading spec…
            </div>
          )}
          {!loading && !spec && (
            <div className="absolute inset-0 flex items-center justify-center text-slate-500 text-sm">
              No spec published yet. Switch to Edit mode to start a draft.
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
            onNodeDoubleClick={onNodeDoubleClick}
            onConnect={onConnect}
            fitView
          >
            <Background gap={20} />
            <Controls />
          </ReactFlow>
          <Legend />
        </div>
        <PropertyPanel
          selection={selection}
          spec={spec}
          onClose={() => setSelection(null)}
        />
      </div>

      {/* Add modals */}
      {addOpen && spec && (
        <Modal
          open
          onClose={() => setAddOpen(null)}
          title={`Add ${addOpen}`}
          widthClass="w-[560px]"
        >
          {renderForm(addOpen, spec, null, (vals) => handleFormSubmit(addOpen, vals, null), draftId)}
        </Modal>
      )}

      {/* Edit modal */}
      {editTarget && spec && (
        <Modal
          open
          onClose={() => setEditTarget(null)}
          title={`Edit ${editTarget.kind} "${editTarget.kind === "sourceBinding" ? `${editTarget.value.sourceName}__${editTarget.value.className}` : (editTarget.value as { name: string }).name}"`}
          widthClass="w-[560px]"
        >
          {renderForm(editTarget.kind, spec, editTarget, (vals) =>
            handleFormSubmit(editTarget.kind, vals, editTarget),
          draftId)}
        </Modal>
      )}
    </div>
  );
}

// ── Form dispatcher ─────────────────────────────────────────────────────────

function renderForm(
  kind: SpecEntityKind,
  spec: PublishedSpec,
  editing: SpecEntity | null,
  onSubmit: (vals: any) => Promise<void>,
  draftId: number | null = null,
) {
  switch (kind) {
    case "class": {
      const editingClass = editing?.kind === "class" ? editing.value : undefined;
      // Pre-populate inline slot rows from the existing class's own slots.
      const initialSlotRows: InlineSlotRow[] = editingClass?.slots.map((s) => ({
        name: s.name,
        typeKind: (s.typeKind ?? "primitive") as import("../components/forms/ClassForm").TypeKindValue,
        typeName: s.typeName ?? "",
        identifier: s.identifier,
        required: s.required,
        originalName: s.name,
      })) ?? [];
      return (
        <ClassForm
          spec={spec}
          initial={editingClass}
          initialSlotRows={initialSlotRows}
          lockName={!!editing}
          onSubmit={onSubmit}
        />
      );
    }
    case "source":
      return (
        <SourceForm
          initial={editing?.kind === "source" ? editing.value : undefined}
          lockName={!!editing}
          onSubmit={onSubmit}
        />
      );
    case "sourceBinding":
      return (
        <SourceBindingForm
          spec={spec}
          initial={editing?.kind === "sourceBinding" ? editing.value : undefined}
          lockName={!!editing}
          onSubmit={onSubmit}
        />
      );
    case "constraint":
      return (
        <ConstraintForm
          spec={spec}
          initial={editing?.kind === "constraint" ? editing.value : undefined}
          lockName={!!editing}
          onSubmit={onSubmit}
          draftId={draftId ?? undefined}
        />
      );
  }
}

// ── Drag-to-connect ─────────────────────────────────────────────────────────

class InvalidConnectionError extends Error {}

async function applyConnection(
  _draftId: number,
  _spec: PublishedSpec,
  src: SpecEntity,
  tgt: SpecEntity,
): Promise<void> {
  // Source → Class / Slot : Sources are now thin labels.
  // The (Source, Class) binding relationship lives on SourceBinding.
  // Use "+ Binding" in the toolbar to create a SourceBinding.
  if (src.kind === "source") {
    throw new InvalidConnectionError(
      "Sources are thin labels. Use '+ Binding' in the toolbar to create a SourceBinding for this source.",
    );
  }
  // Constraint → Class : reset primary via delete + add (constraint body is
  // preserved-as-empty because we don't have the original body in payload).
  if (src.kind === "constraint" && tgt.kind === "class") {
    throw new InvalidConnectionError(
      "Re-targeting a constraint's primary class is not supported via drag (body isn't available). Edit via double-click instead.",
    );
  }
  throw new InvalidConnectionError(
    `Invalid connection: ${src.kind} → ${tgt.kind}`,
  );
}

// ── Misc ────────────────────────────────────────────────────────────────────

function formatErr(e: unknown): string {
  if (e instanceof ApiError) {
    const body = typeof e.body === "string" ? e.body : JSON.stringify(e.body);
    return `${e.status} ${body}`;
  }
  return String((e as Error)?.message ?? e);
}

// Silence unused-symbol-when-noUnusedLocals warning.
void nodeId;
void normalizeDraftSpec;
