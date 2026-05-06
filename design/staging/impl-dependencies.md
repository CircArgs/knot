# Impl dependencies — candidate models

**Status:** under review — brainstorm. Orchestrator (Nick) will pick one.

## The problem

Bound impls are Python source the team submits via API (commitment 5). Every workflow trigger is a fresh compile and the compile hash is run identity (commitment 3). Audit walk-back depends on the compile hash uniquely identifying a run's view of the world: same hash → same data, same impl bytes, same Config, same emitted edges/facts.

But impl bytes alone aren't a complete view of the world. An impl that writes `import recordlinkage` will, at runtime, link against whatever `recordlinkage` resolves to in the orchestrator runner's Python environment. Six months later, replaying the same compile hash may bind a different `recordlinkage` version (CVE patch, transitive bump, image rebuild) and produce different ER edges. Audit walk-back then *lies*: the system will replay the hash and confidently assert "this is what ran," when the deps soup underneath has shifted.

Two architectural shapes resolve this:
- **Deps enter the compile-hash input set.** Whatever pinning artifact knot decides on (lockfile, hash of resolved versions, content-addressed wheel set) is part of canonical form. Replay determinism is preserved end-to-end.
- **knot explicitly disclaims deps tracking.** Replay determinism is bounded by the team's runtime image — "the hash + the image tag is what replays." Deps stay outside knot's seam; the team owns the image story.

A middle position is possible: track *what was used* (post-hoc record) without making it part of compile identity. That preserves audit visibility without preserving replay determinism. Below: four candidate models spanning these positions.

## Concrete scenarios (Reality Checker)

**S1 — small pure-Python dep.** Alice writes an ER impl: `import recordlinkage; from recordlinkage.compare import String`. She hits "Save" in the browser. knot stores the source bytes, validates DataContext refs, registers. At compile, knot bakes the impl hash + Config snapshot into the WorkflowSpec. At runtime, the orchestrator runner imports `recordlinkage`. Six months pass; knot's host image is rebuilt; `recordlinkage` 0.16.0 → 0.17.2 (algorithm tweak in the Jaro-Winkler comparator). A replay of the original compile hash now produces different blocking candidate scores. The ER edge set diverges from what was originally written to `entity_bindings`. Audit walk-back asserts the old hash but produces new edges — silent corruption of the audit story.

**S2 — heavy dep with native code.** Bob writes a Materializer that uses `torch` + a pinned model file from the lake. `import torch; model = torch.load("s3://.../model_v3.pt")`. The model file path is data — it lives in a Source or in Config — and is already pinned. But `torch` itself: CUDA version, BLAS backend, Python ABI. A replay across image rebuilds (or across runners with different GPUs) silently changes embedding outputs by epsilon — sometimes enough to flip downstream cosine-similarity comparisons. The model artifact is content-addressed; the linker isn't.

**S3 — transitive deps.** Carol's DqRunner writes `import pandas`. `pandas` pulls `numpy`, `python-dateutil`, `pytz`, `tzdata`. A `tzdata` revision changes a historical DST cutover; a freshness check that bins timestamps by local-day produces different bin counts. Carol never imported `tzdata`. The transitive set is the actual surface that affects determinism, and it's not visible in the impl source.

**Common thread:** the impl source is the *visible* part of an iceberg. Whatever determinism story knot makes about the compile hash has to either (a) cover the submerged part, or (b) explicitly carve it out as someone else's problem.

## Prior art (Comparative Anchorer)

| System | How it tracks Python/code deps | What knot can borrow / avoid |
|---|---|---|
| **AWS Lambda** | Deployment package (zip) or container image. Layers can be shared. The image digest is the deploy identity; a function version pins the image. *Known directly.* | Borrow: image digest as a single content-addressed root for the deps set. Avoid: per-function deployment ceremony. |
| **Cloudflare Workers** | Workers have a much narrower runtime; deps are bundled at deploy via wrangler/esbuild. Bundle is content-addressed per version. *Known directly.* | Borrow: bundle-at-submission so the dep graph is captured at the moment author hits Save. Avoid: aggressive runtime restrictions that don't fit Python. |
| **Datomic transaction functions** | Compiled into the database; classpath of the transactor process is the deps set. No per-function pinning. *Inferred.* | Avoid: classpath-of-the-process is exactly the failure mode S1/S2 describe — invisible env drift. |
| **dbt models** | dbt itself doesn't track Python package deps; `dbt-core` runs SQL. Python models (newer) defer to the warehouse's Python runtime (Snowpark, BigQuery, Databricks); the warehouse vendor pins the env. *Known directly for SQL; vendor-pinned for Python.* | Borrow: split — knot pins what it owns (impl source + Config + spec), runtime image is vendor-style pinned outside the seam. Avoid: implicit dependence on a vendor pinning the env *for* you. |
| **Airflow DAGs** | DAGs ship as Python files into the scheduler; deps come from the scheduler/worker image. Recent: per-task `@task.virtualenv` and `@task.external_python` allow per-task envs, lockfile-driven. *Known directly.* | Borrow: per-task / per-impl optional env override, opt-in. Avoid: dual world where some tasks are pinned and most aren't (drift). |
| **Postgres `CREATE FUNCTION ... LANGUAGE plpython3u`** | Untrusted PL/Python; deps come from whatever's installed in the postgres host's Python. No tracking. *Known directly.* | Avoid: this is the worst-case outcome — no pinning, full host trust, and replay-impossible across host upgrades. |
| **Hex / Mode notebooks** | Hex pins the workspace's Python environment (lockfile-managed by the platform); notebook execution is reproducible within the workspace. Mode is similar. *Inferred.* | Borrow: a single workspace-level lockfile rather than per-impl is operationally light and fits single-team posture. |
| **Nix / Bazel rules_python** | Full content-addressed build of the dep closure; replay is bit-identical. *Known directly.* | Borrow: content-addressing the closure as the strongest replay story. Avoid: the operational cost of Nix-grade build infra for a single-team tool. |

The clean split: systems that bundle-at-deploy (Lambda, Workers, Nix) get replay determinism cheaply but pay deployment ceremony. Systems that defer to the host env (Datomic, plpython3u) avoid ceremony but accept env drift. Systems with a workspace-level lockfile (Hex, Airflow with constraints) sit in the middle — single pinning artifact, all impls share it.

## Trust posture lens (Trust Posture Interrogator)

Single-team, trusted-author posture changes the answer significantly. The team operates the orchestrator runner, owns the runtime image, and knows when it changes. Multi-tenant defenses (per-impl sandboxed envs, isolated venvs to prevent dep collisions between hostile authors, supply-chain attestations) don't apply — there's no every-team. Deps drift between revisions of "the team's image" is a coordination problem the team can solve socially (changelog, pre-prod validation) without knot enforcing it.

What's still load-bearing despite trust: **audit walk-back is the pitch.** Trust doesn't dissolve the determinism story — the team trusts itself to write good impls, but it still needs to defend audit conclusions to stakeholders six months later who weren't in the room when the image was rebuilt. Trust collapses the "prevent malice" layers; it does not collapse the "what actually ran" layer. The replay-determinism question is independent of trust posture.

## Candidate models

### Model A: Ignore — knot disclaims deps tracking

- **Mechanism:** Impl source is part of the compile hash. The Python environment is not. The team is responsible for runtime-image stability; knot documents that "replay determinism is bounded by the team's runtime image being stable across the replay window."
- **What enters compile-hash input:** impl source bytes, Config snapshot, spec revisions. Nothing about deps.
- **Replay determinism:** Bounded — same hash + same image tag → same result. Hash alone is insufficient; replay across image rebuilds is undefined.
- **Operational cost:** Zero new mechanism. Team owns image discipline (changelog, version-pinned base image, no `:latest`).
- **Strain on commitments:** Strains commitment 3 ("the hash is the run's identity"). Identity becomes "(hash, image)" without that pair being knot's responsibility. Audit walk-back footnotes "modulo image stability."
- **Strain on goals:** Audit walk-back claim weakens. Stakeholders asking "what produced this fact?" get "this hash, against whatever image was current then" — and image-then may not be reconstructable.

### Model B: Lockfile-as-input — workspace-level lockfile pinned at compile

- **Mechanism:** The team maintains one lockfile (uv.lock / poetry.lock / pip-tools requirements.lock) for the workspace. knot stores it as a first-class artifact in postgres-control with revisions, just like Config. At compile, knot reads the *current* lockfile revision and bakes its content hash into the WorkflowSpec. The orchestrator runner is responsible for materializing an env that matches the lockfile (via `uv sync` against a content-addressed wheel cache, or equivalent) before running impls.
- **What enters compile-hash input:** impl source, Config, spec revisions, **lockfile content hash**.
- **Replay determinism:** Strong if the runner can faithfully recreate the env from the lockfile (deterministic resolver + cached wheels). Bit-identical only with full content-addressing of wheels (Nix-style); near-bit-identical with PEP 508 + hashes.
- **Operational cost:** Team must maintain a lockfile (single artifact, single team — low friction). knot needs an API to upload/edit the lockfile (mirrors Config edit flow). Runner needs lockfile-aware env materialization. Wheel cache infra (S3 bucket of hashed wheels) is one extra piece.
- **Strain on commitments:** Preserves commitment 3 cleanly. Mild new surface in commitment 5 (knot now hosts a lockfile alongside impl source — same shape: editable artifact, content-addressed). Commitment 16's loud-failure rule extends to "lockfile resolution failure raises at compile."
- **Strain on goals:** None significant. The lockfile *is* a spec node in the same sense as Config.

### Model C: Per-impl declared deps — `requires: list[str]` on the impl class

- **Mechanism:** Each impl declares its direct deps as a class-level attribute, e.g., `requires: ClassVar[list[str]] = ["recordlinkage==0.16.0", "pandas>=2.0,<3.0"]`. knot resolves the union of all bound impls' `requires` at compile via a deterministic resolver (uv / pip-compile), produces a per-compile lockfile, hashes it, bakes it. Runner materializes per-compile.
- **What enters compile-hash input:** impl source, Config, spec revisions, **resolved per-compile lockfile hash**.
- **Replay determinism:** Strong (same as Model B), with the added property that deps are colocated with the impl that needs them — tighter affinity between source and its deps.
- **Operational cost:** Per-compile resolution is expensive (resolver runs every trigger), but cacheable on `(set of requires)`. Authors must remember to update `requires` when they add an import — easy to drift between source `import x` and `requires` not listing x. knot can validate via AST scan ("import not in requires → loud failure"), but the validation is non-trivial (transitive imports, import-as-aliases, conditional imports).
- **Strain on commitments:** Preserves commitment 3. Adds a new surface to the DI seam (commitment 4) — a fifth element alongside Protocol/DataContexts/Config/ctx. Commitment 16 must extend to "import-without-requires raises at registration."
- **Strain on goals:** Higher author burden. Multiplies surface area: every impl now has a deps spec that must be kept in sync with its imports.

### Model D: Image-digest input — the team registers the runtime image; the digest enters the hash

- **Mechanism:** The team builds a runtime image (Docker / OCI) containing all deps for all impls. knot stores the image's content-addressed digest (sha256 of the manifest) as a first-class artifact, editable via API (same shape as lockfile in Model B). At compile, knot bakes the *current* registered image digest into the WorkflowSpec. The runner pulls by digest. Image-build is the team's CI concern; knot only knows the digest.
- **What enters compile-hash input:** impl source, Config, spec revisions, **image digest**.
- **Replay determinism:** Strongest — image digest is the actual byte-level truth of what runs. Bit-identical replay (modulo non-determinism in the impls themselves).
- **Operational cost:** Team must operate an image-build pipeline (CI rebuilds image when deps change, pushes to a registry, registers digest with knot). Higher iteration friction than lockfile (image rebuild + push vs lockfile re-resolve). knot's "browser-authored, save → live for next compile" iteration story (commitment 5) doesn't break — impl source still goes live next compile — but adding a *new* dep means the team rebuilds the image first, then the impl can use it.
- **Strain on commitments:** Preserves commitment 3 cleanly. Mild strain on commitment 5's "no separate Python service to deploy" — the image is *not* a service (no deploy of impl source), but it is an external artifact the team must maintain. Defensible: the image is for the orchestrator's runner, not for knot itself.
- **Strain on goals:** Iteration on deps is heavier than iteration on impls/configs. Acceptable if deps churn is rare relative to impl/config churn.

## Comparison table

| Model | Replay-deterministic | Op cost | Commitment strain | Author burden |
|---|---|---|---|---|
| **A: Ignore** | No (bounded by image-then) | Zero | Strains 3 (hash isn't full identity) | Zero |
| **B: Lockfile-as-input** | Strong (with wheel cache) | Low — one lockfile, hosted by knot, edited via API | Minimal — lockfile is a Config-shaped artifact | Low — team maintains one lockfile |
| **C: Per-impl `requires`** | Strong | Medium — resolver runs per-compile, AST validation | Adds a fifth DI-seam element; complicates 4 and 16 | Medium-high — every impl needs `requires` kept in sync with imports |
| **D: Image-digest input** | Strongest (bit-identical) | Medium — team operates image-build CI | Mild on 5 (external artifact, but not a service) | Low for impl edits; medium for dep changes (image rebuild) |

## Open questions for orchestrator

- **What's the audit determinism budget?** "Same hash → same edges across years" (Model B/C/D) vs "same hash + remembered image → same edges" (Model A). The pitch leans toward the former; how strongly?
- **Where does the env materialize?** knot's seam ends at the orchestrator runner. Models B/C imply the runner has lockfile-aware env build (uv sync against a wheel cache); Model D implies the runner pulls an OCI image. Which is the team already running?
- **Wheel cache / image registry — whose infra?** Both B and D require a content-addressed artifact store (wheels for B, OCI registry for D). Is this acceptable new infrastructure, or already present?
- **Iteration cadence for deps vs impls.** Commitment 5 emphasizes browser-save → live-next-compile for impls. How often do deps change relative to impls? (If rare: Model D's image-rebuild-on-dep-change is fine. If frequent: Model B's lockfile edit is friendlier.)
- **Does `requires` (Model C) earn its DI-seam slot?** A fifth class-level attribute on every impl is non-trivial. Is the colocation benefit worth the surface, or is one workspace lockfile (Model B) the right granularity for a single-team tool?
- **Audit-visibility-only middle path.** Should knot record the actually-resolved env post-hoc (in `pipeline_runs` or alongside) even if it doesn't enter the compile hash? That preserves "what actually ran" forensics without committing to replay determinism. Worth carving out as a Model E, or fold into whichever model is picked?
