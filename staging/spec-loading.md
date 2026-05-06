# Spec loading at boot — design question

Status: open

## Current state

`knot/api.py` loads the spec at import time from `tests.fixtures.B2.spec`.
This is a development hack; the module docstring marks it as a TODO.

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
