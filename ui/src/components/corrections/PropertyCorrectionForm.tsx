import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { useState } from "react";
import { z } from "zod";

import type { PublishedSpec, SpecProperty } from "../../types/spec";
import { BUILTIN_TYPES, isArrayKind, isClassKind, isPrimitiveKind } from "../../types/spec";
import {
  CheckboxField,
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  textareaClass,
} from "../forms/fields";
import CanonicalIdPicker from "./CanonicalIdPicker";

interface Props {
  spec: PublishedSpec;
  className: string;
  canonicalId: string;
  /** Pre-fill the slot dropdown if known. */
  initialSlot?: string;
  onSubmit: (vals: {
    property: string;
    value: unknown;
  }) => Promise<void>;
}

const Schema = z.object({
  property: z.string().min(1, "select a slot"),
  /** Raw text input; we coerce per-slot at submit time. */
  text: z.string(),
  bool: z.boolean(),
  list: z.string(), // comma-separated for array slots
  classRangeId: z.string(),
});

type FormValues = z.infer<typeof Schema>;

/**
 * Edit one slot value for one canonical entity.
 *
 * The "value" input is rendered conditionally on the slot's typeKind.
 * For class-range properties, we hand off to CanonicalIdPicker so the operator
 * picks an existing target canonical_id (debounced search, since classes
 * can have thousands of rows).
 */
export default function PropertyCorrectionForm({
  spec,
  className,
  canonicalId,
  initialSlot,
  onSubmit,
}: Props) {
  const cls = spec.classes.find((c) => c.name === className);
  // Stored slots only: typeKind must be set (derived slots have null).
  const storedSlots: SpecProperty[] = (cls?.properties ?? []).filter(
    (s): s is SpecProperty => s.typeKind !== null,
  );

  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      property: initialSlot ?? "",
      text: "",
      bool: false,
      list: "",
      classRangeId: "",
    },
  });

  const selectedName = watch("property");
  const slot = storedSlots.find((s) => s.name === selectedName) ?? null;

  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const submit = handleSubmit(async (vals) => {
    if (!slot) {
      setSubmitErr("Pick a slot first.");
      return;
    }
    setSubmitErr(null);
    try {
      const value = coerceValue(property, vals);
      await onSubmit({ property: slot.name, value });
    } catch (e) {
      setSubmitErr(String((e as Error).message ?? e));
    }
  });

  return (
    <form onSubmit={submit}>
      <div className="mb-3 text-xs text-slate-500">
        Editing{" "}
        <span className="font-mono text-slate-700">
          {className}:{canonicalId}
        </span>
        . Submits via the user-corrections synthetic source.
      </div>

      <FieldRow>
        <Label required>slot</Label>
        <select
          {...register("property")}
          className={inputClass + " bg-white"}
          disabled={!!initialSlot}
        >
          <option value="">— pick slot —</option>
          {storedSlots.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} ({rangeLabel(s)})
            </option>
          ))}
        </select>
        <ErrText error={errors.slot} />
      </FieldRow>

      {slot && (
        <FieldRow>
          <Label>value</Label>
          <SlotValueInput
            slot={slot}
            text={watch("text")}
            list={watch("list")}
            bool={watch("bool")}
            classRangeId={watch("classRangeId")}
            setText={(v) => setValue("text", v)}
            setList={(v) => setValue("list", v)}
            setBool={(v) => setValue("bool", v)}
            setClassRangeId={(v) => setValue("classRangeId", v)}
          />
        </FieldRow>
      )}

      {submitErr && <p className="text-xs text-rose-700 mb-2">{submitErr}</p>}

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Apply correction</Submit>
      </div>
    </form>
  );
}

function rangeLabel(property: SpecProperty): string {
  if (isClassKind(slot.typeKind)) return `→ ${slot.typeName}`;
  if (isPrimitiveKind(slot.typeKind)) return slot.typeName ?? "primitive";
  return "derived";
}

function SlotValueInput({
  property,
  text,
  list,
  bool,
  classRangeId,
  setText,
  setList,
  setBool,
  setClassRangeId,
}: {
  property: SpecProperty;
  text: string;
  list: string;
  bool: boolean;
  classRangeId: string;
  setText: (v: string) => void;
  setList: (v: string) => void;
  setBool: (v: boolean) => void;
  setClassRangeId: (v: string) => void;
}) {
  // Class-range slot — CanonicalIdPicker debounce-searches the target class.
  if (isClassKind(slot.typeKind) && slot.typeName) {
    if (isArrayKind(slot.typeKind)) {
      return (
        <textarea
          value={list}
          onChange={(e) => setList(e.target.value)}
          className={textareaClass}
          placeholder="comma-separated canonical_ids of the target class"
        />
      );
    }
    return (
      <CanonicalIdPicker
        className={slot.typeName}
        value={classRangeId}
        onChange={setClassRangeId}
      />
    );
  }

  const baseType = baseTypeFor(slot);
  if (baseType === "boolean") {
    return (
      <CheckboxField
        label={`${slot.name} = true`}
        checked={bool}
        onChange={setBool}
      />
    );
  }
  if (isArrayKind(slot.typeKind)) {
    return (
      <textarea
        value={list}
        onChange={(e) => setList(e.target.value)}
        className={textareaClass}
        placeholder="comma-separated values"
      />
    );
  }
  const inputType =
    baseType === "integer"
      ? "number"
      : baseType === "float"
        ? "number"
        : baseType === "date"
          ? "date"
          : baseType === "datetime"
            ? "datetime-local"
            : "text";
  return (
    <input
      type={inputType}
      value={text}
      onChange={(e) => setText(e.target.value)}
      className={inputClass}
      placeholder={slot.typeName ?? "value"}
      step={baseType === "float" ? "any" : undefined}
    />
  );
}

/**
 * Walk typeName to find the base primitive. Since types are now language-level
 * (no spec.types lookup), we just check if typeName is directly a builtin.
 */
function baseTypeFor(property: SpecProperty): string {
  if (!isPrimitiveKind(slot.typeKind) || !slot.typeName) return "string";
  const name = slot.typeName.toLowerCase();
  if (BUILTIN_TYPES.has(name)) return name;
  return "string";
}

function coerceValue(property: SpecProperty, vals: FormValues): unknown {
  if (isClassKind(slot.typeKind)) {
    if (isArrayKind(slot.typeKind)) {
      return vals.list
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }
    return vals.classRangeId || null;
  }
  if (isArrayKind(slot.typeKind)) {
    return vals.list
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
  }
  if (isPrimitiveKind(slot.typeKind)) {
    const t = slot.typeName?.toLowerCase() ?? "";
    if (t.includes("int")) return vals.text === "" ? null : Number.parseInt(vals.text, 10);
    if (t.includes("float") || t.includes("double") || t.includes("number")) {
      return vals.text === "" ? null : Number.parseFloat(vals.text);
    }
    if (t === "boolean") return vals.bool;
  }
  return vals.text;
}
