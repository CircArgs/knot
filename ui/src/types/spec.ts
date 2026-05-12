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
  slots: SpecSlot[];
  effectiveSlots: SpecSlot[];
}

export interface SpecSource {
  name: string;
  description: string | null;
}

export type SpecNullSemantics = "no_claim" | "asserted_absent";

export interface SpecSlotMapping {
  slotName: string;
  sourceField: string;
  default: unknown | null;
  nullSemantics: SpecNullSemantics;
  prior: [number, number] | null;
}

export interface SpecSourceBinding {
  sourceName: string;
  className: string;
  identifierSlotName: string;
  mappings: SpecSlotMapping[];
  trustPrior: [number, number];
  requiredSlotNames: string[];
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
  classes: SpecClass[];
  sources: SpecSource[];
  sourceBindings: SpecSourceBinding[];
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
  | { kind: "sourceBinding"; value: SpecSourceBinding }
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
