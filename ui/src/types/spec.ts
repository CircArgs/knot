/**
 * TypeScript shapes that mirror the GraphQL `PublishedSpec` payload (or the
 * REST `/spec/drafts/{id}` payload, normalised by `normalizeDraftSpec`).
 *
 * Keep these in sync with `knot.api.spec_graphql.PublishedSpec` and
 * `knot.api.spec` request/response shapes.
 */

export type SlotTypeKind =
  | "primitive"
  | "class"
  | "array_of_primitive"
  | "array_of_class"
  | null;

export type ResolutionPolicy = "argmax_trust" | "posterior_mean" | "lcb";

export interface SpecSlot {
  name: string;
  identifier: boolean;
  required: boolean;
  description: string | null;
  pattern: string | null;
  minimumValue: number | null;
  maximumValue: number | null;
  permissibleValues: string[];
  resolutionPolicy: ResolutionPolicy | string;
  typeKind: SlotTypeKind;
  typeName: string | null;
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
  trustScore: number;
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
  | { kind: "slot"; value: SpecSlot }
  | { kind: "class"; value: SpecClass }
  | { kind: "source"; value: SpecSource }
  | { kind: "constraint"; value: SpecConstraint };

export type SpecEntityKind = SpecEntity["kind"];

/** Names of the six standard primitive types — language-level builtins, not spec data. */
export const BUILTIN_TYPES = new Set([
  "string",
  "integer",
  "float",
  "boolean",
  "datetime",
  "date",
]);

/**
 * Returns true when the slot's typeKind is one of the array variants.
 * Equivalent to the old `slot.multivalued`.
 */
export function isArrayKind(typeKind: SlotTypeKind): boolean {
  return typeKind === "array_of_primitive" || typeKind === "array_of_class";
}

/**
 * Returns true when the slot's typeKind targets a primitive type.
 * Equivalent to the old `slot.rangeKind === "type"`.
 */
export function isPrimitiveKind(typeKind: SlotTypeKind): boolean {
  return typeKind === "primitive" || typeKind === "array_of_primitive";
}

/**
 * Returns true when the slot's typeKind targets a class.
 * Equivalent to the old `slot.rangeKind === "class"`.
 */
export function isClassKind(typeKind: SlotTypeKind): boolean {
  return typeKind === "class" || typeKind === "array_of_class";
}
