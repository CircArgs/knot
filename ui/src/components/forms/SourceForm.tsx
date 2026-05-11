import { useMemo } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecSource } from "../../types/spec";
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

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  entityClassName: z.string().min(1, "required"),
  identifierSlotName: z.string().min(1, "required"),
  description: z.string(),
});

export type SourceFormValues = z.infer<typeof Schema>;

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecSource>;
  lockName?: boolean;
  onSubmit: (vals: SourceFormValues) => Promise<void>;
}

export default function SourceForm({ spec, initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<SourceFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      entityClassName: initial?.entityClassName ?? "",
      identifierSlotName: initial?.identifierSlotName ?? "",
      description: initial?.description ?? "",
    },
  });

  const entityClassName = watch("entityClassName");
  const idSlots = useMemo(() => {
    const cls = spec.classes.find((c) => c.name === entityClassName);
    if (!cls) return [];
    // Filter the class's slots down to those marked identifier=true.
    const onClass = new Set(cls.slotNames);
    return spec.slots.filter((s) => onClass.has(s.name) && s.identifier);
  }, [entityClassName, spec]);

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
        <Label required>entity class</Label>
        <select {...register("entityClassName")} className={selectClass}>
          <option value="">(pick a class)</option>
          {spec.classes.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
        <ErrText error={errors.entityClassName} />
      </FieldRow>
      <FieldRow>
        <Label required>identifier slot</Label>
        <select {...register("identifierSlotName")} className={selectClass}>
          <option value="">
            {entityClassName ? "(pick an identifier slot)" : "(pick a class first)"}
          </option>
          {idSlots.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name}
            </option>
          ))}
        </select>
        {idSlots.length === 0 && entityClassName && (
          <p className="text-xs text-amber-700 mt-1">
            class has no identifier slots; mark a slot as identifier first.
          </p>
        )}
        <ErrText error={errors.identifierSlotName} />
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
