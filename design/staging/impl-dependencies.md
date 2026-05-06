# Impl dependencies — not knot's problem

**Status:** decided.

Bound impls are Python source the team submits via API (commitment 5). They `import` libraries (pandas, recordlinkage, torch). Those libraries are installed in the orchestrator runner's Python environment.

**knot does not track, pin, hash, or manage Python library versions.** The team owns its runtime environment. Adding a new library is a deployment of the runtime image — the same operation the team already does to ship knot itself. There is no in-knot lockfile, no postgres-stored deps artifact, no editable-via-API dependency manifest, no compile-hash input for deps, no per-impl `requires` declaration.

Replay determinism is bounded by runtime stability. "Same compile hash → same edges" holds for the lifetime of the deployed runtime. A deployment that changes deps is a new world; replay across that boundary is undefined and the team's release notes are the audit trail.

This is the answer. There is no design question here.
