import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecClass } from "../../types/spec";
import { BUILTIN_TYPES } from "../../types/spec";
import {
  CheckboxField,
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  textareaClass,
} from "./fields";
import SearchableSelect from "./SearchableSelect";

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

// ── Inline slot row types ────────────────────────────────────────────────────

export type InlineSlotMode = "new" | "existing";

export type TypeKindValue = "" | "primitive" | "array_of_primitive" | "class" | "array_of_class";

export interface InlineSlotRow {
  mode: InlineSlotMode;
  // "new" fields
  name: string;
  typeKind: TypeKindValue;
  typeName: string;
  identifier: boolean;
  required: boolean;
  // "existing" field
  existingName: string;
}

function emptySlotRow(): InlineSlotRow {
  return {
    mode: "new",
    name: "",
    typeKind: "primitive",
    typeName: "string",
    identifier: false,
    required: false,
    existingName: "",
  };
}

// ── Main form schema (class-level fields only; slots submitted separately) ───

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  isAName: z.string(),
  mixinNames: z.array(z.string()),
  abstract: z.boolean(),
  description: z.string(),
  definition: z.string(),
});

export type ClassFormValues = z.infer<typeof Schema> & {
  /** Resolved slot name list (new + existing). Populated by the form's submit. */
  slotNames: string[];
  /** New slot rows that need to be created via POST /slots before the class. */
  newSlots: InlineSlotRow[];
};

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecClass>;
  lockName?: boolean;
  /** initialSlotRows is pre-populated when editing an existing class. */
  initialSlotRows?: InlineSlotRow[];
  onSubmit: (vals: ClassFormValues) => Promise<void>;
}

const TYPE_KIND_OPTIONS: { value: TypeKindValue; label: string }[] = [
  { value: "primitive", label: "primitive" },
  { value: "array_of_primitive", label: "primitive[]" },
  { value: "class", label: "class ref" },
  { value: "array_of_class", label: "class ref[]" },
  { value: "", label: "(derived)" },
];

export default function ClassForm({ spec, initial, lockName, initialSlotRows, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      isAName: initial?.isAName ?? "",
      mixinNames: initial?.mixinNames ?? [],
      abstract: initial?.abstract ?? false,
      description: initial?.description ?? "",
      definition: "",
    },
  });

  const mixinNames = watch("mixinNames");
  const abstract = watch("abstract");
  const isAName = watch("isAName");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  // ── Inline slot rows state ────────────────────────────────────────────────
  const [slotRows, setSlotRows] = useState<InlineSlotRow[]>(() => {
    if (initialSlotRows && initialSlotRows.length > 0) return initialSlotRows;
    if (initial?.slotNames && initial.slotNames.length > 0) {
      return initial.slotNames.map((n) => ({
        mode: "existing" as InlineSlotMode,
        name: "",
        typeKind: "primitive" as TypeKindValue,
        typeName: "",
        identifier: false,
        required: false,
        existingName: n,
      }));
    }
    return [];
  });

  const addSlotRow = () => setSlotRows((rs) => [...rs, emptySlotRow()]);
  const removeSlotRow = (idx: number) =>
    setSlotRows((rs) => rs.filter((_, i) => i !== idx));
  const updateSlotRow = (idx: number, patch: Partial<InlineSlotRow>) =>
    setSlotRows((rs) => rs.map((r, i) => (i === idx ? { ...r, ...patch } : r)));

  const otherClasses = spec.classes.filter((c) => c.name !== initial?.name);
  const classOptions = otherClasses.map((c) => ({ value: c.name, label: c.name }));
  const existingSlotOptions = spec.slots.map((s) => ({ value: s.name, label: s.name }));

  const toggleMixin = (name: string) => {
    const cur = mixinNames as string[];
    setValue(
      "mixinNames",
      cur.includes(name) ? cur.filter((n) => n !== name) : [...cur, name],
    );
  };

  const submit = handleSubmit(async (base) => {
    // Build resolved slot name list and new slot list from inline rows.
    const slotNames: string[] = [];
    const newSlots: InlineSlotRow[] = [];
    for (const row of slotRows) {
      if (row.mode === "existing") {
        if (row.existingName) slotNames.push(row.existingName);
      } else {
        if (row.name) {
          slotNames.push(row.name);
          newSlots.push(row);
        }
      }
    }
    await onSubmit({ ...base, slotNames, newSlots });
  });

  return (
    <form onSubmit={submit}>
      <FieldRow>
        <Label required>name</Label>
        <input
          {...register("name")}
          disabled={lockName}
          className={inputClass}
          placeholder="e.g. Movie"
        />
        <ErrText error={errors.name} />
      </FieldRow>

      <FieldRow>
        <Label>abstract</Label>
        <CheckboxField
          label="abstract class"
          checked={abstract}
          onChange={(b) => setValue("abstract", b)}
        />
      </FieldRow>

      <FieldRow>
        <Label>is_a (parent class)</Label>
        <SearchableSelect
          options={[{ value: "", label: "(none)" }, ...classOptions]}
          value={isAName}
          onChange={(v) => setValue("isAName", v)}
          placeholder="Search classes…"
        />
      </FieldRow>

      <FieldRow>
        <Label>mixins</Label>
        {otherClasses.length === 0 ? (
          <p className="text-xs text-slate-500 italic">no other classes</p>
        ) : (
          <div className="border border-slate-200 rounded p-2 max-h-28 overflow-y-auto grid grid-cols-2 gap-1">
            {otherClasses.map((c) => (
              <label
                key={c.name}
                className="flex items-center gap-1.5 text-xs text-slate-700 font-mono"
              >
                <input
                  type="checkbox"
                  checked={(mixinNames as string[]).includes(c.name)}
                  onChange={() => toggleMixin(c.name)}
                />
                {c.name}
              </label>
            ))}
          </div>
        )}
      </FieldRow>

      {/* ── Inline slot editor ─────────────────────────────────────────────── */}
      <FieldRow>
        <div className="flex items-center justify-between mb-1">
          <Label>slots</Label>
          <button
            type="button"
            onClick={addSlotRow}
            className="text-xs px-2 py-0.5 rounded border border-slate-300 bg-white hover:bg-slate-100"
          >
            + Add slot
          </button>
        </div>
        {slotRows.length === 0 && (
          <p className="text-xs text-slate-400 italic">
            No slots — click "+ Add slot" to define inline.
          </p>
        )}
        {slotRows.map((row, idx) => (
          <InlineSlotEditor
            key={idx}
            row={row}
            idx={idx}
            classOptions={classOptions}
            existingSlotOptions={existingSlotOptions}
            onChange={(patch) => updateSlotRow(idx, patch)}
            onRemove={() => removeSlotRow(idx)}
          />
        ))}
      </FieldRow>

      <FieldRow>
        <Label>description</Label>
        <textarea {...register("description")} className={textareaClass} rows={2} />
      </FieldRow>

      {/* ── Advanced disclosure ───────────────────────────────────────────── */}
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
            <Label>definition (JSON)</Label>
            <textarea
              {...register("definition")}
              className={textareaClass}
              placeholder='{"$kind": "BoolExpr", ...}'
              rows={4}
            />
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save class</Submit>
      </div>
    </form>
  );
}

// ── Inline slot editor row ────────────────────────────────────────────────────

function InlineSlotEditor({
  row,
  idx,
  classOptions,
  existingSlotOptions,
  onChange,
  onRemove,
}: {
  row: InlineSlotRow;
  idx: number;
  classOptions: { value: string; label: string }[];
  existingSlotOptions: { value: string; label: string }[];
  onChange: (patch: Partial<InlineSlotRow>) => void;
  onRemove: () => void;
}) {
  const isClassRange = row.typeKind === "class" || row.typeKind === "array_of_class";
  const isPrimRange = row.typeKind === "primitive" || row.typeKind === "array_of_primitive";

  const typeNameOptions: { value: string; label: string }[] = isClassRange
    ? classOptions
    : isPrimRange
      ? Array.from(BUILTIN_TYPES).map((t) => ({ value: t, label: t }))
      : [];

  return (
    <div className="mb-2 p-2 border border-slate-200 rounded bg-slate-50/60 relative">
      {/* Mode toggle */}
      <div className="flex items-center justify-between mb-1.5 gap-2">
        <span className="text-[10px] uppercase tracking-wide text-slate-400 font-medium">
          slot {idx + 1}
        </span>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() =>
              onChange({ mode: row.mode === "new" ? "existing" : "new" })
            }
            className="text-[10px] text-blue-600 hover:text-blue-800 underline"
          >
            {row.mode === "new" ? "use existing" : "define new"}
          </button>
          <button
            type="button"
            onClick={onRemove}
            className="text-rose-400 hover:text-rose-600 text-sm leading-none"
            title="Remove slot"
          >
            ✕
          </button>
        </div>
      </div>

      {row.mode === "existing" ? (
        <SearchableSelect
          options={existingSlotOptions}
          value={row.existingName}
          onChange={(v) => onChange({ existingName: v })}
          placeholder="Search existing slots…"
        />
      ) : (
        <div className="grid grid-cols-[1fr_auto_1fr] gap-1.5 items-end">
          {/* name */}
          <div>
            <label className="block text-[10px] uppercase text-slate-500 mb-0.5">name</label>
            <input
              type="text"
              value={row.name}
              onChange={(e) => onChange({ name: e.target.value })}
              className={inputClass}
              placeholder="slot_name"
            />
          </div>

          {/* typeKind */}
          <div>
            <label className="block text-[10px] uppercase text-slate-500 mb-0.5">kind</label>
            <select
              value={row.typeKind}
              onChange={(e) =>
                onChange({
                  typeKind: e.target.value as TypeKindValue,
                  typeName: "",
                })
              }
              className={inputClass + " bg-white"}
            >
              {TYPE_KIND_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </div>

          {/* typeName */}
          <div>
            <label className="block text-[10px] uppercase text-slate-500 mb-0.5">type</label>
            {isClassRange ? (
              <SearchableSelect
                options={classOptions}
                value={row.typeName}
                onChange={(v) => onChange({ typeName: v })}
                placeholder="class name…"
                disabled={!row.typeKind}
              />
            ) : (
              <select
                value={row.typeName}
                onChange={(e) => onChange({ typeName: e.target.value })}
                disabled={!row.typeKind}
                className={inputClass + " bg-white"}
              >
                <option value="">—</option>
                {typeNameOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      )}

      {row.mode === "new" && (
        <div className="flex gap-4 mt-1.5">
          <label className="flex items-center gap-1 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={row.identifier}
              onChange={(e) => onChange({ identifier: e.target.checked })}
            />
            identifier
          </label>
          <label className="flex items-center gap-1 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={row.required}
              onChange={(e) => onChange({ required: e.target.checked })}
            />
            required
          </label>
        </div>
      )}
    </div>
  );
}
