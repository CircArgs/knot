/**
 * Toolbar dropdown that picks between the two GraphQL surfaces. The label
 * carries the user-visible name; the value is what every other component
 * keys off (localStorage, fetch URL, default-query map).
 */
import type { EndpointKey } from "../../lib/queryEndpoints";
import { ENDPOINTS } from "../../lib/queryEndpoints";

interface Props {
  value: EndpointKey;
  onChange: (next: EndpointKey) => void;
  disabled?: boolean;
}

export default function EndpointSwitcher({ value, onChange, disabled }: Props) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-knot-muted">endpoint</span>
      <select
        className="border rounded px-2 py-1 bg-white"
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value as EndpointKey)}
      >
        {Object.entries(ENDPOINTS).map(([k, def]) => (
          <option key={k} value={k}>
            {def.label} ({def.path})
          </option>
        ))}
      </select>
    </label>
  );
}
