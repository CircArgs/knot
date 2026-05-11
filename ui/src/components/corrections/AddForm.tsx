import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useState } from "react";

import type { PublishedSpec, SpecSlot } from "../../types/spec";
import {
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
  onSubmit: (vals: {
    newCanonicalId: string;
    values: Record<string, unknown>;
  }) => Promise<void>;
}

const NAME_PATTERN = /^[^\s].*$/; // non-empty, not starting with whitespace

const Schema = z.object({
  newCanonicalId: z.string().regex(NAME_PATTERN, "must be non-empty"),
});

type FormValues = z.infer<typeof Schema>;

/**
 * Add form — create a synthetic entity attributed to user-corrections.
 *
 * Renders one input per stored slot, typed by slot range; the operator can
 * leave any optional slot blank. We submit only the non-empty slot values
 * (so the server's row-model validator can run with whatever subset is
 * provided).
 */
export default function AddForm({ spec, className, onSubmit }: Props) {
  const cls = spec.classes.find((c) => c.name === className);
  const slotsByName = new Map(spec.slots.map((s) => [s.name, s]));
  const storedSlots: SpecSlot[] = (cls?.slotNames ?? [])
    .map((n) => slotsByName.get(n))
    .filter((s): s is SpecSlot => !!s && s.rangeKind !== null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(Schema),
    defaultValues: { newCanonicalId: "" },
  });

  // Per-slot raw inputs — managed outside react-hook-form because the
  // shape is fully dynamic per class.
  const [text, setText] = useState<Record<string, string>>({});
  const [bool, setBool] = useState<Record<string, boolean>>({});
  const [classRefs, setClassRefs] = useState<Record<string, string>>({});
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  const submit = handleSubmit(async (vals) => {
    setSubmitErr(null);
    try {
      const values = buildValues(spec, storedSlots, text, bool, classRefs);
      await onSubmit({ newCanonicalId: vals.newCanonicalId.trim(), values });
    } catch (e) {
      setSubmitErr(String((e as Error).message ?? e));
    }
  });

  return (
    <form onSubmit={submit}>
      <div className="mb-3 text-xs text-slate-500">
        Creating a new <span className="font-mono">{className}</span> entity
        attributed to the user-corrections synthetic source.
      </div>

      <FieldRow>
        <Label required>new canonical_id</Label>
        <input
          {...register("newCanonicalId")}
          className={inputClass}
          placeholder="e.g. inception_2010"
        />
        <ErrText error={errors.newCanonicalId} />
      </FieldRow>

      <div className="border-t border-slate-200 my-3" />

      {storedSlots.length === 0 && (
        <p className="text-xs text-slate-400 italic">
          {className} has no stored slots — submitting will create an empty
          entity.
        </p>
      )}

      {storedSlots.map((slot) => (
        <FieldRow key={slot.name}>
          <Label required={slot.required}>{slot.name}</Label>
          <SlotInput
            slot={slot}
            spec={spec}
            text={text[slot.name] ?? ""}
            bool={bool[slot.name] ?? false}
            classRef={classRefs[slot.name] ?? ""}
            setText={(v) => setText({ ...text, [slot.name]: v })}
            setBool={(v) => setBool({ ...bool, [slot.name]: v })}
            setClassRef={(v) =>
              setClassRefs({ ...classRefs, [slot.name]: v })
            }
          />
        </FieldRow>
      ))}

      {submitErr && <p className="text-xs text-rose-700 mb-2">{submitErr}</p>}

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Add</Submit>
      </div>
    </form>
  );
}

function SlotInput({
  slot,
  spec,
  text,
  bool,
  classRef,
  setText,
  setBool,
  setClassRef,
}: {
  slot: SpecSlot;
  spec: PublishedSpec;
  text: string;
  bool: boolean;
  classRef: string;
  setText: (v: string) => void;
  setBool: (v: boolean) => void;
  setClassRef: (v: string) => void;
}) {
  if (slot.rangeKind === "class" && slot.rangeName) {
    if (slot.multivalued) {
      return (
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          className={textareaClass}
          placeholder="comma-separated canonical_ids of the target class"
        />
      );
    }
    return (
      <CanonicalIdPicker
        className={slot.rangeName}
        value={classRef}
        onChange={setClassRef}
      />
    );
  }

  const baseType = baseTypeFor(spec, slot);
  if (baseType === "boolean" && !slot.multivalued) {
    return (
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input
          type="checkbox"
          checked={bool}
          onChange={(e) => setBool(e.target.checked)}
        />
        true
      </label>
    );
  }
  if (slot.multivalued) {
    return (
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        className={textareaClass}
        placeholder="comma-separated values"
      />
    );
  }
  const inputType =
    baseType === "integer" || baseType === "float"
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
      step={baseType === "float" ? "any" : undefined}
    />
  );
}

function baseTypeFor(spec: PublishedSpec, slot: SpecSlot): string {
  if (slot.rangeKind !== "type" || !slot.rangeName) return "string";
  let cur: string | null = slot.rangeName;
  for (let i = 0; i < 8 && cur; i++) {
    if (["string", "integer", "float", "boolean", "date", "datetime"].includes(cur)) {
      return cur;
    }
    const t = spec.types.find((x) => x.name === cur);
    if (!t) break;
    cur = t.base;
  }
  return "string";
}

function buildValues(
  spec: PublishedSpec,
  slots: SpecSlot[],
  text: Record<string, string>,
  bool: Record<string, boolean>,
  classRefs: Record<string, string>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const slot of slots) {
    if (slot.rangeKind === "class") {
      if (slot.multivalued) {
        const t = text[slot.name];
        if (t == null || !t.trim()) continue;
        const ids = t
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
        if (ids.length > 0) out[slot.name] = ids;
      } else {
        const v = classRefs[slot.name];
        if (v) out[slot.name] = v;
      }
      continue;
    }
    const baseType = baseTypeFor(spec, slot);
    if (baseType === "boolean" && !slot.multivalued) {
      // Booleans are always present in the bool map; only emit if the user
      // explicitly checked it — leaving blank means "skip this slot".
      if (slot.name in bool) out[slot.name] = bool[slot.name];
      continue;
    }
    if (slot.multivalued) {
      const t = text[slot.name];
      if (t == null || !t.trim()) continue;
      const parts = t.split(",").map((s) => s.trim()).filter(Boolean);
      if (parts.length === 0) continue;
      if (baseType === "integer") out[slot.name] = parts.map((p) => Number.parseInt(p, 10));
      else if (baseType === "float") out[slot.name] = parts.map(parseFloat);
      else out[slot.name] = parts;
      continue;
    }
    const t = text[slot.name];
    if (t == null || t.trim() === "") continue;
    if (baseType === "integer") out[slot.name] = Number.parseInt(t, 10);
    else if (baseType === "float") out[slot.name] = Number.parseFloat(t);
    else out[slot.name] = t;
  }
  return out;
}
