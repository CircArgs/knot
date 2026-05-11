import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecSlot } from "../../types/spec";
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

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  rangeKind: z.enum(["", "type", "class"]),
  rangeName: z.string(),
  identifier: z.boolean(),
  required: z.boolean(),
  multivalued: z.boolean(),
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
  initial?: Partial<SpecSlot>;
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
      rangeKind: (initial?.rangeKind as "type" | "class" | null) ?? "",
      rangeName: initial?.rangeName ?? "",
      identifier: initial?.identifier ?? false,
      required: initial?.required ?? false,
      multivalued: initial?.multivalued ?? false,
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
  const rangeKind = watch("rangeKind");
  const rangeOptions =
    rangeKind === "type"
      ? spec.types.map((t) => t.name)
      : rangeKind === "class"
        ? spec.classes.map((c) => c.name)
        : [];

  const [identifier, required, multivalued] = [
    watch("identifier"),
    watch("required"),
    watch("multivalued"),
  ];

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
          <Label>range kind</Label>
          <select {...register("rangeKind")} className={selectClass}>
            <option value="">(unranged)</option>
            <option value="type">type</option>
            <option value="class">class</option>
          </select>
        </FieldRow>
        <FieldRow>
          <Label>range name</Label>
          <input
            list="range-options"
            {...register("rangeName")}
            className={inputClass}
            disabled={!rangeKind}
            placeholder={rangeKind ? `pick a ${rangeKind}` : "—"}
          />
          <datalist id="range-options">
            {rangeOptions.map((opt) => (
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
          <CheckboxField
            label="multivalued"
            checked={multivalued}
            onChange={(b) => setValue("multivalued", b)}
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

      <FieldRow>
        <Label>derivation (JSON, advanced)</Label>
        <textarea
          {...register("derivation")}
          className={textareaClass}
          placeholder='{"$kind": "FormatDerivation", ...}'
        />
      </FieldRow>

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save slot</Submit>
      </div>
    </form>
  );
}
