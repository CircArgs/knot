# Cross-class pinning: relation classes pin parent run hashes

**Status:** staging — captured for review, not yet integrated into authoritative docs. Per handoff Decision 2.

How knot keeps relation-class workflows reproducible when their parent classes are independently scheduled.

## The problem

Some ontology classes are *relation classes* — their slots' ranges reference other ontology classes:

- `Credit` references `Movie` (work) + `Person` (person).
- `Identifier` references whatever entity it points at (`entity_class` + `entity_src_key`).
- `ActedIn` references `Movie` + `Person`.

When `resolve:Credit` runs, it binds source rows to canonical_ids and references parent canonical_ids (Movie + Person). This requires the parent canonical_ids to be **stable for the duration of Credit's run** — otherwise Credit's bindings point at canonical_ids that have shifted.

But Movie and Person each have their own per-class trigger and reschedule independently. If Movie reruns mid-way through Credit's run, "current" Movie canonical_ids may differ from what Credit started with.

## The solution: pin parent run hashes at compile time

When a relation class's workflow is compiled, the parent classes' specific run hashes are pinned into the compiled `WorkflowSpec`. Credit reads parent canonical_ids "as of" those runs — never "current."

### What gets pinned

The compiled spec for a relation class carries:

```yaml
class: Credit
pinned_parent_runs:
  Movie:
    run_hash: 9876ab...
    run_id: <pipeline_runs.id>
    completed_at: 2026-04-30T18:22:11Z
  Person:
    run_hash: 0000zz...
    run_id: <pipeline_runs.id>
    completed_at: 2026-04-30T17:05:42Z
```

The compiled task params for `resolve:Credit` include `endpoint_class_runs: {Movie: hash_X, Person: hash_Y}`. The bound ER impl receives these and reads parent canonical_ids "as of" those run hashes.

### Default and override

**Default at compile time:** most-recent-successful run of each parent class.

```
POST /runs/Credit
→ knot picks: Movie's latest succeeded run + Person's latest succeeded run.
→ pins those into Credit's compiled spec.
→ submits to orchestrator.
```

**Override:** caller specifies parent runs explicitly.

```
POST /runs/Credit?parents={Movie:hash_X,Person:hash_Y}
→ knot pins those exact runs.
→ useful for replay, A/B comparisons, debugging.
```

Both produce different compiled hashes — `pinned_parent_runs` is part of the canonical form (per `compiled-workflow-hashing.md`).

## How parent-pinned reads work

When the bound ER impl reads parent canonical_ids "as of" a pinned run, it queries the parent class's bindings at that historical point. Mechanically:

- Iceberg-backed lake → snapshot read at the snapshot corresponding to the pinned run.
- Parquet-on-disk → partition path keyed by run hash.
- Postgres-backed → version-keyed table or temporal join.

The bound `QueryReader` impl (per `query-executor.md`) executes whatever SQL knot generates against parent-class data at the pinned hash. Knot's seam is unchanged — same protocol, just resolved against the historical view rather than current.

## Identifying relation classes

A class participates in cross-class pinning when its spec has slots whose `range` is another ontology class — it structurally references other entities. The structural reference graph (per `impact-analysis.md`) computes the slot-range edges; knot walks them at compile time to find parent classes.

Compile-time: knot walks the class's outgoing `slot.range -> class` edges to enumerate parent classes; for each, the latest-successful run hash is looked up (or the override-supplied hash is used) and pinned.

## ER dependency graph and topological order

Cross-class pinning handles structural parent dependencies (relation classes referencing parents). For deciding the *overall* per-class execution order — including non-relation classes that use other classes' canonical_ids as ER signals — knot computes an **ER dependency graph** and topologically sorts it.

**The toposort is over the entire ontology graph**, not just classes with ER pipelines. Classes without ER pipelines (single-source-only, pure-enum, derived-only, reference-only) occupy their topological position as no-ops — they don't run anything but they maintain a uniform ordering model. This avoids a special case where some classes are "in the graph for toposort" and others aren't.

The graph has two edge types:

- **Structural dependencies (mandatory)** — a relation class references parent classes via reference patterns. Example: `Credit → Movie`, `Credit → Person`. Edges come from knot's `compute_spec_reference_graph` (slot.range → class, reference_pattern → class, etc.; per `impact-analysis.md`). Cross-class pinning enforces these at compile time.
- **Strategic dependencies (optional, declared via impl config)** — an ER impl's config declares it uses another class's canonical_ids as a matching signal. Example: Movie's ER impl says "use Identifier facts as a strong-evidence signal" → `Movie → Identifier`. Edges come from the ER impl's `cross_references` config field (per `di-input-contract.md`).

Union of both = the ER dependency graph. **Topological sort** gives a valid execution order; classes with no incoming edges go first.

For a Movie / Person / Identifier / Credit ontology:

```
Identifier      (no deps — clusters on (system, value) alone)
    ↓
Movie, Person   (parallelizable; both depend on Identifier strategically)
    ↓
Credit          (depends on Movie + Person structurally; pins their run hashes)
```

Execution order: Identifier → {Movie, Person} (parallel) → Credit.

### Cycles are spec errors

If two classes' ER strategies form a cycle (Movie's strategy uses A; A's strategy uses Movie), knot rejects the spec at compile time with a clear error: "ER strategy dependency cycle: Movie → A → Movie." Either redesign the strategies to break the cycle or accept that one shouldn't use the other as a signal. **No fixed-point iteration; no runtime loop.**

### Implications for scheduling

- **Meta-workflow rebuilds** (rebuild whole KG): classes triggered in topological order, with cross-class pinning at each step (each downstream class pins the upstream classes' just-completed run hashes).
- **Independent per-class triggers** (`POST /runs/Movie`): knot pins the latest-successful run hashes of any classes Movie depends on (structural or strategic). User can override via `?parents=...`.

The graph is computed at compile time from the spec reference graph (per `impact-analysis.md`) + ER strategy configs (which declare strategic dependencies). knot extends the spec-internal graph with the strategy-driven edges and toposorts the union.

## Merge propagation across dependent classes

When an upstream class has merges or splits between runs, the dependent (downstream) ER pipeline picks them up cleanly through the cross-class pinning + SCD2 entity_bindings + canonical_id_lineage machinery. The bound DI ER impl never has to know about merge history — knot's runtime serves it post-merge state at the pinned hash.

Walk:

```
T1: Identifier run A — produces id_X, id_Y (separate identifiers)
T2: Movie run M1 — pins Identifier run A; sees id_X and id_Y as different;
                   binds movies based on (signal includes id_X != id_Y)
T3: Identifier run B — auto-merges id_X + id_Y into id_X (id_Y retired,
                       canonical_id_lineage records the merge)
T4: Movie run M2 — fresh compile pins Identifier run B; sees id_X as the
                   merged identifier; rows that previously referenced id_Y
                   now resolve through id_X via SCD2 entity_bindings;
                   Movie ER's matching evidence shifts (Movie rows that
                   previously didn't share an identifier now do via the
                   merged id_X).
```

What the DI ER impl sees at T4: queries Identifier canonical_ids "as of pinned Identifier run B." The SCD2 `entity_bindings` (and `canonical_id_lineage` redirects) automatically resolve any references to retired `id_Y` → `id_X`. **Clean inputs at the pinned hash; no merge-history awareness needed.**

What changes for dependent ER's clustering:

- **M1's bindings** (compiled against Identifier run A) remain valid historical artifacts. Audit walk-back through M1 still works: M1 → pinned Identifier run A → `id_X` and `id_Y` were separate at A.
- **M2's bindings** (compiled against Identifier run B) reflect the merged world.
- Some Movie rows in M2 may bind to a different canonical_id than they did in M1 — that's handled by Movie's own post-processing (the conflict / split / merge cases already designed in `er-and-storage.md`).

What changes for audit walk-back:

- "Why does this Movie binding reference `id_X`?" → walk to Movie compile spec → pinned Identifier run hash B → query Identifier `entity_bindings` and `canonical_id_lineage` at B.
- The lineage row for `id_X` (absorbed `id_Y` at run B) is part of the audit chain. Full provenance: Movie binding → Identifier merge → original source contributions to `id_X` and `id_Y`.

**Composition of layers.** The DI ER impl + cross-class pinning + SCD2 bindings + lineage all compose. The DI doesn't need to know about the latter three — knot's runtime serves bindings transparently through the SCD2 + lineage layer; the DI just reads canonical_ids at the pinned hash and trusts they're correct.

## Reproducibility

Replay of any compiled relation-class spec produces identical output (modulo the bound impl's determinism), because:

- Spec revisions are pinned (already, via content-addressing).
- Parent run hashes are pinned (this doc).
- Source data is read at a stable point (lake watermarks; the team's `source-layer-contract` responsibility).

A Credit compile hash uniquely identifies a Credit run's view of the world; re-dispatching the same hash produces the same bindings.

## Audit walk-back

Any Credit canonical_id traces back through:

1. The Credit run that produced it (`pipeline_runs` row).
2. The Credit compile hash → the compiled spec.
3. The compiled spec's `pinned_parent_runs` → Movie run hash + Person run hash.
4. Those parent runs' bindings → the specific Movie / Person canonical_ids that Credit used.

Mechanical, deterministic, walks across the parent-class boundary.

## Architectural rules that follow

1. **Relation-class workflows are not standalone.** They depend on parent runs. Compiling a relation-class workflow without resolved parents is an error (no parent run available to pin).
2. **Cross-class scheduling is the orchestrator's job.** Knot tells the orchestrator "Credit needs Movie + Person to be done first"; the orchestrator handles waiting, retries, fan-in. Knot doesn't compose multi-class workflows.
3. **Re-running a parent doesn't invalidate child runs.** Old Credit runs still reference their pinned Movie hash and remain valid historical artifacts. New default-triggered Credit runs pick up the latest Movie hash and produce a new compile hash.
4. **Pinned runs become part of the audit chain.** Anything querying Credit's canonical_ids can walk back through the pinned run hashes to see exactly which parent state was used.

## What this changes vs. earlier framing

`compiled-workflow-hashing.md` previously framed cross-class dependencies as "the orchestrator's job":

> Cross-class dependencies (e.g., `ActedIn` relations referencing `Movie` ids) are handled at the orchestrator level, not by knot. The orchestrator schedules `Movie` first, waits for it to complete, then triggers `ActedIn`. Knot doesn't compose multi-class workflows; the orchestrator does.

This document refines that. The orchestrator still does the scheduling, but knot's compiler now emits relation-class workflows with **explicit `pinned_parent_runs`** in their compiled specs. The orchestrator's job is to dispatch the compiled spec; knot's compiler determines which parent runs to pin.

## Update to authoritative docs (proposed)

- `compiled-workflow-hashing.md` — add `pinned_parent_runs` to the canonical form description; add the `?parents=` query parameter to the API surface; refine the "orchestrator handles cross-class" paragraph to reflect this split.
- `pipeline-stages.md` — add a "parent-run resolution" step at the front of relation-class workflows in the flow diagram.

These are amendments to locked docs and should be reviewed before merging.
