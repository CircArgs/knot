import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

import type { PublishedSpec, SpecClass } from "../../types/spec";
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

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  slotNames: z.array(z.string()),
  isAName: z.string(),
  mixinNames: z.array(z.string()),
  abstract: z.boolean(),
  description: z.string(),
  definition: z.string(),
});

export type ClassFormValues = z.infer<typeof Schema>;

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecClass>;
  lockName?: boolean;
  onSubmit: (vals: ClassFormValues) => Promise<void>;
}

export default function ClassForm({ spec, initial, lockName, onSubmit }: Props) {
  const {
    register,
    handleSubmit,
    watch,
    setValue,
    formState: { errors, isSubmitting },
  } = useForm<ClassFormValues>({
    resolver: zodResolver(Schema),
    defaultValues: {
      name: initial?.name ?? "",
      slotNames: initial?.slotNames ?? [],
      isAName: initial?.isAName ?? "",
      mixinNames: initial?.mixinNames ?? [],
      abstract: initial?.abstract ?? false,
      description: initial?.description ?? "",
      definition: "",
    },
  });

  const slotNames = watch("slotNames");
  const mixinNames = watch("mixinNames");
  const abstract = watch("abstract");

  const toggleSlot = (name: string) => {
    setValue(
      "slotNames",
      slotNames.includes(name)
        ? slotNames.filter((n) => n !== name)
        : [...slotNames, name],
    );
  };
  const toggleMixin = (name: string) => {
    setValue(
      "mixinNames",
      mixinNames.includes(name)
        ? mixinNames.filter((n) => n !== name)
        : [...mixinNames, name],
    );
  };

  const otherClasses = spec.classes.filter((c) => c.name !== initial?.name);

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
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
        <select {...register("isAName")} className={selectClass}>
          <option value="">(none)</option>
          {otherClasses.map((c) => (
            <option key={c.name} value={c.name}>
              {c.name}
            </option>
          ))}
        </select>
      </FieldRow>

      <FieldRow>
        <Label>slots</Label>
        <ChecklistGrid
          options={spec.slots.map((s) => s.name)}
          selected={slotNames}
          onToggle={toggleSlot}
        />
      </FieldRow>

      <FieldRow>
        <Label>mixins</Label>
        <ChecklistGrid
          options={otherClasses.map((c) => c.name)}
          selected={mixinNames}
          onToggle={toggleMixin}
        />
      </FieldRow>

      <FieldRow>
        <Label>description</Label>
        <textarea {...register("description")} className={textareaClass} />
      </FieldRow>

      <FieldRow>
        <Label>definition (JSON, advanced)</Label>
        <textarea
          {...register("definition")}
          className={textareaClass}
          placeholder='{"$kind": "BoolExpr", ...}'
        />
      </FieldRow>

      <div className="flex justify-end">
        <Submit busy={isSubmitting}>Save class</Submit>
      </div>
    </form>
  );
}

function ChecklistGrid({
  options,
  selected,
  onToggle,
}: {
  options: string[];
  selected: string[];
  onToggle: (name: string) => void;
}) {
  if (options.length === 0) {
    return <p className="text-xs text-slate-500 italic">no options available</p>;
  }
  return (
    <div className="border border-slate-200 rounded p-2 max-h-32 overflow-y-auto grid grid-cols-2 gap-1">
      {options.map((opt) => (
        <label key={opt} className="flex items-center gap-1.5 text-xs text-slate-700 font-mono">
          <input
            type="checkbox"
            checked={selected.includes(opt)}
            onChange={() => onToggle(opt)}
          />
          {opt}
        </label>
      ))}
    </div>
  );
}
