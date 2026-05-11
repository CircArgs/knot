/**
 * Thin fetch wrappers around the data-plane REST surface (knot core).
 *
 *   GET  /graph/classes/{class}                    — list rows (per-source)
 *   GET  /graph/classes/{class}/{cid}              — contributions for one cid
 *   GET  /graph/classes/{class}/{cid}/resolved     — trust-resolved view
 *   POST /graph/corrections                        — submit typed correction
 *
 * Row shape (universally): every per-source contribution row carries the
 * housekeeping columns `_canonical_id`, `_source`, `_source_row_id`,
 * `_spec_revision`, plus one column per stored slot of the class.
 *
 * Mirrors ``draftApi`` in style: ``request<T>`` returns parsed JSON or throws
 * ``ApiError`` with the parsed body for the caller to surface.
 */
import { ApiError } from "./draftApi";

// ─── Universal row shape ────────────────────────────────────────────────────

/** Universal housekeeping columns on every data-plane row. */
export interface RowKeys {
  _canonical_id: string;
  _source: string;
  _source_row_id: string;
  _spec_revision: number;
}

/**
 * One per-source contribution row. The class's stored-slot columns live next
 * to the housekeeping keys. Values are whatever the data plane stores —
 * scalars, lists for multivalued slots, ISO strings for dates, etc.
 */
export type ContributionRow = RowKeys & Record<string, unknown>;

/** The trust-resolved view is just `{slot: value}` (no housekeeping cols). */
export type ResolvedRecord = Record<string, unknown>;

// ─── Read endpoints ─────────────────────────────────────────────────────────

export interface ListResponse {
  entity_class: string;
  rows: ContributionRow[];
  total: number;
  limit: number;
  offset: number;
  as_of: number | null;
}

export interface EntityResponse {
  entity_class: string;
  canonical_id: string;
  contributions: ContributionRow[];
  as_of: number | null;
}

export interface ResolvedEntityResponse {
  entity_class: string;
  canonical_id: string;
  resolved: ResolvedRecord;
  as_of: number | null;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
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

export async function listClassRows(
  className: string,
  opts: {
    limit?: number;
    offset?: number;
    asOf?: number | null;
    includeTombstoned?: boolean;
  } = {},
): Promise<ListResponse> {
  const params = new URLSearchParams();
  if (opts.limit !== undefined) params.set("limit", String(opts.limit));
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts.asOf !== undefined && opts.asOf !== null) {
    params.set("as_of", String(opts.asOf));
  }
  if (opts.includeTombstoned) params.set("include_tombstoned", "true");
  const qs = params.toString();
  return request(`/graph/classes/${encodeURIComponent(className)}${qs ? `?${qs}` : ""}`);
}

export async function getEntity(
  className: string,
  canonicalId: string,
  opts: { asOf?: number | null; includeTombstoned?: boolean } = {},
): Promise<EntityResponse> {
  const params = new URLSearchParams();
  if (opts.asOf !== undefined && opts.asOf !== null) {
    params.set("as_of", String(opts.asOf));
  }
  if (opts.includeTombstoned) params.set("include_tombstoned", "true");
  const qs = params.toString();
  return request(
    `/graph/classes/${encodeURIComponent(className)}/${encodeURIComponent(canonicalId)}${qs ? `?${qs}` : ""}`,
  );
}

export async function getResolvedEntity(
  className: string,
  canonicalId: string,
  opts: { asOf?: number | null } = {},
): Promise<ResolvedEntityResponse> {
  const params = new URLSearchParams();
  if (opts.asOf !== undefined && opts.asOf !== null) {
    params.set("as_of", String(opts.asOf));
  }
  const qs = params.toString();
  return request(
    `/graph/classes/${encodeURIComponent(className)}/${encodeURIComponent(canonicalId)}/resolved${qs ? `?${qs}` : ""}`,
  );
}

// ─── Correction submission ──────────────────────────────────────────────────

export type CorrectionBody =
  | PropertyCorrectionBody
  | MergeBody
  | SplitBody
  | AddBody
  | TombstoneBody
  | RejectContributionBody;

export interface PropertyCorrectionBody {
  type: "property";
  class_name: string;
  canonical_id: string;
  slot: string;
  value: unknown;
}

export interface MergeBody {
  type: "merge";
  class_name: string;
  keep_canonical_id: string;
  merge_canonical_ids: string[];
}

export interface SplitBody {
  type: "split";
  class_name: string;
  source_canonical_id: string;
  /** new_canonical_id -> [(source, source_row_id), ...] */
  partitions: Record<string, [string, string][]>;
}

export interface AddBody {
  type: "add";
  class_name: string;
  new_canonical_id: string;
  values: Record<string, unknown>;
}

export interface TombstoneBody {
  type: "tombstone";
  class_name: string;
  canonical_id: string;
  reason?: string | null;
}

export interface RejectContributionBody {
  type: "reject_contribution";
  class_name: string;
  canonical_id: string;
  source: string;
}

export interface CorrectionResponse {
  id: number;
  correction_type: string;
  applied_revision: number;
}

export async function submitCorrection(
  body: CorrectionBody,
): Promise<CorrectionResponse> {
  return request("/graph/corrections", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// ─── Grouping helpers ───────────────────────────────────────────────────────

/**
 * Group a list of contribution rows by `_canonical_id`. Preserves the row
 * order within each group (which is `_source` ASC out of the API).
 */
export function groupByCanonicalId(
  rows: ContributionRow[],
): Map<string, ContributionRow[]> {
  const byId = new Map<string, ContributionRow[]>();
  for (const row of rows) {
    const cid = String(row._canonical_id);
    const bucket = byId.get(cid) ?? [];
    bucket.push(row);
    byId.set(cid, bucket);
  }
  return byId;
}
