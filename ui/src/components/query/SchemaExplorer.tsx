/**
 * Collapsible left sidebar that renders the introspected schema as a tree.
 *
 * Two top-level groups:
 *   - Query: root fields on the Query type (most often what you actually want)
 *   - Types: every named object type
 *
 * Clicking a field inserts its name at the parent's caret via `onInsert`.
 *
 * We walk the raw introspection JSON because it's easier to recurse than the
 * built `GraphQLSchema` object — the JSON has no methods, just plain shapes.
 */
import { useMemo, useState } from "react";
import type { IntrospectionQuery } from "graphql";

interface Props {
  introspection: IntrospectionQuery | null;
  onInsert: (text: string) => void;
}

interface NamedField {
  name: string;
  typeLabel: string;
  args: { name: string; typeLabel: string }[];
  description: string | null;
}

interface NamedObjectType {
  name: string;
  description: string | null;
  fields: NamedField[];
}

/** Render a possibly-wrapped (NON_NULL / LIST) type ref as a short label. */
function refLabel(ref: unknown): string {
  if (!ref || typeof ref !== "object") return "?";
  const r = ref as { kind?: string; name?: string | null; ofType?: unknown };
  if (r.kind === "NON_NULL") return `${refLabel(r.ofType)}!`;
  if (r.kind === "LIST") return `[${refLabel(r.ofType)}]`;
  return r.name ?? "?";
}

function pickObjectTypes(intro: IntrospectionQuery): NamedObjectType[] {
  const out: NamedObjectType[] = [];
  for (const t of intro.__schema.types) {
    if (t.kind !== "OBJECT") continue;
    if (t.name.startsWith("__")) continue; // skip meta types
    const fields: NamedField[] = (t.fields ?? []).map((f) => ({
      name: f.name,
      typeLabel: refLabel(f.type),
      args: (f.args ?? []).map((a) => ({
        name: a.name,
        typeLabel: refLabel(a.type),
      })),
      description: f.description ?? null,
    }));
    out.push({ name: t.name, description: t.description ?? null, fields });
  }
  out.sort((a, b) => a.name.localeCompare(b.name));
  return out;
}

function queryRoot(intro: IntrospectionQuery): NamedObjectType | null {
  const rootName = intro.__schema.queryType.name;
  for (const t of intro.__schema.types) {
    if (t.kind === "OBJECT" && t.name === rootName) {
      return {
        name: t.name,
        description: t.description ?? null,
        fields: (t.fields ?? []).map((f) => ({
          name: f.name,
          typeLabel: refLabel(f.type),
          args: (f.args ?? []).map((a) => ({
            name: a.name,
            typeLabel: refLabel(a.type),
          })),
          description: f.description ?? null,
        })),
      };
    }
  }
  return null;
}

export default function SchemaExplorer({ introspection, onInsert }: Props) {
  const [queryOpen, setQueryOpen] = useState(true);
  const [typesOpen, setTypesOpen] = useState(false);
  const [openTypes, setOpenTypes] = useState<Record<string, boolean>>({});

  const root = useMemo(
    () => (introspection ? queryRoot(introspection) : null),
    [introspection],
  );
  const types = useMemo(
    () => (introspection ? pickObjectTypes(introspection) : []),
    [introspection],
  );

  if (!introspection) {
    return (
      <div className="p-3 text-sm text-knot-muted">
        Loading schema…
      </div>
    );
  }

  return (
    <div className="p-2 text-sm overflow-auto h-full">
      <Section
        label="Query"
        open={queryOpen}
        onToggle={() => setQueryOpen((v) => !v)}
      >
        {root?.fields.map((f) => (
          <FieldRow key={f.name} field={f} onInsert={onInsert} />
        ))}
      </Section>

      <Section
        label="Types"
        open={typesOpen}
        onToggle={() => setTypesOpen((v) => !v)}
      >
        {types.map((t) => (
          <div key={t.name} className="ml-2">
            <button
              type="button"
              className="text-left w-full hover:bg-slate-100 rounded px-1 py-0.5"
              onClick={() =>
                setOpenTypes((m) => ({ ...m, [t.name]: !m[t.name] }))
              }
            >
              <span className="text-knot-muted mr-1">
                {openTypes[t.name] ? "▾" : "▸"}
              </span>
              <span className="font-medium">{t.name}</span>
            </button>
            {openTypes[t.name] && (
              <div className="ml-4 border-l border-slate-200 pl-2">
                {t.fields.map((f) => (
                  <FieldRow key={f.name} field={f} onInsert={onInsert} />
                ))}
              </div>
            )}
          </div>
        ))}
      </Section>
    </div>
  );
}

function Section({
  label,
  open,
  onToggle,
  children,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-3">
      <button
        type="button"
        className="text-left w-full hover:bg-slate-100 rounded px-1 py-0.5 font-semibold"
        onClick={onToggle}
      >
        <span className="text-knot-muted mr-1">{open ? "▾" : "▸"}</span>
        {label}
      </button>
      {open && <div className="ml-2 mt-1">{children}</div>}
    </div>
  );
}

function FieldRow({
  field,
  onInsert,
}: {
  field: NamedField;
  onInsert: (text: string) => void;
}) {
  return (
    <button
      type="button"
      title={field.description ?? undefined}
      className="text-left w-full hover:bg-slate-100 rounded px-1 py-0.5 flex items-baseline gap-1"
      onClick={() => onInsert(field.name)}
    >
      <span className="text-knot-accent">{field.name}</span>
      {field.args.length > 0 && (
        <span className="text-knot-muted text-xs">
          ({field.args.map((a) => a.name).join(", ")})
        </span>
      )}
      <span className="text-knot-muted text-xs ml-auto">{field.typeLabel}</span>
    </button>
  );
}
