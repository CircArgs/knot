import { useEffect, useState } from "react";

interface Cls { name: string; abstract: boolean; properties: string[]; }

export default function Home() {
  const [classes, setClasses] = useState<Cls[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch("/spec/published/classes")
      .then(r => r.json())
      .then(setClasses)
      .catch(e => setError(String(e)));
  }, []);
  if (error) return <div className="p-4 text-red-600">Error: {error}</div>;
  if (!classes) return <div className="p-4">Loading…</div>;
  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold mb-3">knot — published classes</h1>
      <ul className="space-y-2">
        {classes.map(c => (
          <li key={c.name} className="border rounded p-3">
            <div className="font-medium">{c.name} {c.abstract && <span className="text-xs">abstract</span>}</div>
            <div className="text-sm text-gray-600">{c.properties.join(", ") || "(no slots)"}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}
