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
import ConstraintNode from "../components/nodes/ConstraintNode";
import Legend from "../components/Legend";
import Modal from "../components/Modal";
import PropertyPanel, {
  resolveSelection,
  type SpecSelection,
} from "../components/PropertyPanel";
import SlotNode from "../components/nodes/SlotNode";
import SourceNode from "../components/nodes/SourceNode";
import Toolbar, { type AddKind } from "../components/Toolbar";
import TypeNode from "../components/nodes/TypeNode";
import ClassForm from "../components/forms/ClassForm";
import ConstraintForm from "../components/forms/ConstraintForm";
import SlotForm from "../components/forms/SlotForm";
import SourceForm from "../components/forms/SourceForm";
import TypeForm from "../components/forms/TypeForm";
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
import { useLocalStorage } from "../lib/useLocalStorage";
import type { PublishedSpec, SpecEntity, SpecEntityKind } from "../types/spec";

const NODE_TYPES = {
  specType: TypeNode,
  specSlot: SlotNode,
  specClass: ClassNode,
  specSource: SourceNode,
  specConstraint: ConstraintNode,
};

interface QueryResult {
  publishedSpec: PublishedSpec | null;
}

export default function SpecGraph() {
  const navigate = useNavigate();
  const { draftId: draftIdParam } = useParams<{ draftId?: string }>();
  const draftId = draftIdParam ? Number(draftIdParam) : null;
  const mode: "view" | "edit" = draftId !== null ? "edit" : "view";

  const [showOntologyDetails, setShowOntologyDetails] = useLocalStorage(
    "knot:show-ontology-details",
    false,
  );
  const [includeBuiltins, setIncludeBuiltins] = useState(true);
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
    const { nodes: ns, edges: es } = buildGraph(spec, {
      includeBuiltinTypes: includeBuiltins,
      showOntologyDetails,
    });
    // Inject onSelect into class-card data so slot rows / chips can dispatch
    // selection without bubbling through React Flow's node-click handler.
    const withHandlers = ns.map((n) =>
      n.data.entity.kind === "class" && !showOntologyDetails
        ? { ...n, data: { ...n.data, onSelect: setSelection } }
        : n,
    );
    // Preserve drag positions across refetches.
    const preserved = withHandlers.map((n) => {
      const saved = positionsRef.current[n.id];
      return saved ? { ...n, position: saved } : n;
    });
    // Class cards are wider + taller than the old generic nodes; use bigger
    // sizing inputs so elk leaves enough room between layers.
    const sizing = showOntologyDetails
      ? { width: 220, height: 110 }
      : { width: 300, height: 220 };
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
  }, [spec, includeBuiltins, showOntologyDetails]);

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
    setSelection({ kind: data.entity.kind, name: data.entity.value.name });
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
      const name = entity.value.name;
      const ok = confirm(`Delete ${entity.kind} "${name}"?`);
      if (!ok) return;
      const attempt = async () => {
        switch (entity.kind) {
          case "type":
            return api.deleteType(draftId, name);
          case "slot":
            return api.deleteSlot(draftId, name);
          case "class":
            return api.deleteClass(draftId, name);
          case "source":
            return api.deleteSource(draftId, name);
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
              entity.kind as "class" | "slot" | "type" | "source" | "constraint",
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
          case "type":
            return api.addType(draftId, {
              name: vals.name,
              base: vals.base || null,
              pattern: vals.pattern || null,
              description: vals.description || null,
            });
          case "slot":
            return api.addSlot(draftId, {
              name: vals.name,
              range_kind: vals.rangeKind || null,
              range_name: vals.rangeName || null,
              identifier: vals.identifier,
              required: vals.required,
              multivalued: vals.multivalued,
              resolution_policy: vals.resolutionPolicy,
              pattern: vals.pattern || null,
              minimum_value: vals.minimumValue ? Number(vals.minimumValue) : null,
              maximum_value: vals.maximumValue ? Number(vals.maximumValue) : null,
              permissible_values: vals.permissibleValues
                ? vals.permissibleValues
                    .split(",")
                    .map((s: string) => s.trim())
                    .filter(Boolean)
                : null,
              description: vals.description || null,
              derivation: vals.derivation ? JSON.parse(vals.derivation) : null,
            });
          case "class":
            return api.addClass(draftId, {
              name: vals.name,
              slot_names: vals.slotNames,
              is_a_name: vals.isAName || null,
              mixin_names: vals.mixinNames,
              abstract: vals.abstract,
              description: vals.description || null,
              definition: vals.definition ? JSON.parse(vals.definition) : null,
            });
          case "source":
            return api.addSource(draftId, {
              name: vals.name,
              entity_class_name: vals.entityClassName,
              identifier_slot_name: vals.identifierSlotName,
              description: vals.description || null,
            });
          case "constraint":
            return api.addConstraint(draftId, {
              name: vals.name,
              primary_class_name: vals.primaryClassName,
              body: JSON.parse(vals.body),
              severity: vals.severity,
              message: vals.message || null,
            });
        }
      };
      try {
        if (!editing) {
          await performAdd();
        } else if (kind === "class") {
          // The only PATCH-able entity.
          await api.updateClass(draftId, editing.value.name, {
            slot_names: vals.slotNames,
            is_a_name: vals.isAName || null,
            mixin_names: vals.mixinNames,
            abstract: vals.abstract,
            description: vals.description || null,
          });
        } else {
          const ok = confirm(
            `Editing a ${kind} via delete + add. References will be temporarily detached. Continue?`,
          );
          if (!ok) return;
          await editEntityViaDeleteAdd(
            draftId,
            spec,
            kind as "type" | "slot" | "source" | "constraint",
            editing.value.name,
            performAdd,
          );
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
        showOntologyDetails={showOntologyDetails}
        onToggleOntologyDetails={() => setShowOntologyDetails((b) => !b)}
        includeBuiltins={includeBuiltins}
        onToggleBuiltins={() => setIncludeBuiltins((b) => !b)}
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
          <Legend mode={showOntologyDetails ? "details" : "class-card"} />
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
          {renderForm(addOpen, spec, null, (vals) => handleFormSubmit(addOpen, vals, null))}
        </Modal>
      )}

      {/* Edit modal */}
      {editTarget && spec && (
        <Modal
          open
          onClose={() => setEditTarget(null)}
          title={`Edit ${editTarget.kind} "${editTarget.value.name}"`}
          widthClass="w-[560px]"
        >
          {renderForm(editTarget.kind, spec, editTarget, (vals) =>
            handleFormSubmit(editTarget.kind, vals, editTarget),
          )}
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
) {
  switch (kind) {
    case "type":
      return (
        <TypeForm
          initial={editing?.kind === "type" ? editing.value : undefined}
          lockName={!!editing}
          onSubmit={onSubmit}
        />
      );
    case "slot":
      return (
        <SlotForm
          spec={spec}
          initial={editing?.kind === "slot" ? editing.value : undefined}
          lockName={!!editing}
          onSubmit={onSubmit}
        />
      );
    case "class":
      return (
        <ClassForm
          spec={spec}
          initial={editing?.kind === "class" ? editing.value : undefined}
          lockName={!!editing}
          onSubmit={onSubmit}
        />
      );
    case "source":
      return (
        <SourceForm
          spec={spec}
          initial={editing?.kind === "source" ? editing.value : undefined}
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
        />
      );
  }
}

// ── Drag-to-connect ─────────────────────────────────────────────────────────

class InvalidConnectionError extends Error {}

async function applyConnection(
  draftId: number,
  spec: PublishedSpec,
  src: SpecEntity,
  tgt: SpecEntity,
): Promise<void> {
  // Slot → Class : add slot to class slot_names.
  if (src.kind === "slot" && tgt.kind === "class") {
    const cls = tgt.value;
    if (cls.slotNames.includes(src.value.name)) {
      throw new InvalidConnectionError(
        `${cls.name} already has slot ${src.value.name}`,
      );
    }
    await api.updateClass(draftId, cls.name, {
      slot_names: [...cls.slotNames, src.value.name],
    });
    return;
  }
  // Slot → Type / Class (range): delete + add slot with new range.
  if (
    src.kind === "slot" &&
    (tgt.kind === "type" || tgt.kind === "class")
  ) {
    const slot = src.value;
    await editEntityViaDeleteAdd(draftId, spec, "slot", slot.name, async () => {
      await api.addSlot(draftId, {
        name: slot.name,
        range_kind: tgt.kind,
        range_name: tgt.value.name,
        identifier: slot.identifier,
        required: slot.required,
        multivalued: slot.multivalued,
        resolution_policy: String(slot.resolutionPolicy),
        pattern: slot.pattern,
        minimum_value: slot.minimumValue,
        maximum_value: slot.maximumValue,
        permissible_values: slot.permissibleValues.length ? slot.permissibleValues : null,
        description: slot.description,
        derivation: null,
      });
    });
    return;
  }
  // Source → Class : reset source entity_class via delete + add.
  if (src.kind === "source" && tgt.kind === "class") {
    const oldSrc = src.value;
    await api.deleteSource(draftId, oldSrc.name);
    await api.addSource(draftId, {
      name: oldSrc.name,
      entity_class_name: tgt.value.name,
      identifier_slot_name: oldSrc.identifierSlotName,
      description: oldSrc.description,
    });
    return;
  }
  // Source → Slot : reset identifier_slot via delete + add.
  if (src.kind === "source" && tgt.kind === "slot") {
    const oldSrc = src.value;
    await api.deleteSource(draftId, oldSrc.name);
    await api.addSource(draftId, {
      name: oldSrc.name,
      entity_class_name: oldSrc.entityClassName,
      identifier_slot_name: tgt.value.name,
      description: oldSrc.description,
    });
    return;
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
