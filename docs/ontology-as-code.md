# Ontology as Code: Why UI-Driven Ontology Editing Is an Antipattern

A position paper. The argument applies whether we use knot or any other schema layer.

---

## TL;DR

Ontology changes should happen in code, in git, through PRs. UI-driven ontology editing is an antipattern. The argument is **not** that schema-as-code has no friction — it does. The argument is that **the friction buys you something you can't get any other way**, and the friction you'd avoid by going UI-driven is mostly imaginary because schema changes already coincide with code changes you'd be doing anyway.

---

## What "Ontology in the UI" Usually Means

A web interface where authorized users can:

- Add a new entity class
- Add a slot (typed property) to an existing class
- Define a relation between two classes
- Add a constraint or validation rule
- Promote a virtual class to a concrete class (or vice versa)
- Rename or remove any of the above

The argument for it: "the team can move faster without engineering involvement." The argument against it is everything below.

---

## 1. The Friction Is Load-Bearing

Schema-as-code has friction. You edit a Python file (or LinkML YAML, or whatever), open a PR, get a review, run CI, deploy. That's a workflow with multiple gates.

That workflow gives you, for free:

- **Auditability.** Git history tells you who changed the ontology, when, and why (commit message + PR description).
- **Reviewability.** PRs force at least one other person to look at the change. Catches the "this rename has downstream consequences nobody noticed" class of mistakes.
- **Rollback.** `git revert` puts the ontology back to a known-good state in seconds. UI-driven changes need an undo system the UI builders have to design and maintain.
- **Branching.** You can develop the next domain's ontology on a branch without affecting production.
- **Testing.** CI runs against the proposed schema before it lands. Failed constraint checks, broken downstream contracts, dimensional mismatches — caught pre-deploy.
- **Diff.** "What changed between Tuesday and today?" is a `git diff`. With UI changes, you need a custom changelog system, and it never captures the full context (commit message, linked tickets, PR review threads).

A UI-driven system has to reimplement all of these from scratch, badly, with no underlying primitive (git) doing the heavy lifting.

---

## 2. The Friction You'd Avoid Is Mostly Imaginary

The pitch for UI-driven ontology editing is "non-engineers can make schema changes without going through engineering." Let's look at what actually drives schema changes:

### Case A: New domain or new source

Most ontology changes are driven by integrating a new source or a new domain. Adding "podcasts" as a class because Netflix is now publishing podcasts. Adding a "music\_track" relation because we licensed a music catalog.

These changes require:
- Writing ingest code for the new source
- Writing transformations from source schema to canonical schema
- Writing matching rules for the new entities
- Possibly writing new ML pipelines (genre prediction, embedding generation)
- Configuring monitoring, alerting, dashboards

The ontology change is a few-line addition in a much larger body of engineering work that **already requires PRs, reviews, deploys**. Pushing the ontology change through the same workflow adds zero marginal friction — the workflow is happening anyway.

### Case B: Slot addition to existing class

"We want to track production budget on movies." Easy. Add a slot.

But: does the existing data have this value? Where does it come from? Which sources? What's the transformation? Does any downstream system need to be told it exists? Does the API contract need to expose it?

A UI that lets someone click "add slot" without answering those questions creates ghost slots — declared in the schema, never populated, polluting the resolved view, confusing downstream consumers. The PR workflow forces those questions.

### Case C: Renames and refactors

Renaming a slot is dangerous. Every downstream consumer (queries, dashboards, ML features, API clients) that references the old name breaks.

In code, you can grep for usages, refactor in a single atomic commit, and reviewers can verify completeness. In a UI, the rename is one click and the explosion is delayed until something tries to read the old name.

### Case D: Constraint changes

Changing a constraint (tightening a check, adding a required field) can invalidate existing data. The change needs to be staged: deploy the constraint as a warning first, identify violations, backfill or fix, then promote to error.

This is a multi-step deploy with verification at each step. PRs naturally express this. UI clicks don't.

---

## 3. The "Schema Lives With Code" Principle

A core insight: **the code that uses the schema and the schema itself need to be deployed in coordinated, atomic units.**

If the ontology says "every Movie now has a `production_budget` slot" but the ingest code hasn't been updated to populate it, you have a broken contract. The resolved view returns NULL where downstream consumers expected a value.

If the ontology removes a slot but the API server still references it, the server crashes.

In a schema-as-code world, schema and code ship together — the same PR, the same deploy, the same rollback. In a UI-driven world, schema changes happen out-of-band, and you need separate sync mechanisms to keep code in step (or the code is forever lagging and broken).

---

## 4. Who Actually Wants UI-Driven Ontology?

This is worth interrogating directly.

- **Product managers / non-engineers who want autonomy.** Real, but the autonomy they actually want is usually different — they want to be able to *propose* changes without writing code. That's a different problem; solve it with templates, an "ontology proposal" form that opens a PR, or a markdown-based RFC process.
- **Staff engineers selling a platform.** A UI looks impressive in a roadmap. It demos well. It is not the same as being useful.
- **Teams under deadline pressure.** "We just need to add this slot before Monday." If you've designed the ingest, deploy, and rollback workflows correctly, a PR-driven slot addition takes minutes, not days. The pressure is a signal that the workflow has too many gates, not that the gates should be removed.

---

## 5. What to Do Instead

If the goal is to lower the activation energy for non-engineers to propose ontology changes:

1. **An RFC template.** A markdown file in `docs/ontology-proposals/` that anyone can copy and fill out. Engineers convert approved RFCs into actual schema PRs.
2. **An "ontology proposal" PR generator.** A small UI that collects "I want to add slot X of type Y to class Z because R" and opens a draft PR. Engineer reviews, edits if needed, lands it.
3. **Better docs.** A clear written process for "how do I add a slot." Most of the perceived friction is actually unfamiliarity.
4. **Pair sessions.** When a non-engineer needs an ontology change, an engineer sits with them and walks through the PR together. Builds shared context, takes 20 minutes.

None of these require building a UI that owns the schema. All of them preserve git as the source of truth.

---

## 6. The Argument From Precedent

Every mature system in this space has converged on schema-as-code:

- LinkML: YAML schemas in git
- Protocol Buffers: `.proto` files in git, generated code committed
- GraphQL: SDL files in git, generated types committed
- Apache Iceberg: schema evolution via DDL committed to migration files
- Netflix's own Upper metamodel: built on git-tracked artifacts

Companies that tried UI-driven schema management at scale (early Datameer, some MDM products) ended up adding "schema export to git" as the only workable rollback story. The UI became a fancy form for generating PRs.

---

## 7. The Carve-Out

There are a few cases where UI editing of *some* metadata is fine:

- **Trust weights.** These are runtime tuning parameters, not schema. Editing them via UI (or admin CLI) is appropriate — they need to be changed without a deploy.
- **Source priority overrides.** Same reasoning: operator tuning, not schema.
- **Filter/projection saved queries.** User-defined views on top of the schema, not the schema itself.
- **User-facing labels and translations.** Display metadata that the system tolerates losing.

The line: **if a change requires regenerating code, regenerating downstream contracts, or running a migration, it's schema. Schema lives in git.**

---

## 8. Recommendation

Ontology stays as typed code (knot spec, LinkML, or equivalent) in the main repo. Changes go through PRs. The "lower the activation energy" problem is solved with templates, RFC processes, and pairing — not by moving schema ownership into a UI.

If a UI is built, scope it strictly to runtime tuning (trust weights, projections, labels). It must not be able to change class definitions, slot definitions, relation definitions, or constraints.

When this question comes up in design review, the rebuttal is one sentence: **"Show me the rollback story."**
