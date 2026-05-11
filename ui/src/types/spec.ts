/**
 * TypeScript shapes that mirror the GraphQL `PublishedSpec` payload (or the
 * REST `/spec/drafts/{id}` payload, normalised by `normalizeDraftSpec`).
 *
 * Keep these in sync with `knot.api.spec_graphql.PublishedSpec` and
 * `knot.api.spec` request/response shapes.
 */

export interface SpecType {
  name: string;
  base: string | null;
  pattern: string | null;
  description: string | null;
}

export type RangeKind = "type" | "class" | null;
export type ResolutionPolicy = "argmax_trust" | "posterior_mean" | "lcb";

export interface SpecSlot {
  name: string;
  identifier: boolean;
  required: boolean;
  multivalued: boolean;
  description: string | null;
  pattern: string | null;
  minimumValue: number | null;
  maximumValue: number | null;
  permissibleValues: string[];
  resolutionPolicy: ResolutionPolicy | string;
  rangeKind: RangeKind;
  rangeName: string | null;
}

export interface SpecClass {
  name: string;
  abstract: boolean;
  description: string | null;
  isAName: string | null;
  mixinNames: string[];
  slotNames: string[];
}

export interface SpecSource {
  name: string;
  entityClassName: string;
  identifierSlotName: string;
  description: string | null;
}

export type Severity = "error" | "warning";

export interface SpecConstraint {
  name: string;
  primaryClassName: string;
  severity: Severity | string;
  message: string | null;
}

export interface PublishedSpec {
  id: string;
  version: string;
  revision: number;
  contentHash: string;
  types: SpecType[];
  slots: SpecSlot[];
  classes: SpecClass[];
  sources: SpecSource[];
  constraints: SpecConstraint[];
}

/**
 * Convenience: discriminated union for "which entity does this node represent?"
 * Carried as React Flow `node.data.entity` so panels/forms can switch on `kind`.
 */
export type SpecEntity =
  | { kind: "type"; value: SpecType }
  | { kind: "slot"; value: SpecSlot }
  | { kind: "class"; value: SpecClass }
  | { kind: "source"; value: SpecSource }
  | { kind: "constraint"; value: SpecConstraint };

export type SpecEntityKind = SpecEntity["kind"];

/** Names of the six standard primitive types auto-seeded by the base spec. */
export const BUILTIN_TYPES = new Set([
  "string",
  "integer",
  "float",
  "boolean",
  "datetime",
  "date",
]);
