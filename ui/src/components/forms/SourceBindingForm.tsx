import { useMemo, useRef, useEffect } from "react";
import { useForm, useFieldArray } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecSourceBinding } from "../../types/spec";
import {
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  selectClass,
  textareaClass,
} from "./fields";

// ── Zod schema ───────────────────────────────────────────────────────────────

const SlotMappingSchema = z.object({
  property_name: z.string(),
  source_field: z.string(),
  null_semantics: z.enum(["no_claim", "asserted_absent"]),
  prior_alpha: z.string(),
  prior_beta: z.string(),
});

const Schema = z.object({
  source_name: z.string().min(1, "required"),
  class_name: z.string().min(1, "required"),
  identifier_property_name: z.string(),
  trust_prior_alpha: z.string(),
  trust_prior_beta: z.string(),
  required_property_names: z.string(),
  description: z.string(),
  mappings: z.array(SlotMappingSchema),
});

export type SourceBindingFormValues = z.infer<typeof Schema>;

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecSourceBinding>;
  lockName?: boolean;
  onSubmit: (vals: SourceBindingFormValues) => Promise<void>;
}

export default function SourceBindingForm({ spec, initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    watch,
    control,
    formState: { errors, isSubmitting },
  } = useForm<SourceBindingFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      source_name: initial?.sourceName ?? "",
      class_name: initial?.className ?? "",
      identifier_property_name: initial?.identifierSlotName ?? "",
      trust_prior_alpha: initial?.trustPrior ? String(initial.trustPrior[0]) : "1",
      trust_prior_beta:  initial?.trustPrior ? String(initial.trustPrior[1]) : "1",
      required_property_names: initial?.requiredSlotNames?.join(", ") ?? "",
      description: initial?.description ?? "",
      mappings: initial?.mappings?.map((m) => ({
        property_name: m.slotName,
        source_field: m.sourceField,
        null_semantics: m.nullSemantics,
        prior_alpha: "",
        prior_beta: "",
      })) ?? [],
    },
  });

  const { fields, append, remove, replace } = useFieldArray({ control, name: "mappings" });

  const className = watch("class_name");

  // Slots available on the selected class (effectiveProperties = own + inherited via is_a + mixins).
  const classSlots = useMemo(() => {
    if (!className) return [];
    const cls = spec.classes.find((c) => c.name === className);
    return cls?.effectiveProperties ?? [];
  }, [className, spec]);

  // Identifier slots (subset of class slots marked identifier=true).
  const idSlots = useMemo(
    () => classSlots.filter((s) => s.identifier),
    [classSlots],
  );

  // Concrete (non-abstract) classes only — bindings target concrete classes.
  const concreteClasses = useMemo(
    () => spec.classes.filter((c) => !c.abstract),
    [spec.classes],
  );

  const alpha = watch("trust_prior_alpha");
  const beta  = watch("trust_prior_beta");
  const a = parseFloat(alpha) || 1;
  const b = parseFloat(beta) || 1;
  const posteriorMean = Math.round((a / (a + b)) * 100);

  // ── Prepopulation logic ───────────────────────────────────────────────────
  //
  // When the user picks a class and the mappings array is currently empty,
  // we auto-populate one row per effectiveSlot. We only fire when mappings
  // are empty so we never overwrite rows the user has already edited.
  //
  // Rows that are prepopulated have their property_name locked (readonly label).
  // Manually-added rows (via "+ row") keep an editable slot select.
  //
  // We track which field IDs are locked in a Set stored in a ref so the
  // identity is stable across renders and doesn't need to be part of form
  // state.
  //
  const lockedFieldIds = useRef<Set<string>>(new Set());
  const prevClassNameRef = useRef<string>("");

  useEffect(() => {
    // Only act when the class actually changes.
    if (className === prevClassNameRef.current) return;
    prevClassNameRef.current = className;

    // Only prepopulate when the user has no existing mappings (empty array).
    // This preserves any rows they've already typed into.
    if (fields.length > 0) return;

    if (!className) return;
    const cls = spec.classes.find((c) => c.name === className);
    const properties = cls?.effectiveProperties ?? [];
    if (properties.length === 0) return;

    const newRows = properties.map((s) => ({
      property_name: s.name,
      source_field: "",
      null_semantics: "no_claim" as const,
      prior_alpha: "",
      prior_beta: "",
    }));

    // Replace clears existing rows and re-populates; since fields.length === 0
    // this is equivalent to a bulk append but avoids stale closure issues.
    replace(newRows);
    // We don't yet have the new field IDs here — they're assigned by
    // react-hook-form after the next render. We mark them in the render phase
    // below by comparing property_name against classSlots names.
    // We use a sentinel to flag "all current rows are locked" on next render.
    pendingLockAll.current = true;
  }, [className, fields.length, spec, replace]);

  // Sentinel: when true, the next render should lock all current field IDs.
  const pendingLockAll = useRef(false);

  if (pendingLockAll.current && fields.length > 0) {
    pendingLockAll.current = false;
    lockedFieldIds.current = new Set(fields.map((f) => f.id));
  }

  return (
    <form onSubmit={handleSubmit(onSubmit)} className="space-y-1">
      {/* Source */}
      <FieldRow>
        <Label required>source</Label>
        <select {...register("source_name")} disabled={lockName} className={selectClass}>
          <option value="">(pick a source)</option>
          {spec.sources.map((s) => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
        <ErrText error={errors.source_name} />
      </FieldRow>

      {/* Class (concrete only) */}
      <FieldRow>
        <Label required>class</Label>
        <select {...register("class_name")} disabled={lockName} className={selectClass}>
          <option value="">(pick a class)</option>
          {concreteClasses.map((c) => (
            <option key={c.name} value={c.name}>{c.name}</option>
          ))}
        </select>
        <ErrText error={errors.class_name} />
      </FieldRow>

      {/* Identifier slot */}
      <FieldRow>
        <Label required>identifier slot</Label>
        <select {...register("identifier_property_name")} className={selectClass}>
          <option value="">
            {className ? "(pick an identifier slot)" : "(pick a class first)"}
          </option>
          {idSlots.map((s) => (
            <option key={s.name} value={s.name}>{s.name}</option>
          ))}
        </select>
        {idSlots.length === 0 && className && (
          <p className="text-xs text-amber-700 mt-1">
            class has no identifier slots; mark a slot as identifier first.
          </p>
        )}
        <ErrText error={errors.identifier_property_name} />
      </FieldRow>

      {/* Trust prior */}
      <FieldRow>
        <Label>trust prior (α, β)</Label>
        <div className="flex gap-2 items-center">
          <input
            {...register("trust_prior_alpha")}
            type="number"
            min="0.01"
            step="0.1"
            className={`${inputClass} w-20`}
            placeholder="α"
          />
          <span className="text-slate-500 text-xs">,</span>
          <input
            {...register("trust_prior_beta")}
            type="number"
            min="0.01"
            step="0.1"
            className={`${inputClass} w-20`}
            placeholder="β"
          />
          <span className="text-xs text-slate-500">
            → posterior mean ≈ {posteriorMean}%
          </span>
        </div>
      </FieldRow>

      {/* Required slots */}
      <FieldRow>
        <Label>required slots</Label>
        <input
          {...register("required_property_names")}
          className={inputClass}
          placeholder="comma-separated slot names"
        />
        <p className="text-xs text-slate-500 mt-1">
          Rows missing any required slot are rejected with 422.
        </p>
      </FieldRow>

      {/* Description */}
      <FieldRow>
        <Label>description</Label>
        <textarea {...register("description")} className={textareaClass} rows={2} />
      </FieldRow>

      {/* Mappings table */}
      <div className="mt-3">
        <div className="flex items-center justify-between mb-1">
          <Label>field mappings</Label>
          <button
            type="button"
            onClick={() => append({ property_name: "", source_field: "", null_semantics: "no_claim", prior_alpha: "", prior_beta: "" })}
            className="text-xs px-2 py-0.5 rounded border border-slate-300 bg-white hover:bg-slate-100"
          >
            + row
          </button>
        </div>
        {fields.length === 0 && (
          <p className="text-xs text-slate-400 mb-2">
            {className
              ? "No mappings — pick a class to auto-populate properties, or add rows manually."
              : "No mappings — slots will be matched by name from the source payload."}
          </p>
        )}
        {fields.map((field, idx) => {
          const isLocked = lockedFieldIds.current.has(field.id);
          return (
            <div key={field.id} className="grid grid-cols-[1fr_1fr_auto_auto] gap-1.5 mb-1.5 items-start">
              {/* Slot name — readonly label for prepopulated rows, editable select for manual rows */}
              <div>
                {isLocked ? (
                  <div className={`${inputClass} bg-slate-50 text-slate-600 flex items-center`}>
                    <span className="font-mono text-xs">{field.property_name}</span>
                    {/* Register as a read-only text input so RHF reads the value correctly */}
                    <input
                      type="text"
                      className="sr-only"
                      readOnly
                      {...register(`mappings.${idx}.property_name`)}
                      value={field.property_name}
                    />
                  </div>
                ) : (
                  <select {...register(`mappings.${idx}.property_name`)} className={selectClass}>
                    <option value="">(slot)</option>
                    {classSlots.map((s) => (
                      <option key={s.name} value={s.name}>{s.name}</option>
                    ))}
                  </select>
                )}
              </div>
              <div>
                <input
                  {...register(`mappings.${idx}.source_field`)}
                  className={inputClass}
                  placeholder="field name in source's row payload"
                />
              </div>
              <div>
                <select {...register(`mappings.${idx}.null_semantics`)} className={selectClass}>
                  <option value="no_claim">no_claim</option>
                  <option value="asserted_absent">absent</option>
                </select>
              </div>
              <button
                type="button"
                onClick={() => {
                  lockedFieldIds.current.delete(field.id);
                  remove(idx);
                }}
                className="text-rose-500 hover:text-rose-700 text-sm px-1 mt-1"
                title="Remove mapping"
              >
                ✕
              </button>
            </div>
          );
        })}
      </div>

      <div className="flex justify-end pt-2">
        <Submit busy={isSubmitting}>Save binding</Submit>
      </div>
    </form>
  );
}
