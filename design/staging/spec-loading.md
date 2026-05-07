# Spec loading at boot — design question

**Status: decided.** Postgres-backed `spec_revisions` (option 1).

## Decision

`spec_revisions` table in postgres-control: `(revision SERIAL PK, spec
JSONB, content_hash, active BOOLEAN, created_at)`.  Exactly one row at a
time has `active = TRUE` (enforced by partial unique index).  Boot-time:
`knot.api._get_spec()` calls `spec_store.load_active()`; on first boot
with no rows, `spec_store.seed_from_fixture()` writes B2 as revision 1.

`POST /sources` resolves entity_class + identifier_slot names to real
refs against the active spec, constructs a `Source` Pydantic, builds a
new `Spec` instance with the source appended, and writes a new revision
(deactivating the prior active row).

`spec_store.spec_to_dict` flattens real Python refs to name-strings at
the persistence boundary; `spec_store.spec_from_dict` does a two-pass
parse (build entities indexed by name, then resolve cross-refs to real
objects) per `spec-model.md` § "Persistence boundary".  After load,
`spec.sources[0].entity_class is spec.classes[0]` is `True`.

## Current state (closed)

Previously: `knot/api.py` loaded the spec at import time from
`tests.fixtures.B2.spec`.  That hack is gone.

## Options

1. **Postgres control table** (`spec_revisions`): boot queries the DB for the
   active spec revision. Canonical, auditable, matches the single-source-of-truth
   model. Adds a DB dependency at startup.

2. **Filesystem path** (`KNOT_SPEC_DIR` env var): api reads a spec file from a
   well-known path baked into the deployment artifact. Simple, no DB at startup,
   but couples spec versioning to deploy cycle.

3. **Python module import** (current B2 hack): works for local dev and tests,
   breaks for any deployed instance that doesn't have the fixture on its path.

## Single-team posture

No multi-tenant considerations. One team, one active spec at a time.
The right answer is likely option 1 (postgres `spec_revisions` table) —
consistent with how impl revisions and configs are already stored —
but option 2 is acceptable for a purely filesystem-deployed setup.

## Next step

Design the `spec_revisions` table and a `GET /spec/active` → load path,
or decide on `KNOT_SPEC_DIR` and document the convention. Separate slice.
