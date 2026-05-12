import { useCallback, useEffect, useRef, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import Editor from "@monaco-editor/react";

import type { PublishedSpec, SpecConstraint } from "../../types/spec";
import {
  ErrText,
  FieldRow,
  Label,
  Submit,
  inputClass,
  selectClass,
} from "./fields";
import SearchableSelect from "./SearchableSelect";

const NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]{0,62}$/;
const SEVERITIES = ["error", "warning"] as const;

const Schema = z.object({
  name: z.string().regex(NAME_PATTERN, "must match ^[A-Za-z_][A-Za-z0-9_]{0,62}$"),
  primaryClassName: z.string().min(1, "required"),
  body: z.string().min(1, "SQL predicate is required"),
  severity: z.enum(SEVERITIES),
  message: z.string(),
});

export type ConstraintFormValues = z.infer<typeof Schema>;

interface Props {
  spec: PublishedSpec;
  initial?: Partial<SpecConstraint>;
  lockName?: boolean;
  onSubmit: (vals: ConstraintFormValues) => Promise<void>;
  /** Base URL for the spec API (e.g. "/api" or ""). Used for live SQL validation. */
  draftId?: number;
}

function normalizeSev(s: string | undefined): (typeof SEVERITIES)[number] | null {
  if (!s) return null;
  const lc = s.toLowerCase();
  return (SEVERITIES as readonly string[]).includes(lc)
    ? (lc as (typeof SEVERITIES)[number])
    : null;
}

/** Debounce helper — returns a stable debounced function. */
function useDebounce<T extends (...args: Parameters<T>) => void>(fn: T, delay: number): T {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  return useCallback(
    ((...args: Parameters<T>) => {
      if (timer.current !== null) clearTimeout(timer.current);
      timer.current = setTimeout(() => fn(...args), delay);
    }) as T,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [fn, delay],
  );
}

export default function ConstraintForm({
  spec,
  initial,
  lockName,
  onSubmit,
  draftId,
}: Props) {
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
      body: initial?.body ?? "",
      severity: normalizeSev(initial?.severity) ?? "error",
      message: initial?.message ?? "",
    },
  });

  const primaryClassName = watch("primaryClassName");
  const body = watch("body");

  // Live SQL validation state
  const [sqlError, setSqlError] = useState<string | null>(null);
  const [sqlValid, setSqlValid] = useState(false);
  const [validating, setValidating] = useState(false);

  const classOptions = spec.classes.map((c) => ({ value: c.name, label: c.name }));

  /** Validate the SQL body against the server's parse endpoint. */
  const validateSql = useCallback(
    async (sql: string, className: string) => {
      if (!sql.trim() || !draftId) {
        setSqlError(null);
        setSqlValid(false);
        return;
      }
      setValidating(true);
      try {
        const resp = await fetch(`/spec/drafts/${draftId}/_validate_constraint`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body: sql, primary_class_name: className || undefined }),
        });
        if (resp.ok) {
          const data = (await resp.json()) as { valid: boolean; errors: string[] };
          if (data.valid) {
            setSqlError(null);
            setSqlValid(true);
          } else {
            setSqlError(data.errors[0] ?? "Invalid SQL predicate");
            setSqlValid(false);
          }
        } else {
          setSqlError(null); // network error — don't block submission
          setSqlValid(false);
        }
      } catch {
        setSqlError(null);
        setSqlValid(false);
      } finally {
        setValidating(false);
      }
    },
    [draftId],
  );

  const debouncedValidate = useDebounce(validateSql, 400);

  useEffect(() => {
    setSqlValid(false);
    debouncedValidate(body, primaryClassName);
  }, [body, primaryClassName, debouncedValidate]);

  return (
    <form onSubmit={handleSubmit(onSubmit)}>
      <FieldRow>
        <Label required>name</Label>
        <input
          {...register("name")}
          disabled={lockName}
          className={inputClass}
          placeholder="e.g. year_not_before_cinema"
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

      {/* ── SQL predicate body ─────────────────────────────────────────────── */}
      <div className="mb-3">
        <div className="flex items-center justify-between mb-1">
          <Label required>body (SQL predicate)</Label>
          {validating && (
            <span className="text-xs text-slate-400 italic">validating…</span>
          )}
        </div>
        <div
          className={`border rounded overflow-hidden ${
            (errors.body && !body) || sqlError
              ? "border-rose-400 ring-1 ring-rose-300"
              : "border-slate-300"
          }`}
          style={{ height: 120 }}
        >
          <Editor
            height="120px"
            defaultLanguage="sql"
            language="sql"
            value={body}
            onChange={(v) => setValue("body", v ?? "", { shouldValidate: true })}
            options={{
              minimap: { enabled: false },
              fontSize: 12,
              scrollBeyondLastLine: false,
              automaticLayout: true,
              lineNumbers: "off",
              glyphMargin: false,
              folding: false,
              lineDecorationsWidth: 4,
              lineNumbersMinChars: 0,
              wordWrap: "on",
              renderLineHighlight: "none",
              overviewRulerLanes: 0,
              scrollbar: { vertical: "hidden", horizontal: "auto" },
            }}
          />
        </div>
        <p className="text-xs text-slate-400 mt-0.5">
          WHERE-clause fragment, e.g.{" "}
          <code className="bg-slate-100 px-0.5 rounded">year &gt;= 1888</code> or{" "}
          <code className="bg-slate-100 px-0.5 rounded">
            year &gt;= 1888 AND year &lt;= 2100
          </code>
        </p>
        {errors.body && !body && (
          <p className="text-xs text-rose-600 mt-1">{errors.body.message}</p>
        )}
        {sqlError && (
          <p className="text-xs text-rose-600 mt-1">{sqlError}</p>
        )}
        {sqlValid && !sqlError && body.trim() && (
          <p className="text-xs text-emerald-600 mt-1">Valid SQL predicate</p>
        )}
      </div>

      <div className="flex justify-end">
        <Submit busy={isSubmitting} disabled={!!sqlError || validating}>
          Save constraint
        </Submit>
      </div>
    </form>
  );
}
