import { useLocalStorage } from "../lib/useLocalStorage";

/**
 * Floating color-key for the spec graph. Overlays the React Flow canvas in the
 * top-right corner. Collapsible — click the header to toggle. Expansion state
 * persists in localStorage.
 *
 * The spec graph shows one node shape (Class) and three edge kinds. Slots,
 * sources, and constraints live inline inside the class card rather than as
 * standalone nodes — a slot is a property of the class table, not a
 * separately-joinable entity.
 */

interface EdgeRow {
  label: string;
  stroke: string;
  strokeDasharray?: string;
  desc: string;
}

const EDGE_ROWS: EdgeRow[] = [
  {
    label: "ClassRef",
    stroke: "#2563eb",
    desc: "slot whose type is another class",
  },
  {
    label: "is_a",
    stroke: "#475569",
    desc: "class inherits from another class",
  },
  {
    label: "mixin",
    stroke: "#94a3b8",
    strokeDasharray: "4 3",
    desc: "class composes a mixin",
  },
];

export default function Legend() {
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
                <li className="flex items-center gap-2 text-slate-800">
                  <span className="inline-block w-3 h-3 rounded border bg-white border-slate-300" />
                  <span className="font-medium w-[72px]">Class</span>
                  <span className="font-mono text-[10px] text-slate-500">
                    (card; slots inline)
                  </span>
                </li>
                <li className="flex items-center gap-2 text-slate-800">
                  <span
                    className="inline-block w-3 h-3 bg-white border-violet-400"
                    style={{
                      clipPath:
                        "polygon(3px 0%, calc(100% - 3px) 0%, 100% 3px, 100% calc(100% - 3px), calc(100% - 3px) 100%, 3px 100%, 0% calc(100% - 3px), 0% 3px)",
                      border: "1px solid",
                    }}
                  />
                  <span className="font-medium w-[72px]">Junction</span>
                  <span className="font-mono text-[10px] text-slate-500">
                    (class with ≥2 ClassRef slots — reified relation)
                  </span>
                </li>
              </ul>
            </div>
            <div className="px-3 py-2 border-t border-slate-200">
              <div className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                Edges
              </div>
              <ul className="space-y-1">
                {EDGE_ROWS.map((edge) => (
                  <li
                    key={edge.label}
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
