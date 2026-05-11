import type { FieldError } from "react-hook-form";

/**
 * Tailwind form-field helpers shared by every entity form.
 * Keep these dumb — no react-hook-form registration here, just styling.
 */

export function Label({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <label className="block text-xs uppercase tracking-wide text-slate-600 font-medium mb-1">
      {children}
      {required && <span className="text-rose-600 ml-0.5">*</span>}
    </label>
  );
}

export function ErrText({ error }: { error?: FieldError | undefined | { message?: string } }) {
  if (!error) return null;
  const msg = (error as { message?: string }).message;
  if (!msg) return null;
  return <p className="text-xs text-rose-600 mt-1">{msg}</p>;
}

export const inputClass =
  "w-full px-2.5 py-1.5 border border-slate-300 rounded text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-400 focus:border-blue-400";

export const selectClass = inputClass + " bg-white";

export const textareaClass =
  inputClass + " min-h-[80px] font-mono text-xs whitespace-pre";

export function CheckboxField({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (b: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-sm text-slate-700">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

export function FieldRow({ children }: { children: React.ReactNode }) {
  return <div className="mb-3">{children}</div>;
}

export function Submit({ children, busy }: { children: React.ReactNode; busy?: boolean }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="px-4 py-1.5 rounded bg-blue-600 text-white font-medium hover:bg-blue-700 disabled:opacity-50"
    >
      {busy ? "Saving…" : children}
    </button>
  );
}
