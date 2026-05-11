# CLAUDE.md — knot

**knot** is an API-first knowledge-graph + ontology platform. Single team,
no tenants. Postgres data plane. The repo is a **monorepo of sibling
services**, each self-contained (code + tests + own pyproject/package.json).

This file is the contract between the codebase and any agent that mutates it.
Read it before making changes.

The previous `design/` tree (architectural commitments, staging docs, personas)
has been removed for a fresh slate. It's preserved in git history and other
branches. The new docs site is being planned in `.knot-docs-plan.md` and
`.knot-docs-infra.md`.

## Posture (load-bearing)

- **Single-team tool, no tenants.** The team that operates knot owns every
  impl, every spec edit, and the lake/graph-store infrastructure. External
  users only enter at three narrow surfaces: read published outputs, query
  via the translator, submit corrections via UI.
- **Trusted authors.** No sandboxing of impls. Full Python power. Defensive
  multi-tenant infrastructure does NOT apply unless explicitly chosen.
- **Knot is a compiler that delegates execution.** No internal SQL engine,
  no internal queue, no internal scheduler. Bound DI impls do all execution.
- **Async-first.** psycopg `AsyncConnection`; async FastAPI routes. The
  compile/metaschema layers stay sync (pure transforms over Pydantic types).
- **Sibling services talk via HTTP only.** `ai/` and `er/` never import
  `knot/`. They call knot's public API. The framework inside `knot/extensions/`
  is a thin shim that POSTs to those services when configured.

## Layout (monorepo)

```
core/                 # knot core Python package (importable as "knot")
  knot/               # the package itself
    api/              # FastAPI + Strawberry GraphQL (graph/, auth/, ai/, lake, spec, dq)
    db/               # SQL execution layer; only this dir touches postgres
    spec/             # Spec model + compilation (compile/postgres, compile/graphql)
    extensions/       # Master dispatcher; HTTP shims to sibling services
    graph/            # Orchestration tier; one function per API operation
    config/           # Settings (KNOT_ env prefix)
  tests/              # unit/ (pure Python, no I/O) + integration/ (real postgres)
  pyproject.toml      # knot core deps and config
ai/                   # AI sibling FastAPI service (knot_ai) on :8001
  knot_ai/            # NL→GraphQL etc., talks to knot via HTTP only
  tests/
  pyproject.toml
er/                   # ER sibling FastAPI service (knot_er) on :8002
  knot_er/            # entity resolution strategies; receives RowsIngesting payloads
  tests/
  pyproject.toml
ui/                   # React + Vite + React Flow + Apollo (pnpm)
  src/
  package.json
notebooks/            # marimo demo notebooks
scripts/              # docker-compose helpers (up.sh, down.sh, wait-ready.sh)
docker-compose.yml    # Local postgres for dev + tests
```

## Boundary rules

- **Execute SQL → `core/knot/db/`.** Build SQL from models →
  `core/knot/spec/compile/`. No crosstalk between the two for compilation.
  Metadata reads (e.g., schema name from settings) are tolerable.
- **Configurable names → `core/knot/config/`.** No hardcoded schema names,
  table names, or role names anywhere else.
- **Spec → DB direction only.** `core/knot/db/` may import typed metadata
  from `core/knot/spec/`. `core/knot/spec/compile/` must NOT import from
  `core/knot/db/`.
- **Siblings never import knot.** `ai/`, `er/`, `ui/` talk via HTTP.
  `core/knot/extensions/` may register HTTP-delegating shims (e.g., calls
  the `er/` service on `RowsIngesting` events when `KNOT_ER_URL` is set).

## Workflow

```bash
# Bring up postgres
./scripts/up.sh

# Install knot core in the root .venv (only needed once / after deps change)
.venv/bin/pip install -e ./core

# Unit tests — fast, no docker required
.venv/bin/pytest core/tests/unit/ -q

# Full suite (requires docker-compose postgres up)
cd core && KNOT_DEV_MODE=1 ../.venv/bin/pytest tests/ -q

# Lint + format + types
.venv/bin/ruff check core/knot/ core/tests/
.venv/bin/ruff format core/knot/ core/tests/
.venv/bin/mypy core/knot/

# Sibling services (each self-contained)
cd ai && pip install -e . && uvicorn knot_ai.main:app --port 8001
cd er && pip install -e . && uvicorn knot_er.main:app --port 8002
cd ui && pnpm install && pnpm dev   # :5173, proxies to all three

# Tear down
./scripts/down.sh
```

`KNOT_DEV_MODE=1` is required for tests — the fail-closed DSN guard falls
back to the docker-compose default only when this is set. It also implies
`KNOT_AUTH_DEV_MODE=1`, so mutation routes (`POST /spec/drafts/...`,
`POST /graph/ingest/...`, `POST /graph/corrections`) bypass auth in the
same single-env-var dev stack.

## Conventions (apply proactively)

- **Real Pydantic types over discriminator strings.** Class-based discrimination
  + real enums. Strings are for data, not structural shape.
- **Walk the typed entity tree directly via single-dispatch.** Don't build
  parallel meta-structures.
- **Interrogate every named entity.** "Is this an actual thing or just a label
  for a bundle of existing things?"
- **No v0/v1/future-work framing.** Either commit to a design or explicitly
  mark it open with the question stated.
- **Baby-step + ELI5 pacing.** One self-contained step per turn; wait for
  confirmation. Long structured walkthroughs are a smell.
- **Comparative anchoring.** When proposing architecture, name 2-3 comparators
  (dbt, DataJunction, LinkML, SHACL, OWL, Splink, Atlas, RDF, Neo4j) and
  explicitly position. If we're reinventing, earn the cost.
- **Cull claims that don't earn their cost.** Don't preserve existing
  decisions out of inertia.

## What NOT to do

- Don't reach for sandboxing or multi-tenant defenses — trust posture is
  single-team.
- Don't use v0/v1/future-work framing as a deferral.
- Don't build parallel meta-structures when the typed entity tree carries
  the data.
- Don't introduce new named entities without interrogating whether they
  earn their place.
- Don't reach into `design/` — it's gone. The new docs site (planned in
  `.knot-docs-plan.md` and `.knot-docs-infra.md`) is the replacement.

## Auto-memory

`~/.claude/projects/-mnt-main-code-knot/memory/` — `feedback_design_thinking_style.md`
condenses the patterns above for proactive application.
