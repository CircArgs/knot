/**
 * Tiny fetch wrappers for the REST surface of knot core.
 *
 * The Vite dev server proxies these paths to `localhost:8000`. In
 * prod, the UI is served behind the same origin as the API so the
 * relative paths just work.
 */

export interface PublishedClass {
  name: string;
  abstract: boolean;
  slots: string[];
}

export async function getPublishedClasses(): Promise<PublishedClass[]> {
  const r = await fetch("/spec/published/classes");
  if (!r.ok) throw new Error(`/spec/published/classes ${r.status}`);
  return (await r.json()) as PublishedClass[];
}

export async function getHealth(): Promise<{ status: string }> {
  const r = await fetch("/healthz");
  if (!r.ok) throw new Error(`/healthz ${r.status}`);
  return (await r.json()) as { status: string };
}
