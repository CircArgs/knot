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
        <select {...register("primaryClassName")} className={selectClass}>
          <option value="">(pick a class)</option>
          {spec.classes.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
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
        <Label required>body (JSON ExprTree, advanced)</Label>
        <textarea
          {...register("body")}
          className={textareaClass}
          placeholder='{"$kind": "BoolExpr", "op": "and", "args": [...]}'
        />
        <ErrText error={errors.body} />
      </FieldRow>
      <FieldRow>
        <Label>message</Label>
        <input {...register("message")} className={inputClass} />
      </FieldRow>
      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save constraint</Submit>
      </div>
    </form>
  );
}
