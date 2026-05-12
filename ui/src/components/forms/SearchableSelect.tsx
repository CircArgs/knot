import { useEffect, useRef, useState } from "react";
import { inputClass } from "./fields";

export interface SelectOption {
  value: string;
  label: string;
}

interface Props {
  options: SelectOption[];
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
}

/**
 * Minimal searchable single-select. Renders a text input that filters the
 * option list as the user types; selecting an option commits the value.
 * Clicking outside or pressing Escape closes the dropdown without committing
 * a partial query.
 */
export default function SearchableSelect({
  options,
  value,
  onChange,
  placeholder = "Search…",
  disabled = false,
  className,
}: Props) {
  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // When the dropdown is closed, display the selected label in the input.
  const displayValue = open ? query : selectedLabel;

  const filtered = query.trim()
    ? options.filter(
        (o) =>
          o.label.toLowerCase().includes(query.toLowerCase()) ||
          o.value.toLowerCase().includes(query.toLowerCase()),
      )
    : options;

  const commit = (opt: SelectOption) => {
    onChange(opt.value);
    setQuery("");
    setOpen(false);
  };

  const handleFocus = () => {
    setQuery("");
    setOpen(true);
  };

  const handleBlur = (e: React.FocusEvent) => {
    // Don't close if focus is moving to an option button inside the container.
    if (containerRef.current?.contains(e.relatedTarget as Node)) return;
    setOpen(false);
    setQuery("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  };

  // Close on outside click (pointer events don't always fire blur).
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) {
        setOpen(false);
        setQuery("");
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  return (
    <div ref={containerRef} className={`relative ${className ?? ""}`}>
      <input
        type="text"
        value={displayValue}
        disabled={disabled}
        placeholder={placeholder}
        className={inputClass}
        onFocus={handleFocus}
        onBlur={handleBlur}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={handleKeyDown}
        autoComplete="off"
      />
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full max-h-48 overflow-y-auto bg-white border border-slate-300 rounded shadow-lg text-sm">
          {filtered.map((opt) => (
            <li key={opt.value}>
              <button
                type="button"
                className={`w-full text-left px-3 py-1.5 font-mono hover:bg-blue-50 ${
                  opt.value === value ? "bg-blue-100 text-blue-900" : "text-slate-800"
                }`}
                onMouseDown={(e) => {
                  // Prevent the input's onBlur from firing before onClick.
                  e.preventDefault();
                  commit(opt);
                }}
              >
                {opt.label}
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && filtered.length === 0 && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-300 rounded shadow-lg px-3 py-2 text-xs text-slate-500 italic">
          no matches
        </div>
      )}
    </div>
  );
}
