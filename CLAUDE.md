# CLAUDE.md — knot

**knot** is an API-first knowledge-graph + ontology platform. Single team,
no tenants. Postgres data plane. Python implementation in `knot/`.

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

## Layout

```
knot/                 # Python package
  api/                # FastAPI + Strawberry GraphQL (graph/, auth/, lake, spec, dq)
  db/                 # SQL execution layer; only this dir touches postgres
  spec/               # Spec model + compilation (compile/postgres, compile/graphql)
  extensions/         # Master dispatcher + builtin ER extension
  graph/              # Cross-cutting graph helpers (resolve, corrections)
  config/             # Settings (KNOT_ env prefix)
tests/                # Split into unit/ (pure Python, no I/O) and
                      # integration/ (real postgres via docker-compose);
                      # both subdirs mirror the knot/ package layout
scripts/              # up.sh / down.sh / wait-ready.sh
docker-compose.yml    # Local postgres for dev + tests
```

## Boundary rules

- **Execute SQL → `knot/db/`.** Build SQL from models → `knot/spec/compile/`.
  No crosstalk between the two for compilation. Metadata reads (e.g., schema
  name from settings) are tolerable.
- **Configurable names → `knot/config/`.** No hardcoded schema names, table
  names, or role names anywhere else.
- **Spec → DB direction only.** `knot/db/` may import typed metadata from
  `knot/spec/`. `knot/spec/compile/` must NOT import from `knot/db/`.

## Workflow

```bash
# Bring up postgres
./scripts/up.sh

# Unit tests — fast, no docker required
.venv/bin/pytest tests/unit/ -q

# Full suite (requires docker-compose postgres up)
KNOT_DEV_MODE=1 .venv/bin/pytest tests/ -q

# Lint + format + types
.venv/bin/ruff check knot/ tests/
.venv/bin/ruff format knot/ tests/
.venv/bin/mypy knot/

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
