import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { SpecSource } from "../../types/spec";
import {
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  textareaClass,
} from "./fields";

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  description: z.string(),
});

export type SourceFormValues = z.infer<typeof Schema>;

interface Props {
  spec?: never;
  initial?: Partial<SpecSource>;
  lockName?: boolean;
  onSubmit: (vals: SourceFormValues) => Promise<void>;
}

export default function SourceForm({ initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SourceFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      description: initial?.description ?? "",
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
          placeholder="e.g. imdb"
        />
        <ErrText error={errors.name} />
      </FieldRow>
      <FieldRow>
        <Label>description</Label>
        <textarea {...register("description")} className={textareaClass} />
      </FieldRow>
      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save source</Submit>
      </div>
    </form>
  );
}
