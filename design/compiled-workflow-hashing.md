# Compiled workflows are content-addressed

**Status:** authoritative. This is the canonical statement of how knot resolves spec revisions, identifies workflow runs, and supports reproducible reruns.

**Date locked:** 2026-04-29.

**See also:** [`knot-as-compiler.md`](knot-as-compiler.md) — establishes that knot compiles `WorkflowSpec`s and dispatches them. This document specifies how those compiled specs are stored, identified, and replayed.

---

## The statement

> Every workflow trigger is a fresh compile. The compiler reads the *current* revisions of every spec node in the compiled workflow's dependency graph, emits a `WorkflowSpec` with revision IDs baked in, hashes the canonical form, and stores it. **The hash is the run's identity.**
>
> Reruns reference the hash, not the trigger. The orchestrator dispatches the stored compiled spec; the runner invokes the bound impls with the pinned revisions; the result is identical to the original run modulo the team's impl determinism.
>
> Spec edits between triggers produce a different hash. Identical state between triggers produces the same hash; runs deduplicate against existing compiled specs.

This is the standard pattern from build systems and pipeline engines: Bazel's action graphs, Nix derivations, dbt manifests, Terraform plans. Knot's compiled `WorkflowSpec`s sit at the same architectural layer.

## Why this beats both naive options

The two naive alternatives were:

- **Pin at compile time, store the pin separately, recompile on edits.** Reproducible reruns, but requires an explicit "recompile and resubmit" loop after every spec edit. Drift between "what's been compiled" and "what's currently active" becomes a real concern.
- **Read latest at task runtime.** Edits propagate immediately. But yesterday's rerun uses today's specs — the audit walk-back becomes "approximately what was active," not "exactly what ran." The whole audit story is the pitch; this breaks it.

Content-addressing gets both:
- **Fresh compile on every trigger** — no recompile-and-resubmit loop. The latest specs go into the new compile automatically.
- **Pinned execution** — the compile pins all revisions at the moment of trigger, before dispatch.
- **Reproducible reruns** — fetch by hash; the immutable artifact replays exactly.
- **Free deduplication** — same input = same hash; identical reruns share storage.

## The flow

1. **Trigger.** A run is requested for some target (typically an ontology class — see [Per-class granularity](#per-class-granularity-the-default-unit) below).
2. **Compile.** Knot reads the current revision of every spec node in the compiled workflow's dependency graph. Constructs a `WorkflowSpec` with all revision IDs and DAG structure baked in. Canonicalizes (sorted keys, deterministic JSON). Computes `sha256` of the canonical form.
3. **Store-or-dedupe.** If the hash exists in `compiled_workflows`, skip insert. Otherwise insert the new compiled spec.
4. **Dispatch.** Submit the compiled spec to the orchestrator. Record a `pipeline_runs` row pointing at the compiled hash and the orchestrator's run id.
5. **Run.** Orchestrator runs each task in the compiled DAG. Tasks invoke the runner with `handler_ref` + `params`; the runner asks the registry for the bound impl and calls it. Pinned revision IDs travel with each task as audit metadata; the runner can fetch *exactly that* revision of any referenced spec node when the impl needs it.
6. **Complete.** Orchestrator reports terminal status; knot updates the run record.
7. **Audit walk-back.** Any fact in `resolved_facts` traces back through its `pipeline_runs` row → `compiled_workflows.hash` → the stored `WorkflowSpec` → every revision ID baked in → the actual specs as they were at compile time.

## Storage shape

Two postgres tables on the control plane:

```sql
CREATE TABLE compiled_workflows (
    hash             CHAR(64) PRIMARY KEY,          -- sha256 of canonical-form spec
    spec             JSONB    NOT NULL,             -- the full WorkflowSpec
    target_kind      TEXT     NOT NULL,             -- e.g. 'ontology_class'
    target_name      TEXT     NOT NULL,             -- e.g. 'Movie'
    first_compiled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- denormalized revision refs for query convenience:
    pinned_revisions JSONB    NOT NULL              -- {"ontology:Movie": "rev_a1", ...}
);

CREATE INDEX compiled_workflows_target ON compiled_workflows (target_kind, target_name);

CREATE TABLE pipeline_runs (
    id                    UUID PRIMARY KEY,
    compiled_spec_hash    CHAR(64) NOT NULL REFERENCES compiled_workflows(hash),
    orchestrator_run_id   TEXT,                      -- the orchestrator's own ID
    triggered_at          TIMESTAMPTZ NOT NULL,
    triggered_by          TEXT NOT NULL,             -- user id
    status                TEXT NOT NULL,             -- queued | running | succeeded | failed
    finished_at           TIMESTAMPTZ,
    error                 TEXT
);

CREATE INDEX pipeline_runs_hash ON pipeline_runs (compiled_spec_hash);
```

A run *is* `(triggered_at, hash, orchestrator_run_id)`. The run record is small; the spec record is the heavy thing and is shared across reruns.

## Canonical form (for hashing)

The `WorkflowSpec` must be canonicalized before hashing so trivial reorderings don't produce different hashes. Canonicalization rules:

- JSON serialization with **sorted keys** at every nesting level
- **No insignificant whitespace** (single-line JSON, or pretty-printed with deterministic indentation)
- **Sorted task list** by `task.name`
- **Sorted `depends_on`** within each task
- **No timestamps, run ids, or compile-time metadata** in the hashed payload — only the spec itself (DAG structure, handler refs, params, pinned revision IDs)
- **`pinned_parent_runs`** for relation classes (classes whose slots' ranges reference other ontology classes) — the parent classes' run hashes pinned at compile time so the relation class reads parent canonical_ids "as of" specific historical runs. See [`staging/cross-class-pinning.md`](staging/cross-class-pinning.md).

The canonical form is deterministic and pure: same spec graph at the same revisions always produces the same bytes, always produces the same hash.

`compile_at` and `compile_id` go in the run record, not the hashed payload.

## API surface

```
POST  /runs/{target}              # fresh compile + dispatch
                                  # response: { run_id, compiled_spec_hash, orchestrator_run_id }
                                  # the hash may already exist (deduplicated) or be new

POST  /runs/{target}?parents=...  # fresh compile + dispatch with explicit parent runs pinned
                                  # query: parents={ParentClass: <run_hash>, ...}
                                  # only meaningful for relation classes; default = latest-successful
                                  # produces a different compile hash than the default for the same target

POST  /runs/replay/{hash}         # re-dispatch existing compiled spec — no recompile
                                  # response: { run_id, compiled_spec_hash, orchestrator_run_id }

GET   /workflows/{hash}           # inspect a compiled spec
                                  # response: full WorkflowSpec + pinned_revisions

GET   /workflows/{hash}/runs      # all runs that used this hash
                                  # response: list of pipeline_runs

GET   /runs/{run_id}              # inspect a single run
                                  # response: full run record + the compiled spec it ran
```

## Per-class granularity (the default unit)

The default trigger unit is **one ontology class**. `POST /runs/Movie` compiles a workflow that produces resolved `Movie` entities — pulling in every source contributing to Movie, the bound ER impl + its config, the bound DQ checks (built-in + custom), and bound materialization impls.

Why per-class:
- ER must see all sources for a class together (cross-source matching is the whole point of resolve)
- Merge operates within one class
- Validate / publish are per-class

Cross-class dependencies (e.g., `ActedIn` relations referencing `Movie` ids) are split: the **orchestrator** handles scheduling (waits for `Movie` to complete before triggering `ActedIn`), and **knot's compiler** bakes the specific parent run hashes into `ActedIn`'s compiled spec via `pinned_parent_runs` (see [`staging/cross-class-pinning.md`](staging/cross-class-pinning.md)). The relation-class workflow reads parent canonical_ids "as of" the pinned runs — never "current." Same separation as everywhere else: knot owns the spec (including which parent runs to pin); the orchestrator owns the runtime scheduling.

If a team needs broader composition ("rebuild the whole media graph"), they author it as orchestrator-level meta-workflows that trigger N per-class workflows in order. Knot's API gives them the `POST /runs/{class}` and `GET /runs/{run_id}` primitives; the orchestrator handles the loop.

## Deduplication semantics

Two runs of the same target with no spec edits in between produce the same hash. They are stored as **two rows in `pipeline_runs`** referencing **one row in `compiled_workflows`**. Both runs execute fully — they don't skip dispatch — because the *data* the workflow operates on may have changed (new rows landed in the source layer between the two triggers; teams may want to capture that).

If a team wants to skip the dispatch when nothing has changed, that's a separate optimization (compare hash + most-recent-run timestamps + source-layer watermark; if all equal, return the prior run's results without re-dispatching). Not part of this contract; the default is "every trigger dispatches."

## Retention

`compiled_workflows` rows are **retained indefinitely**. There is no GC; there is no retention knob. Audit walk-back depends on every prior compile being dereferenceable — dropping a compiled spec would silently break the walk-back for any fact produced by a run that referenced it. `pipeline_runs` rows accumulate faster but reference `compiled_workflows` via a foreign key, so dropping old run records does not cascade-delete the compiled specs.

See `staging/incremental-execution.md` § "What's NOT in scope" (cache entries retained forever) and `staging/cross-class-pinning.md` (relation-class audit depends on pinned parent hashes remaining dereferenceable).

## Implications for the boundary

- **Knot stores compiled specs in postgres-control.** This is metadata, not data plane. The audit walk-back lives in knot's spec layer.
- **The orchestrator stores its own run state.** Maestro / Airflow / Argo each track their workflow runs natively. Knot's `pipeline_runs.orchestrator_run_id` is the cross-reference.
- **The runner reads pinned revisions, not "current."** When the runner needs a spec node (e.g., the ER strategy's threshold), it fetches the *pinned* revision baked into the task, not the current one. This is how "rerun from yesterday's hash uses yesterday's strategy" actually works.

## Architectural rules that follow from this

1. **No spec is read at runtime by name only.** Always by name + revision. The pinned revision lives in the task params; runners fetch by `(name, revision)`.
2. **Compiled specs are immutable once stored.** They are content-addressed; mutating one would invalidate its hash. Edits produce new specs with new hashes; old specs remain.
3. **The compiler is pure.** Same input (spec node revisions + class network) → same output (`WorkflowSpec`). No randomness, no timestamps, no environment dependency. Otherwise hashing is meaningless.
4. **Replay is a first-class operation.** `POST /runs/replay/{hash}` is not a debugging affordance — it is the contract for reproducibility.
