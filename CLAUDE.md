# CLAUDE.md — knot library

**knot** is a reflective ontology compiler — a pure library that takes a
typed Pydantic spec (classes, properties, sources, source-bindings,
constraints) and emits the runtime artifacts (postgres DDL + GraphQL
schema + Pydantic row validators + constraint SQL).

This branch (`library/v0`) is the focused library. Anything that talks
to a connection, serves HTTP, or holds runtime state lives outside the
library — in a *reference adapter* a team builds around it. The earlier
monorepo (API service + UI + ingest + corrections + ER + AI) is in git
history on the `draft-rfc` and `main` branches.

Two ground-truth docs:
- `RFC.md` — what knot is, framed for a consumer / new teammate.
- `LIBRARY_DESIGN.md` — what knot is, framed for an implementer:
  phased plan, file-by-file map, test strategy, Java port track.

## Posture

- **Pure library.** No FastAPI, no HTTP, no `psycopg.connect`, no
  ingest path. The library returns `sql.Composable`, `strawberry.Schema`,
  `BaseModel` subclasses; the host runs them.
- **No runtime config.** Names like the postgres schema (`knot_data`)
  and the synthetic corrections source (`_user_corrections`) are
  module-level constants in `knot.spec.compile.postgres._naming`. A host
  that needs a non-default schema rebinds the constants before importing
  the emitters; there is no env-var indirection.
- **Single team posture survives.** Trusted authors of the spec, no
  multi-tenant defenses, no sandboxing.
- **Async-aware but not async-only.** Compiler is sync (pure transforms).
  Resolvers emitted into the GraphQL schema are async because Strawberry
  expects that.

## Layout

```
core/                                  # the library package
  pyproject.toml                       # core deps only (pydantic, jcs,
                                       # sqlglot, psycopg, strawberry)
  knot/
    __version__.py
    __init__.py                        # public re-exports
    spec/
      metaschema.py                    # Spec + OntologyClass + DefinedClass + Slot + ...
      canonical.py                     # canonical_dump, compute_content_hash
      serialization.py                 # spec_to_dict / spec_from_dict
      effective_slots.py               # effective_slots, is_stored, stored_slot_names
      effective_constraints.py         # effective_constraints
      errors.py                        # PublishGateError, ...
      expressions.py                   # ExprTree + translate_expr
      sql_validate.py                  # sqlglot-validated constraint SQL
      primitives.py                    # STANDARD_PRIMITIVE_NAMES
      compile/
        postgres/                      # DDL + predicate + order-by + relation
          __init__.py                  # public compile_* functions
          _context.py                  # CompileContext
          _dispatch.py                 # single-dispatch over expression tree
          _naming.py                   # SCHEMA + USER_CORRECTIONS_SOURCE constants
          _types.py                    # slot_pg_type
          _queries.py                  # select_with_binding / _with_derivations
          _predicate.py                # WHERE-fragment compiler
          _relation.py                 # ClassRef / Array traversal SQL
          migration.py                 # Change types + diff_specs + DDL emitters
          lake.py                      # lake-side compile helpers
        graphql/
          __init__.py                  # build schema entry point
        validators/
          __init__.py
          row_models.py                # Pydantic row-model factories
  tests/
    unit/                              # pure unit tests (no I/O)
RFC.md                                 # consumer-facing pitch + walkthrough
LIBRARY_DESIGN.md                      # implementer-facing handoff
assets/                                # images referenced by RFC
```

## Workflow

```bash
# Install in the root .venv (editable; library only — no service deps).
.venv/bin/pip install -e ./core

# Smoke-test the public surface.
.venv/bin/python -c "
from knot.spec import Spec, OntologyClass, DefinedClass, Slot
from knot.spec.compile.postgres import compile_constraint
from knot.spec.compile.graphql import get_or_build_schema
from knot.spec.compile.validators import build_row_model
print('library import surface ok')
"

# Unit tests (pure, no I/O).
.venv/bin/pytest core/tests/unit/ -q

# Lint + types.
.venv/bin/ruff check core/knot/ core/tests/
.venv/bin/ruff format core/knot/ core/tests/
.venv/bin/mypy core/knot/
```

There is no docker-compose, no uvicorn, no UI on this branch. Integration
tests that need postgres live with a reference adapter (separate repo or
`examples/` subdir, TBD).

## Boundary rules

- **No I/O in the library.** Anything in `knot/` that calls
  `psycopg.connect`, `await conn.execute`, or otherwise talks to a
  resource is a bug. The library *returns* `sql.Composable` and parameter
  lists; the host runs them.
- **No env-var indirection.** Per-deployment knobs are
  module-level constants (rebindable by the host before import) or
  function parameters, never `os.environ.get`.
- **Spec → compile direction only.** `knot/spec/compile/` may import
  from `knot/spec/` but `knot/spec/` must not import from
  `knot/spec/compile/`. The metaschema is upstream of every emitter.
- **No sibling services.** `ai/` and `er/` are gone from this branch.
  Compatible external services can be wired by a reference adapter,
  not by the library.

## Conventions (apply proactively)

- **Real Pydantic types over discriminator strings.** Real enums, real
  class-based discrimination. Strings are for data, not structural shape.
- **Walk the typed entity tree via single-dispatch.** No parallel meta
  structures.
- **Interrogate every named entity.** Is this an actual thing or a label
  for a bundle of existing things?
- **No v0/v1/future-work framing.** Either commit to a design or
  explicitly state the open question.
- **Comparative anchoring when proposing architecture.** Name 2-3
  comparators (LinkML, DataJunction, dbt, RDF+SHACL, Foundry's ontology
  layer) and explicitly position.
- **Cull claims that don't earn their cost.** No inertia commits.

## What NOT to do

- Don't add a service, a router, or anything that mounts an HTTP route.
  This branch is a library.
- Don't reach for env-var config. Constants are rebindable; that's enough.
- Don't reintroduce `knot.db`, `knot.api`, `knot.graph`, or
  `knot.extensions` modules. Those moved to history.
- Don't introduce new named metaschema entities without interrogating
  whether they earn their place.

## Auto-memory

`~/.claude/projects/-mnt-main-code-knot/memory/MEMORY.md` —
`feedback_design_thinking_style.md` condenses the patterns above for
proactive application.
