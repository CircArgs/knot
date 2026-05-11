import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { SpecType } from "../../types/spec";
import {
  CheckboxField as _Cb,
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  selectClass,
  textareaClass,
} from "./fields";

// Suppress unused-import warning; CheckboxField unused but exported by fields.
void _Cb;

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const BASES = ["", "str", "int", "float", "bool", "datetime", "date"] as const;

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  base: z.string(),
  pattern: z.string(),
  description: z.string(),
});

export type TypeFormValues = z.infer<typeof Schema>;

interface Props {
  initial?: Partial<SpecType>;
  /** When editing, the name is locked (delete+add for renames). */
  lockName?: boolean;
  onSubmit: (vals: TypeFormValues) => Promise<void>;
}

export default function TypeForm({ initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<TypeFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      base: initial?.base ?? "",
      pattern: initial?.pattern ?? "",
      description: initial?.description ?? "",
    },
  });

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <FieldRow>
        <Label required>name</Label>
        <input
          {...register("name")}
          className={inputClass}
          disabled={lockName}
          placeholder="e.g. positive_int"
        />
        <ErrText error={errors.name} />
      </FieldRow>
      <FieldRow>
        <Label>base</Label>
        <select {...register("base")} className={selectClass}>
          {BASES.map((b) => (
            <option key={b} value={b}>
              {b || "(none)"}
            </option>
          ))}
        </select>
      </FieldRow>
      <FieldRow>
        <Label>pattern</Label>
        <input
          {...register("pattern")}
          className={inputClass}
          placeholder="^[A-Z][0-9]+$"
        />
      </FieldRow>
      <FieldRow>
        <Label>description</Label>
        <textarea {...register("description")} className={textareaClass} />
      </FieldRow>
      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save type</Submit>
      </div>
    </form>
  );
}
