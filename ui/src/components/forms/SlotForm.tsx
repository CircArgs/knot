import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecProperty } from "../../types/spec";
import { BUILTIN_TYPES } from "../../types/spec";
import {
  CheckboxField,
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  selectClass,
  textareaClass,
} from "./fields";

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const POLICIES = ["argmax_trust", "posterior_mean", "lcb"] as const;

/**
 * typeKind encodes both "primitive vs class" and "scalar vs array":
 *   ""                  — derived (no type expression)
 *   "primitive"         — scalar primitive  e.g. string, integer
 *   "array_of_primitive"— list of primitives
 *   "class"             — FK to a class (scalar)
 *   "array_of_class"    — FK list to a class
 */
const TYPE_KIND_OPTIONS = [
  { value: "", label: "(derived)" },
  { value: "primitive", label: "primitive (scalar)" },
  { value: "array_of_primitive", label: "primitive (array)" },
  { value: "class", label: "class ref (scalar)" },
  { value: "array_of_class", label: "class ref (array)" },
] as const;

type TypeKindValue = "" | "primitive" | "array_of_primitive" | "class" | "array_of_class";

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  typeKind: z.enum(["", "primitive", "array_of_primitive", "class", "array_of_class"]),
  typeName: z.string(),
  identifier: z.boolean(),
  required: z.boolean(),
  resolutionPolicy: z.enum(POLICIES),
  pattern: z.string(),
  minimumValue: z.string(),
  maximumValue: z.string(),
  permissibleValues: z.string(), // comma-separated
  description: z.string(),
  derivation: z.string(), // JSON
});

export type SlotFormValues = z.infer<typeof Schema>;

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecProperty>;
  lockName?: boolean;
  onSubmit: (vals: SlotFormValues) => Promise<void>;
}

function normalizePolicy(p: string | undefined): (typeof POLICIES)[number] | null {
  if (!p) return null;
  const lc = p.toLowerCase();
  return (POLICIES as readonly string[]).includes(lc)
    ? (lc as (typeof POLICIES)[number])
    : null;
}

/** Map an existing SpecProperty's typeKind (null means derived) to the form's string enum. */
function toFormTypeKind(typeKind: SpecProperty["typeKind"]): TypeKindValue {
  return typeKind ?? "";
}

export default function SlotForm({ spec, initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<SlotFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      typeKind: toFormTypeKind(initial?.typeKind ?? null),
      typeName: initial?.typeName ?? "",
      identifier: initial?.identifier ?? false,
      required: initial?.required ?? false,
      resolutionPolicy:
        normalizePolicy(initial?.resolutionPolicy) ?? "argmax_trust",
      pattern: initial?.pattern ?? "",
      minimumValue: initial?.minimumValue?.toString() ?? "",
      maximumValue: initial?.maximumValue?.toString() ?? "",
      permissibleValues: (initial?.permissibleValues ?? []).join(", "),
      description: initial?.description ?? "",
      derivation: "",
    },
  });

  const typeKind = watch("typeKind") as TypeKindValue;
  const isPrimitive = typeKind === "primitive" || typeKind === "array_of_primitive";
  const isClass = typeKind === "class" || typeKind === "array_of_class";
  const [identifier, required] = [watch("identifier"), watch("required")];
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // Dropdown options for typeName depend on typeKind.
  const typeNameOptions: string[] = isPrimitive
    ? Array.from(BUILTIN_TYPES)
    : isClass
      ? spec.classes.map((c) => c.name)
      : [];

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <FieldRow>
        <Label required>name</Label>
        <input
          {...register("name")}
          disabled={lockName}
          className={inputClass}
          placeholder="e.g. imdb_id"
        />
        <ErrText error={errors.name} />
      </FieldRow>

      <div className="grid grid-cols-2 gap-3">
        <FieldRow>
          <Label>type kind</Label>
          <select {...register("typeKind")} className={selectClass}>
            {TYPE_KIND_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </FieldRow>
        <FieldRow>
          <Label>type name</Label>
          <input
            list="type-name-options"
            {...register("typeName")}
            className={inputClass}
            disabled={!typeKind}
            placeholder={
              isPrimitive ? "e.g. string" : isClass ? "pick a class" : "—"
            }
          />
          <datalist id="type-name-options">
            {typeNameOptions.map((opt) => (
              <option key={opt} value={opt} />
            ))}
          </datalist>
        </FieldRow>
      </div>

      <FieldRow>
        <div className="flex gap-4 flex-wrap">
          <CheckboxField
            label="identifier"
            checked={identifier}
            onChange={(b) => setValue("identifier", b)}
          />
          <CheckboxField
            label="required"
            checked={required}
            onChange={(b) => setValue("required", b)}
          />
        </div>
      </FieldRow>

      <FieldRow>
        <Label>resolution policy</Label>
        <select {...register("resolutionPolicy")} className={selectClass}>
          {POLICIES.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
      </FieldRow>

      <div className="grid grid-cols-3 gap-3">
        <FieldRow>
          <Label>pattern</Label>
          <input {...register("pattern")} className={inputClass} />
        </FieldRow>
        <FieldRow>
          <Label>min</Label>
          <input {...register("minimumValue")} type="number" className={inputClass} />
        </FieldRow>
        <FieldRow>
          <Label>max</Label>
          <input {...register("maximumValue")} type="number" className={inputClass} />
        </FieldRow>
      </div>

      <FieldRow>
        <Label>permissible values (comma-separated)</Label>
        <input {...register("permissibleValues")} className={inputClass} />
      </FieldRow>

      <FieldRow>
        <Label>description</Label>
        <textarea {...register("description")} className={textareaClass} />
      </FieldRow>

      {/* ── Advanced: derivation ──────────────────────────────────────────── */}
      <div className="mb-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((b) => !b)}
          className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1"
        >
          <span>{advancedOpen ? "▾" : "▸"}</span>
          Advanced (JSON)
        </button>
        {advancedOpen && (
          <div className="mt-2">
            <Label>derivation (JSON)</Label>
            <textarea
              {...register("derivation")}
              className={textareaClass}
              placeholder='{"$kind": "FormatDerivation", ...}'
              rows={4}
            />
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save slot</Submit>
      </div>
    </form>
  );
}
