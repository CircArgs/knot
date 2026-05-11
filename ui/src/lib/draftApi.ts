/**
 * Thin fetch wrappers around the spec draft REST surface.
 *
 * All mutation endpoints require auth, but the local stack runs with
 * `KNOT_DEV_MODE=1` which bypasses the bearer check (see
 * `knot.api.auth.security.require_user`). For prod, the Apollo client would
 * be replaced with one that injects an Authorization header — out of scope here.
 *
 * Every helper throws an `ApiError` with the parsed JSON body on non-2xx so
 * callers can surface the cascade list from 409 responses verbatim.
 */

import type {
  PublishedSpec,
  RangeKind,
  Severity,
  SpecClass,
  SpecConstraint,
  SpecSlot,
  SpecSource,
  SpecType,
} from "../types/spec";

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `HTTP ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`);
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  url: string,
  init: RequestInit = {},
): Promise<T> {
  const r = await fetch(url, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init.headers ?? {}),
    },
  });
  const text = await r.text();
  const body: unknown = text ? safeJson(text) : null;
  if (!r.ok) throw new ApiError(r.status, body);
  return body as T;
}

function safeJson(s: string): unknown {
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

// ─── Draft lifecycle ────────────────────────────────────────────────────────

export interface DraftSummary {
  revision: number;
  label: string | null;
  parent_revision: number | null;
  content_hash: string;
  created_at: string;
}

export interface MutationResponse {
  draft_revision: number;
  content_hash: string;
  spec_summary: Record<string, number>;
}

export interface PublishResponse {
  revision: number;
  content_hash: string;
  published_at: string;
}

export async function listDrafts(): Promise<DraftSummary[]> {
  return request("/spec/drafts");
}

export async function createDraft(
  body: { parent_revision?: number | null; label?: string | null } = {},
): Promise<DraftSummary> {
  return request("/spec/drafts", {
    method: "POST",
    body: JSON.stringify({
      parent_revision: body.parent_revision ?? null,
      label: body.label ?? null,
    }),
  });
}

export async function discardDraft(id: number): Promise<void> {
  await request(`/spec/drafts/${id}`, { method: "DELETE" });
}

export async function publishDraft(
  id: number,
  opts: { allowDestructive?: boolean } = {},
): Promise<PublishResponse> {
  const q = opts.allowDestructive ? "?allow_destructive=true" : "";
  return request(`/spec/drafts/${id}/publish${q}`, { method: "POST" });
}

/**
 * Fetch the cycle-safe Spec dict for a draft (mirrors `/spec/published`
 * shape). The REST `spec_to_dict` payload uses `$uid`/`$ref` references for
 * cycles — we normalize it into the same flat shape as GraphQL `PublishedSpec`.
 */
export async function getDraftSpec(id: number): Promise<PublishedSpec> {
  const raw = await request<Record<string, any>>(`/spec/drafts/${id}`);
  return normalizeDraftSpec(raw, { revision: id });
}

/**
 * Convert the cycle-safe spec_to_dict payload to the flat GraphQL-shape used
 * by the UI. References are dereferenced via `$uid` -> first definition.
 *
 * The payload's slot ranges are inline `{$kind: TypeDefinition | OntologyClass}`
 * objects (or `$ref` placeholders); we flatten to `rangeKind` + `rangeName`.
 */
export function normalizeDraftSpec(
  raw: Record<string, any>,
  override: { revision?: number; contentHash?: string } = {},
): PublishedSpec {
  // Build uid -> name map across types/slots/classes for $ref resolution.
  const uidNames = new Map<number, string>();
  const walkForNames = (node: unknown) => {
    if (!node) return;
    if (Array.isArray(node)) {
      node.forEach(walkForNames);
      return;
    }
    if (typeof node !== "object") return;
    const obj = node as Record<string, any>;
    if (typeof obj.$uid === "number" && typeof obj.name === "string") {
      uidNames.set(obj.$uid, obj.name);
    }
    for (const v of Object.values(obj)) walkForNames(v);
  };
  walkForNames(raw);

  const resolveName = (node: unknown): string | null => {
    if (!node || typeof node !== "object") return null;
    const obj = node as Record<string, any>;
    if (typeof obj.$ref === "number") return uidNames.get(obj.$ref) ?? null;
    if (typeof obj.name === "string") return obj.name;
    return null;
  };

  const types: SpecType[] = (raw.types ?? []).map(
    (t: Record<string, any>): SpecType => ({
      name: t.name,
      base: t.base ?? null,
      pattern: t.pattern ?? null,
      description: t.description ?? null,
    }),
  );

  const slots: SpecSlot[] = (raw.slots ?? []).map(
    (s: Record<string, any>): SpecSlot => {
      let rangeKind: RangeKind = null;
      let rangeName: string | null = null;
      if (s.range) {
        const r = s.range as Record<string, any>;
        const refKind = r.$kind;
        if (refKind === "TypeDefinition") {
          rangeKind = "type";
        } else if (refKind === "OntologyClass") {
          rangeKind = "class";
        } else if (refKind === undefined && typeof r.$ref === "number") {
          // Pure $ref — kind not encoded; infer from name overlap.
          const n = uidNames.get(r.$ref);
          if (n) {
            rangeName = n;
            if (types.some((t) => t.name === n)) rangeKind = "type";
            else rangeKind = "class";
          }
        }
        if (rangeKind && rangeName == null) rangeName = resolveName(r);
      }
      const permVals = (s.permissible_values ?? []).map((pv: any) =>
        typeof pv === "string" ? pv : pv?.text ?? String(pv?.value ?? ""),
      );
      return {
        name: s.name,
        identifier: !!s.identifier,
        required: !!s.required,
        multivalued: !!s.multivalued,
        description: s.description ?? null,
        pattern: s.pattern ?? null,
        minimumValue: s.minimum_value ?? null,
        maximumValue: s.maximum_value ?? null,
        permissibleValues: permVals,
        resolutionPolicy: s.resolution_policy ?? "argmax_trust",
        rangeKind,
        rangeName,
      };
    },
  );

  const classes: SpecClass[] = (raw.classes ?? []).map(
    (c: Record<string, any>): SpecClass => ({
      name: c.name,
      abstract: !!c.abstract,
      description: c.description ?? null,
      isAName: resolveName(c.is_a),
      mixinNames: (c.mixins ?? []).map((m: any) => resolveName(m) ?? "").filter(Boolean),
      slotNames: (c.slots ?? []).map((s: any) => resolveName(s) ?? "").filter(Boolean),
    }),
  );

  const sources: SpecSource[] = (raw.sources ?? []).map(
    (src: Record<string, any>): SpecSource => ({
      name: src.name,
      entityClassName: resolveName(src.entity_class) ?? "",
      identifierSlotName: resolveName(src.identifier_slot) ?? "",
      description: src.description ?? null,
    }),
  );

  const constraints: SpecConstraint[] = (raw.constraints ?? []).map(
    (k: Record<string, any>): SpecConstraint => ({
      name: k.name,
      primaryClassName: resolveName(k.primary) ?? "",
      severity: k.severity ?? "ERROR",
      message: k.message ?? null,
    }),
  );

  return {
    id: raw.id ?? "",
    version: raw.version ?? "",
    revision: override.revision ?? 0,
    contentHash: override.contentHash ?? "",
    types,
    slots,
    classes,
    sources,
    constraints,
  };
}

// ─── Add / patch / delete ───────────────────────────────────────────────────

export interface TypeCreate {
  name: string;
  base?: string | null;
  pattern?: string | null;
  description?: string | null;
}
export interface SlotCreate {
  name: string;
  range_kind?: RangeKind;
  range_name?: string | null;
  identifier?: boolean;
  required?: boolean;
  multivalued?: boolean;
  resolution_policy?: string;
  pattern?: string | null;
  minimum_value?: number | null;
  maximum_value?: number | null;
  permissible_values?: string[] | null;
  description?: string | null;
  derivation?: unknown;
}
export interface ClassCreate {
  name: string;
  slot_names?: string[];
  is_a_name?: string | null;
  mixin_names?: string[];
  abstract?: boolean;
  description?: string | null;
  definition?: unknown;
}
export interface ClassUpdate {
  slot_names?: string[] | null;
  is_a_name?: string | null;
  mixin_names?: string[] | null;
  abstract?: boolean | null;
  description?: string | null;
}
export interface SourceCreate {
  name: string;
  entity_class_name: string;
  identifier_slot_name: string;
  description?: string | null;
}
export interface ConstraintCreate {
  name: string;
  primary_class_name: string;
  body: unknown;
  severity?: Severity;
  message?: string | null;
}

export const addType = (id: number, body: TypeCreate) =>
  request<MutationResponse>(`/spec/drafts/${id}/types`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const addSlot = (id: number, body: SlotCreate) =>
  request<MutationResponse>(`/spec/drafts/${id}/slots`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const addClass = (id: number, body: ClassCreate) =>
  request<MutationResponse>(`/spec/drafts/${id}/classes`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateClass = (id: number, name: string, body: ClassUpdate) =>
  request<MutationResponse>(`/spec/drafts/${id}/classes/${name}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const addSource = (id: number, body: SourceCreate) =>
  request<MutationResponse>(`/spec/drafts/${id}/sources`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const addConstraint = (id: number, body: ConstraintCreate) =>
  request<MutationResponse>(`/spec/drafts/${id}/constraints`, {
    method: "POST",
    body: JSON.stringify(body),
  });

export const deleteType = (id: number, name: string) =>
  request<MutationResponse>(`/spec/drafts/${id}/types/${name}`, { method: "DELETE" });

export const deleteSlot = (id: number, name: string) =>
  request<MutationResponse>(`/spec/drafts/${id}/slots/${name}`, { method: "DELETE" });

export const deleteClass = (id: number, name: string) =>
  request<MutationResponse>(`/spec/drafts/${id}/classes/${name}`, { method: "DELETE" });

export const deleteSource = (id: number, name: string) =>
  request<MutationResponse>(`/spec/drafts/${id}/sources/${name}`, { method: "DELETE" });

export const deleteConstraint = (id: number, name: string) =>
  request<MutationResponse>(`/spec/drafts/${id}/constraints/${name}`, { method: "DELETE" });

export type { SpecClass };
