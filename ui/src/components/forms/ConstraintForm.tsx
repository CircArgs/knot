import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecConstraint } from "../../types/spec";
import {
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  selectClass,
  textareaClass,
} from "./fields";
import SearchableSelect from "./SearchableSelect";

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const SEVERITIES = ["error", "warning"] as const;

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  primaryClassName: z.string().min(1, "required"),
  body: z.string().min(1, "constraint body is required (JSON ExprTree)"),
  severity: z.enum(SEVERITIES),
  message: z.string(),
});

export type ConstraintFormValues = z.infer<typeof Schema>;

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecConstraint>;
  lockName?: boolean;
  onSubmit: (vals: ConstraintFormValues) => Promise<void>;
}

function normalizeSev(s: string | undefined): (typeof SEVERITIES)[number] | null {
  if (!s) return null;
  const lc = s.toLowerCase();
  return (SEVERITIES as readonly string[]).includes(lc)
    ? (lc as (typeof SEVERITIES)[number])
    : null;
}

export default function ConstraintForm({ spec, initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ConstraintFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      primaryClassName: initial?.primaryClassName ?? "",
      body: "",
      severity: normalizeSev(initial?.severity) ?? "error",
      message: initial?.message ?? "",
    },
  });

  const primaryClassName = watch("primaryClassName");
  const body = watch("body");
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const classOptions = spec.classes.map((c) => ({ value: c.name, label: c.name }));

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <FieldRow>
        <Label required>name</Label>
        <input
          {...register("name")}
          disabled={lockName}
          className={inputClass}
          placeholder="e.g. imdb_id_present"
        />
        <ErrText error={errors.name} />
      </FieldRow>

      <FieldRow>
        <Label required>primary class</Label>
        <SearchableSelect
          options={[{ value: "", label: "(pick a class)" }, ...classOptions]}
          value={primaryClassName}
          onChange={(v) => setValue("primaryClassName", v)}
          placeholder="Search classes…"
        />
        <ErrText error={errors.primaryClassName} />
      </FieldRow>

      <FieldRow>
        <Label>severity</Label>
        <select {...register("severity")} className={selectClass}>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </FieldRow>

      <FieldRow>
        <Label>message</Label>
        <input {...register("message")} className={inputClass} />
      </FieldRow>

      {/* ── Advanced: body ─────────────────────────────────────────────────── */}
      <div className="mb-3">
        <button
          type="button"
          onClick={() => setAdvancedOpen((b) => !b)}
          className="text-xs text-slate-500 hover:text-slate-700 flex items-center gap-1"
        >
          <span>{advancedOpen ? "▾" : "▸"}</span>
          Advanced — body (JSON ExprTree) <span className="text-rose-500 ml-0.5">*</span>
        </button>
        {advancedOpen && (
          <div className="mt-2">
            <textarea
              {...register("body")}
              className={`${textareaClass} ${
                errors.body && !body ? "border-rose-400 ring-1 ring-rose-300" : ""
              }`}
              placeholder='{"$kind": "BoolExpr", "op": "and", "args": [...]}'
              rows={5}
            />
            {errors.body && (
              <p className="text-xs text-rose-600 mt-1">{errors.body.message}</p>
            )}
          </div>
        )}
        {!advancedOpen && errors.body && (
          <p className="text-xs text-rose-600 mt-1">
            constraint requires a body — expand Advanced to fill it in
          </p>
        )}
      </div>

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save constraint</Submit>
      </div>
    </form>
  );
}
