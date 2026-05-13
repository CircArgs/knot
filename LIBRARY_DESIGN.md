# knot — library design + handoff

This is the implementer's contract for the knot library. The RFC
(`RFC.md`) is the consumer pitch; this doc is the build plan.

If you are a new contributor (human or agent) picking this up cold,
read this end-to-end before writing code. The branch is already
shaped — most of what's left is structural cleanup, splitting two
mixed-purpose files, and writing tests + a reference adapter.

---

## What knot the library is

**A pure spec compiler.** Typed-spec-in, runtime-artifacts-out, no I/O,
no service. Given a `Spec` (Pydantic-typed value), the library emits:

- **Postgres DDL** — full schema-creation for a fresh spec, plus
  incremental migration SQL for a previous → candidate spec diff.
- **GraphQL schema** — Strawberry types projected from the spec
  (concrete classes, defined classes, where-inputs, order-by inputs,
  forward + back-edge resolvers, DataLoader-aware batching).
- **Pydantic row validators** — one `BaseModel` subclass per source
  binding for ingest-time payload validation.
- **Constraint SQL** — sqlglot-validated predicates compiled to
  violation-shape `SELECT`s, with constraint inheritance through the
  `is_a` and mixin chains, against the per-class binding tables.
- **Publish-gate evaluation** *(planned split — see Phase A below)* —
  pure spec-graph checks (cycle detection, reference resolution,
  mixin collision, identifier-slot reachability) returned as a
  structured result; data-revalidation queries returned as a list of
  SQL fragments the host runs against current data.

## What knot the library is NOT

- Not a service. No FastAPI, no HTTP, no auth, no router.
- Not a database client. No `psycopg.connect`, no `await conn.execute`.
- Not an ingest pipeline. The library emits the validator that an
  ingest path uses.
- Not a query runtime. It emits the Strawberry schema; the host serves
  it (and provides per-request context, DataLoaders, open connections).
- Not a draft persistence layer. Drafts are in-memory `Spec` values;
  the host persists them however it wants.
- Not an opinionated migration applier. It returns SQL + revalidation
  queries; the host applies them on its own cadence with its own
  tooling (Alembic, Liquibase, Flyway, custom).
- Not the trust / ER / corrections / materialization layer. Those are
  separate concerns layered on top.

## Current state (as of this branch)

The `library/v0` branch has the library code lifted from the monorepo
and everything else deleted. What's already present and tested:

| Area                                      | State                | File(s)                                                                    |
|---|---|---|
| Metaschema (types + serialization)        | ✅ done              | `core/knot/spec/{metaschema,canonical,serialization,errors,primitives}.py` |
| `effective_slots` + `effective_constraints` walkers | ✅ done       | `core/knot/spec/effective_*.py`                                            |
| sqlglot-validated constraint compiler     | ✅ done              | `core/knot/spec/sql_validate.py`                                           |
| Expression tree + single-dispatch         | ✅ done              | `core/knot/spec/expressions.py`                                            |
| Postgres compile context + naming         | ✅ done              | `core/knot/spec/compile/postgres/{_context,_naming,_types}.py`             |
| Predicate / relation / queries compilers  | ✅ done              | `core/knot/spec/compile/postgres/{_predicate,_relation,_queries}.py`       |
| Diff types + DDL emitters (mixed)         | ⚠️ done but mixed     | `core/knot/spec/compile/postgres/migration.py` *(see Phase A)*             |
| GraphQL schema + resolvers + DataLoader   | ✅ done              | `core/knot/spec/compile/graphql/__init__.py` *(monolithic; see Phase B)*   |
| Pydantic row validators                   | ✅ done              | `core/knot/spec/compile/validators/row_models.py`                          |
| Publish gate (pure half)                  | 🟡 lift from history | was in `core/knot/db/spec_store.py::publish_gate`; needs Phase C lift      |
| Revalidation query builder                | 🟡 lift from history | was implicit in `spec_store.preview_publish`; needs Phase C lift           |
| Reference adapter                         | ⛔ not built          | Phase D                                                                    |
| Java port                                 | ⛔ not started        | Phase E (parallel track)                                                   |
| Golden tests                              | ⛔ not built          | needed for Phase B + cross-track parity                                    |

Unit tests pass (`pytest core/tests/unit/ -q` → 119 passing). Integration
tests were deleted with the rest of the runtime; they belong with the
reference adapter when it exists.

## Target public API surface

```python
# knot/__init__.py — once Phases A-C land

from knot.spec import (
    Spec, OntologyClass, DefinedClass, Slot,
    Source, SourceBinding, SlotMapping, Constraint,
    Primitive, Array, ClassRef, TypeExpression,
    Severity, ResolutionPolicy, NullSemantics,
    effective_slots, effective_constraints,
    canonical_dump, compute_content_hash,
    spec_to_dict, spec_from_dict,
)

from knot.diff import (
    diff_specs, is_destructive,
    Change,
    AddClass, DropClass, RenameClass,
    AddSlot, DropSlot, RenameSlot, ChangeSlotType, ChangeSlotConstraints,
    AddSource, DropSource,
    AddSourceBinding, DropSourceBinding,
    AddConstraint, DropConstraint,
    ChangeConstraintBody, ChangeConstraintPrimary, ChangeConstraintSeverity,
)

from knot.gate import (
    publish_gate,                 # raises PublishGateError on spec-graph failure
    build_revalidation_queries,   # → [RevalidationQuery(name, class_name, sql, params)]
)

from knot.compile.postgres import (
    emit_initial_ddl,             # Spec → sql.Composable
    emit_migration_ddl,           # list[Change] → sql.Composable
    compile_constraint,           # Constraint + cls → (sql.Composable, list)
    compile_predicate,            # graphql where + oc → (sql.Composable | None, list)
    compile_order_by,             # graphql order_by + oc → (sql.Composable | None, list)
)

from knot.compile.graphql import (
    build_schema,                 # Spec → (strawberry.Schema, types_by_name, oc_by_name)
)

from knot.compile.validators import (
    build_row_model,              # SourceBinding → type[BaseModel]
    build_row_model_for_class,    # OntologyClass → type[BaseModel] (corrections)
    build_value_model_for_slot,
)
```

Terminology note: the current code uses `Slot` (LinkML lineage). The
RFC uses "property" for external readability. The library keeps `Slot`
internal because `property` shadows a Python builtin and a prior
mass-rename collapsed too many call sites and was reverted. A docstring
glossary in `metaschema.py` bridges the external/internal gap.

## Remaining phases

Each phase ends with: green tests, a public re-export in
`knot/__init__.py`, a CHANGELOG entry, a single PR.

### Phase A — split `migration.py` into `diff/` + `compile/postgres/ddl.py`

`core/knot/spec/compile/postgres/migration.py` mixes two concerns:

1. **Diff types + classifier** — `Change` subclasses, `diff_specs()`,
   `is_destructive()`. Pure: spec-in, list-of-Change-out.
2. **DDL emitters** — per-Change postgres DDL emission, plus full-spec
   initial DDL.

Split into:

- `knot/diff/changes.py` — Change base + all subclasses.
- `knot/diff/__init__.py` — `diff_specs`, `is_destructive`.
- `knot/spec/compile/postgres/ddl.py` — `emit_initial_ddl(spec)`,
  `emit_migration_ddl(changes)` and any per-Change DDL helpers.

The diff layer must import nothing from `knot/spec/compile/postgres/`
(or psycopg). The DDL emitters import diff types, not the other way.

Acceptance:
- `pytest core/tests/unit/spec/compile/test_migration_diff.py` passes
- `pytest core/tests/unit/spec/compile/test_migration_field_diffs.py` passes
- No circular import between `knot.diff` and `knot.compile`
- A golden test of `emit_initial_ddl(NETFLIX_DEMO_SPEC)` snapshots the
  full DDL to a file (acceptable to update only via deliberate review)

### Phase B — split `compile/graphql/__init__.py`

It's a single ~1300-line module today. Split for legibility:

- `knot/spec/compile/graphql/types.py` — `_make_class_object_type`,
  `_class_where_fields`, `_make_class_where_type`,
  `_make_field_enum`, `_make_order_by_input`, `_make_aggregate_result_type`.
- `knot/spec/compile/graphql/resolvers.py` —
  `_make_classref_resolver`, `_make_classref_array_resolver`,
  `_make_reverse_classref_resolver`, `_make_classref_dataloader`,
  `_compute_back_edges`, `_back_edge_field_name`.
- `knot/spec/compile/graphql/walkers.py` — `_all_slots`,
  `_topo_sort_by_classref`, `_merge_contributions`, `_row_to_typed`.
- `knot/spec/compile/graphql/__init__.py` — only `_build_schema`,
  `_SchemaBundle`, `get_or_build_schema`, `build_request_context`,
  plus the public re-exports.

This is mechanical refactor; no behavior change. Run the import smoke
test after each move.

Acceptance:
- `from knot.spec.compile.graphql import get_or_build_schema, build_request_context` works
- A new unit test introspects `get_or_build_schema(NETFLIX_DEMO_SPEC, hash)`
  and asserts type / field shape (root fields per concrete class,
  back-edges, where + order-by inputs)

### Phase C — `publish_gate` + revalidation query builder

This code was deleted with `core/knot/db/spec_store.py` (too entangled
with DB lifecycle). Lift just the relevant parts from
`draft-rfc@b5b9b8d:core/knot/db/spec_store.py` and shape them as pure
functions.

- `knot/gate/spec_graph.py::publish_gate(spec) -> None` — pure checks:
  - mixin cycle detection (`_detect_mixin_cycle`)
  - mixin slot collisions (`_detect_mixin_slot_collision`)
  - reference resolution (every ClassRef target on spec, every
    `SourceBinding.class_`, every `Constraint.primary`)
  - identifier_slot reachable via `effective_slots()` from the
    binding's class
  - Raises `PublishGateError` with structured errors list.

- `knot/gate/revalidation.py::build_revalidation_queries(prev, candidate)
  -> list[RevalidationQuery]` — for each new/changed constraint in
  the candidate, build the SQL the host should run against current
  data. `RevalidationQuery` is a dataclass `(constraint_name,
  class_name, sql: sql.Composable, params: list)`.

The host's publish flow becomes:

```python
publish_gate(candidate)
queries = build_revalidation_queries(prev, candidate)
for q in queries:
    rows = await conn.execute(q.sql, q.params)
    if rows:
        raise ConstraintViolation(q.constraint_name, q.class_name, rows)
migration_sql = emit_migration_ddl(diff_specs(prev, candidate))
await conn.execute(migration_sql)
```

Acceptance:
- Unit tests for each spec-graph check (cycle, collision, reference,
  identifier_slot) using synthetic specs
- A unit test that asserts revalidation queries are generated for
  added + body-changed + severity-changed constraints, not for
  identity-equal ones (hash-skip)

### Phase D — reference adapter

A separate package or `examples/reference_service/` that demonstrates
the wiring:

- Draft persistence (sqlite or in-memory)
- Publish flow (`publish_gate` → `build_revalidation_queries` →
  `emit_migration_ddl` → apply)
- Ingest endpoint that uses `build_row_model(binding)`
- GraphQL endpoint that calls `get_or_build_schema(spec, hash)` +
  `build_request_context(hash, conn)` per request
- Optional: lift the React UI from history (`draft-rfc` branch) and
  wire it against the adapter's endpoints

Goal: a team can copy `examples/reference_service/` and have a working
ontology platform in ~200 lines of glue. The adapter is *example
material*, not the library's contract — teams write their own.

Acceptance:
- Adapter starts, the Netflix demo spec publishes against it, ingest
  + query + corrections endpoints work end-to-end
- Integration tests against the adapter assert the library's outputs
  are correct against a real postgres

### Phase E — Java port (parallel track)

Same library shape, ported to JVM. Branch `library-java/v0`. See
"Java port plan" below.

Goal: knot's compiler available in both Python and JVM ecosystems,
with golden tests (input spec → expected SQL / GraphQL output) shared
between them so the implementations don't drift.

Independent of phases A-D for development; bring online once Python's
Phase A (diff) and the initial-DDL emitter are stable enough to serve
as the golden-test reference.

## Java port plan

Mostly mechanical. The architecture survives the port; the dependency
swaps are well-trodden.

| Python                              | Java equivalent                                  | Notes |
|---|---|---|
| Pydantic typed Spec                 | Java 21 sealed interfaces + records (or Kotlin sealed data classes) | Jackson `@JsonTypeInfo` + `@JsonSubTypes` for discriminated unions |
| Bean Validation on records          | JSR-380 annotations + constructor invariants     | Anything stricter than the record contract |
| `sqlglot` for SQL parse + rewrite   | **Apache Calcite**                               | Strictly more capable — full relational algebra, rule-based rewrites, dialect-aware emission |
| `psycopg.sql.Composable`            | `StringBuilder` + `PreparedStatement`            | Or jOOQ for type-safe DDL emission |
| `strawberry-graphql` types          | `graphql-java` types (or Spring GraphQL)         | `GraphQLObjectType` / `GraphQLInputObjectType` / `DataFetcher` |
| Per-request DataLoader bundle       | `org.dataloader.DataLoader` (graphql-java)       | First-class integration |

Phase shape (parallel to Python):

- **J1** — Spec model in Java + Jackson round-trip + canonical hash
  (use same canonicalisation rule as `jcs` so both implementations
  produce identical hashes for the same Spec)
- **J2** — Diff types + `diff_specs` port
- **J3** — Calcite-based predicate / constraint rewrite + DDL emitter
- **J4** — graphql-java schema construction
- **J5** — Pydantic-equivalent row validators (Jackson + record
  factories, or static codegen via JavaPoet)
- **J6** — `publish_gate` + revalidation query builder
- **J7** — JVM reference adapter (Spring Boot)

Effort estimate: ~6-9 focused weeks for one senior engineer to reach
parity with the Python library.

## Golden test contract (cross-track)

The Python and Java libraries must produce **byte-identical** outputs
for the same input spec. The golden test suite is the contract.

Layout:

```
golden/
  specs/                       # canonical input specs (JSON dumps via spec_to_dict)
    netflix.json
    minimal.json
    with_defined_class.json
    with_constraint_inheritance.json
    ...
  expected/
    netflix/
      initial.sql              # emit_initial_ddl output
      schema.graphql           # build_schema → SDL print
      constraint_year_plausible.sql
      ...
    minimal/
      ...
```

Both Python and Java test suites read `golden/specs/<name>.json`,
generate output, compare against `golden/expected/<name>/<artefact>`,
fail on mismatch. Updates to expected/ require deliberate review.

This is also the migration-safety net for the library itself — any
internal refactor that changes output gets caught by the diff.

## Test strategy

Three layers:

1. **Unit (pure)** — every emitter, walker, diff helper gets a
   focused test. Synthetic-spec-in, expected-output-out. No postgres,
   no FastAPI, no async I/O. Runs in <5s.

2. **Golden** — `golden/` suite described above. Byte-equality of
   emitted DDL / GraphQL SDL / constraint SQL against checked-in
   snapshots, exercised by both Python and Java suites.

3. **Integration (in reference adapter)** — adapter boots postgres,
   applies emitted DDL, runs emitted constraint SQL, executes
   GraphQL queries end-to-end. Not part of the library's own suite.

Current `core/tests/unit/` (119 passing) covers the spec types,
canonicalisation, effective walkers, sql_validate, migration diff,
and basic SQL compilation. Phase A adds DDL golden tests; Phase B
adds GraphQL introspection tests; Phase C adds publish-gate +
revalidation tests.

## Open decisions for the implementer

- **Naming `Slot` vs `Property`.** Keep `Slot` internal. Document the
  external/internal mapping in `metaschema.py`'s module docstring.
  Reject a wholesale rename — it broke last time.
- **Library packaging.** `core/pyproject.toml` declares `knot` with
  core deps only (pydantic, jcs, sqlglot, psycopg, strawberry).
  Strawberry + psycopg are core deps (not extras) because the library
  *returns* types from those packages; consumers need them at import
  time.
- **Versioning.** SemVer. Start at `0.1.0`. Bump minor for any
  change to the public emission shape (DDL, GraphQL schema, validator
  signatures) — those changes affect downstream golden snapshots.
- **Python version.** 3.11+ in the pyproject; the existing code uses
  PEP 749 lazy annotations and modern union syntax. Keep that going.
- **Where the reference adapter lives.** Either a sibling
  `examples/reference_service/` directory on this branch or a separate
  repo. Recommendation: separate repo (clean dependency direction,
  the library doesn't even know the adapter exists).

## How a new agent / contributor starts

1. Read `RFC.md` end-to-end (consumer pitch + walkthrough).
2. Read this doc end-to-end.
3. Skim `core/knot/spec/metaschema.py` to internalise the typed
   entity tree.
4. Verify the baseline: `pip install -e ./core && pytest
   core/tests/unit/ -q` should return 119 passing.
5. Run the import smoke test in `CLAUDE.md`.
6. Pick a phase from "Remaining phases" above. Open a PR per phase.

When in doubt, the rule is: **if it touches a connection or holds
runtime state, it is not in the library.**
