import { useLocalStorage } from "../lib/useLocalStorage";

/**
 * Floating color-key for the spec graph. Overlays the React Flow canvas in the
 * top-right corner (like the toolbar overlays a page header). Collapsible —
 * click the header to toggle. Expansion state persists in localStorage.
 *
 * In class-card mode, only `class` is actually visible as a node; the other
 * entity kinds either live inline (slot rows, source chips, constraint chips)
 * or are hidden until the "show ontology details" toggle is on. Those rows
 * render dimmed so the user knows the legend covers them, but the canvas
 * doesn't currently have a corresponding standalone node.
 */
export type LegendMode = "class-card" | "details";

interface EntityRow {
  kind: "class" | "slot" | "type" | "source" | "constraint";
  label: string;
  /** Tailwind bg utility for the swatch — picked to match the node bg. */
  swatch: string;
  /** Tailwind border utility for the swatch — mirrors the node border. */
  border: string;
  /** Per-mode hint shown in parens after the label. */
  hint: { "class-card": string; details: string };
  /** True when a standalone node of this kind is rendered in the given mode. */
  standalone: { "class-card": boolean; details: boolean };
}

interface EdgeRow {
  label: string;
  stroke: string;
  strokeDasharray?: string;
  modes: ("class-card" | "details")[];
  /** Where this edge actually shows up, in plain words. */
  desc: string;
}

const EDGE_ROWS: EdgeRow[] = [
  {
    label: "cross-class FK",
    stroke: "#2563eb",
    modes: ["class-card"],
    desc: "slot whose range is another class",
  },
  {
    label: "is_a",
    stroke: "#475569",
    modes: ["class-card"],
    desc: "class inheritance",
  },
  {
    label: "mixin",
    stroke: "#94a3b8",
    strokeDasharray: "4 3",
    modes: ["class-card"],
    desc: "class composes a mixin",
  },
  {
    label: "has",
    stroke: "#cbd5e1",
    modes: ["details"],
    desc: "class lists this slot",
  },
  {
    label: "range",
    stroke: "#0f766e",
    modes: ["details"],
    desc: "slot's value type",
  },
  {
    label: "primary",
    stroke: "#e11d48",
    modes: ["details"],
    desc: "constraint's target class",
  },
  {
    label: "of",
    stroke: "#7c3aed",
    modes: ["details"],
    desc: "source ingests into class",
  },
  {
    label: "identifier",
    stroke: "#7c3aed",
    strokeDasharray: "3 3",
    modes: ["details"],
    desc: "source's identifier slot",
  },
  {
    label: "is_a",
    stroke: "#1e40af",
    strokeDasharray: "4 2",
    modes: ["details"],
    desc: "class inheritance",
  },
  {
    label: "mixin",
    stroke: "#1e40af",
    strokeDasharray: "2 4",
    modes: ["details"],
    desc: "class composes a mixin",
  },
];

const ROWS: EntityRow[] = [
  {
    kind: "class",
    label: "Class",
    swatch: "bg-white",
    border: "border-slate-300",
    hint: { "class-card": "card", details: "node" },
    standalone: { "class-card": true, details: true },
  },
  {
    kind: "slot",
    label: "Slot",
    swatch: "bg-green-50",
    border: "border-green-300",
    hint: { "class-card": "inline row", details: "node" },
    standalone: { "class-card": false, details: true },
  },
  {
    kind: "type",
    label: "Type",
    swatch: "bg-amber-50",
    border: "border-amber-300",
    hint: { "class-card": "inline range", details: "node" },
    standalone: { "class-card": false, details: true },
  },
  {
    kind: "source",
    label: "Source",
    swatch: "bg-purple-50",
    border: "border-purple-300",
    hint: { "class-card": "inline chip", details: "node" },
    standalone: { "class-card": false, details: true },
  },
  {
    kind: "constraint",
    label: "Constraint",
    swatch: "bg-rose-50",
    border: "border-rose-300",
    hint: { "class-card": "inline chip", details: "node" },
    standalone: { "class-card": false, details: true },
  },
];

export default function Legend({ mode }: { mode: LegendMode }) {
  const [expanded, setExpanded] = useLocalStorage("knot:legend:expanded", true);

  return (
    <div className="absolute top-2 right-2 z-10 select-none pointer-events-auto">
      <div className="rounded-lg border border-slate-300 bg-white/95 shadow-md text-xs backdrop-blur-sm">
        <button
          onClick={() => setExpanded((b) => !b)}
          className="w-full flex items-center justify-between gap-3 px-3 py-1.5 font-semibold text-slate-700 hover:bg-slate-50 rounded-t-lg"
          aria-expanded={expanded}
        >
          <span>Legend</span>
          <span className="text-slate-400 font-mono text-[10px]">
            {expanded ? "▾" : "▸"}
          </span>
        </button>
        {expanded && (
          <div className="border-t border-slate-200">
            <div className="px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                Nodes
              </div>
              <ul className="space-y-1">
                {ROWS.map((row) => {
                  const live = row.standalone[mode];
                  return (
                    <li
                      key={row.kind}
                      className={`flex items-center gap-2 ${
                        live ? "text-slate-800" : "text-slate-400"
                      }`}
                      title={
                        live
                          ? `${row.label} node visible in this view`
                          : `${row.label} not rendered as a standalone node in this view`
                      }
                    >
                      <span
                        className={`inline-block w-3 h-3 rounded border ${row.swatch} ${row.border} ${
                          live ? "" : "opacity-50"
                        }`}
                      />
                      <span className="font-medium w-[72px]">{row.label}</span>
                      <span
                        className={`font-mono text-[10px] ${
                          live ? "text-slate-500" : "text-slate-400 italic"
                        }`}
                      >
                        ({row.hint[mode]})
                      </span>
                    </li>
                  );
                })}
              </ul>
            </div>
            <div className="px-3 py-2 border-t border-slate-200">
              <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                Edges
              </div>
              <ul className="space-y-1">
                {EDGE_ROWS.filter((e) => e.modes.includes(mode)).map((edge) => (
                  <li
                    key={edge.label + edge.stroke}
                    className="flex items-center gap-2 text-slate-800"
                    title={edge.desc}
                  >
                    <svg
                      width={28}
                      height={10}
                      viewBox="0 0 28 10"
                      className="flex-shrink-0"
                      aria-hidden
                    >
                      <line
                        x1={2}
                        y1={5}
                        x2={22}
                        y2={5}
                        stroke={edge.stroke}
                        strokeWidth={1.5}
                        strokeDasharray={edge.strokeDasharray}
                      />
                      <polygon
                        points="22,2 26,5 22,8"
                        fill={edge.stroke}
                      />
                    </svg>
                    <span className="font-medium w-[72px]">{edge.label}</span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {edge.desc}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
