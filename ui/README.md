# knot-ui

React + Vite + TypeScript front-end for knot. Sketch only — proves the
proxy + layout + Apollo wiring. Real components (spec graph, ingest
console, correction UI) land in later commits.

## Install + run

```bash
cd ui
pnpm install
pnpm dev          # http://localhost:5173
```

`pnpm dev` proxies (configured in `vite.config.ts`):

| Path        | Upstream                |
| ----------- | ----------------------- |
| `/spec`     | `http://localhost:8000` |
| `/graph`    | `http://localhost:8000` |
| `/healthz`  | `http://localhost:8000` |
| `/auth`     | `http://localhost:8000` |
| `/ai`       | `http://localhost:8001` |
| `/er`       | `http://localhost:8002` |

So the UI expects knot core at `:8000`, knot-ai at `:8001`, knot-er at
`:8002`.

## Type-check + build

```bash
pnpm typecheck
pnpm build
```

## Module map

- `src/main.tsx` — entrypoint, mounts `<App>` inside Apollo + Router.
- `src/App.tsx` — top-level routes.
- `src/components/Layout.tsx` — shell with top nav.
- `src/pages/Home.tsx` — lists published classes via `/spec/published/classes`.
- `src/pages/SpecGraph.tsx` — placeholder for the @xyflow/react graph view.
- `src/graphql/client.ts` — Apollo Client pointed at `/spec/graphql`.
- `src/api/knot.ts` — tiny fetch wrappers for the REST surface.

## Why pnpm

Lockfile (`pnpm-lock.yaml`) is committed; `node_modules/` is not.
`packageManager` is pinned in `package.json`.

## Not in this commit

- shadcn/ui — added later when real components arrive.
- Tests — added once there's behavior worth testing beyond the smoke
  build.
- Auth — once the auth seam stabilizes in knot core.
