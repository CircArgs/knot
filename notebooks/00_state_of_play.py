"""knot — state of play notebook.

Reactive snapshot of where the project is right now. Scroll through;
cells re-run on dependency changes. Updated as work progresses.
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import pandas as pd
    import yaml
    from pathlib import Path

    REPO = Path("/mnt/main/code/knot")
    return REPO, Path, mo, pd, yaml


@app.cell
def _(mo):
    mo.md(
        r"""
        # knot — state of play

        **Repository:** [`CircArgs/knot`](https://github.com/CircArgs/knot) (private)

        **Classification:** a reflective ontology compiler — spec edits validate
        against the running system that interprets them.

        **Posture:** single-team tool (no tenants). External users at three
        narrow surfaces only: read published outputs, query via translator,
        submit corrections via UI.

        Each section below is a live cell — re-runs when its inputs change.
        """
    )
    return


@app.cell
def _(REPO, mo):
    goals_md = (REPO / "design/goals.md").read_text()
    lines = goals_md.splitlines()
    # Pull the "What knot is" section
    start = next(i for i, l in enumerate(lines) if l.startswith("## What knot is"))
    end = next(
        i for i, l in enumerate(lines[start + 1 :], start + 1) if l.startswith("## ")
    )
    what_section = "\n".join(lines[start:end])

    mo.md(f"---\n\n{what_section}")
    return


@app.cell
def _(REPO, mo, pd):
    core = (REPO / "design/core-design.md").read_text()
    commits = []
    for line in core.splitlines():
        if line.startswith("## "):
            head = line[3:].strip()
            if head and head[0].isdigit():
                # "1. Knot is a compiler." → split number + body
                parts = head.split(".", 1)
                if len(parts) == 2 and parts[0].isdigit():
                    commits.append(
                        {"#": int(parts[0]), "commitment": parts[1].strip()}
                    )
    df = pd.DataFrame(commits).set_index("#")
    mo.vstack(
        [
            mo.md("## 17 architectural commitments"),
            mo.ui.table(df, page_size=20, selection=None),
            mo.md(
                "_Each is load-bearing — remove or invert and knot becomes a "
                "different system. Full prose at `design/core-design.md`._"
            ),
        ]
    )
    return


@app.cell
def _(mo):
    layers = [
        ("1 — Why knot exists", "complete"),
        ("2 — Architectural commitments", "complete"),
        ("3 — Open items resolved", "in progress"),
        ("4 — vs alternatives", "not started"),
        ("5 — What this enables / costs", "not started"),
    ]

    import pandas as pd  # local import for cell isolation

    df = pd.DataFrame(layers, columns=["Layer", "Status"])
    mo.vstack(
        [
            mo.md("## Design write-up layer status"),
            mo.ui.table(df, page_size=10, selection=None),
            mo.md(
                "_Layer 3 closed many open items this round — multi-valued "
                "semantics, DataContext-Config binding, multi-class "
                "DataContexts, graph-level incremental, retention, trigger "
                "model, derivation refs walking._"
            ),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    fixtures = [
        ("A1", "Movie", 1, "smoke", "✅ implemented"),
        ("A2", "Movie", 3, "trust-resolution focus", "📝 template"),
        ("A3", "Movie", "~10", "many-source coordination", "📝 template"),
        ("B1", "Movie/Person/Credit", 1, "relations w/o multi-source", "📝 template"),
        ("B2", "Movie/Person/Credit", 3, "kitchen-sink default", "✅ implemented"),
        ("B3", "Movie/Person/Credit", "~10", "scaling within relations", "📝 template"),
        ("C1", "10+ classes", 1, "rich ontology, isolated", "📝 template"),
        ("C2", "10+ classes", 3, "rich-ontology stress", "✅ implemented"),
        ("C3", "10+ classes", "~10", "max stress", "📝 template"),
    ]
    df = pd.DataFrame(
        fixtures, columns=["Tier", "Classes", "Sources", "Purpose", "Status"]
    )
    impl_count = (df["Status"] == "✅ implemented").sum()

    edge_cases_md = (REPO / "tests/fixtures/EDGE-CASES.md").read_text()
    n_categories = sum(
        1 for line in edge_cases_md.splitlines() if line.startswith("## Category ")
    )

    mo.vstack(
        [
            mo.md("## Test fixture matrix (9 tiers, 3 implemented)"),
            mo.ui.table(df, page_size=10, selection=None),
            mo.md(
                f"**Implemented set ({impl_count} of 9)** seeds every one of "
                f"the **{n_categories} edge-case categories** in `EDGE-CASES.md` "
                "at least once."
            ),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    a1_csv = REPO / "tests/fixtures/A1/sources/imdb_movies.csv"
    a1_df = pd.read_csv(a1_csv)
    mo.vstack(
        [
            mo.md("## A1 fixture (smoke) — sample rows"),
            mo.md(
                f"Single-class smoke fixture. **{len(a1_df)} rows** in "
                "`imdb_movies.csv`. Edge cases seeded: nulls in optionals, "
                "null titles, empty-vs-null, whitespace, numeric edge values, "
                "missing identifiers."
            ),
            mo.ui.table(a1_df, page_size=15, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    b2_dir = REPO / "tests/fixtures/B2/sources"
    rows_per = []
    for csv in sorted(b2_dir.glob("*.csv")):
        df = pd.read_csv(csv)
        rows_per.append({"source": csv.stem, "rows": len(df)})
    summary = pd.DataFrame(rows_per)

    movie_sample = pd.read_csv(b2_dir / "imdb_movies.csv").head(10)
    credit_sample = pd.read_csv(b2_dir / "imdb_credits.csv").head(10)

    mo.vstack(
        [
            mo.md("## B2 fixture (kitchen-sink default) — row counts + samples"),
            mo.md(
                "**Volumes:** "
                f"{summary['rows'].sum()} total rows across "
                f"{len(summary)} sources. Movie/Person/Credit ontology, "
                "3 sources per class, multi-source resolution + cross-class "
                "pinning + derivations + Neo4j publish."
            ),
            mo.ui.table(summary, page_size=10, selection=None),
            mo.md("**Sample — `imdb_movies.csv` (first 10):**"),
            mo.ui.table(movie_sample, page_size=10, selection=None),
            mo.md("**Sample — `imdb_credits.csv` (first 10):**"),
            mo.ui.table(credit_sample, page_size=10, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    c2_dir = REPO / "tests/fixtures/C2/sources"
    rows_per = []
    for csv in sorted(c2_dir.glob("*.csv")):
        df = pd.read_csv(csv)
        rows_per.append({"source": csv.stem, "rows": len(df)})
    summary = pd.DataFrame(rows_per)

    movie_sample = pd.read_csv(c2_dir / "imdb_movies.csv").head(8)
    series_sample = pd.read_csv(c2_dir / "imdb_series.csv").head(8)
    identifier_sample = pd.read_csv(c2_dir / "imdb_identifiers.csv").head(8)

    mo.vstack(
        [
            mo.md("## C2 fixture (rich-ontology stress) — sources"),
            mo.md(
                "Title hierarchy (Movie/Series/Episode/Game), Person/Credit, "
                "polymorphic Identifier, Studio/Award/Country. 10+ classes, "
                "discriminator-routed sources, polymorphic refs, deep "
                "derivation chains."
            ),
            mo.ui.table(summary, page_size=15, selection=None),
            mo.md("**Sample — `imdb_movies.csv`:**"),
            mo.ui.table(movie_sample, page_size=10, selection=None),
            mo.md("**Sample — `imdb_series.csv`:**"),
            mo.ui.table(series_sample, page_size=10, selection=None),
            mo.md(
                "**Sample — `imdb_identifiers.csv` (polymorphic — "
                "`entity_class` discriminates the target):**"
            ),
            mo.ui.table(identifier_sample, page_size=10, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd, yaml):
    edge_cases_path = REPO / "tests/fixtures/B2/edge_cases.yaml"
    with edge_cases_path.open() as fh:
        b2_cases = yaml.safe_load(fh)

    summary_rows = []
    for case_id, payload in (b2_cases or {}).items():
        cases = payload.get("cases", []) if isinstance(payload, dict) else []
        summary_rows.append(
            {
                "case_id": case_id,
                "description": (payload or {}).get("description", "")
                if isinstance(payload, dict)
                else "",
                "n_seeded": len(cases),
            }
        )
    df = pd.DataFrame(summary_rows).sort_values("case_id")

    mo.vstack(
        [
            mo.md("## B2 edge-case coverage"),
            mo.md(
                "Each row is a category from `EDGE-CASES.md` with a count of "
                "seeded canonical_ids in B2's fixture data. Tests target "
                "these cases by id."
            ),
            mo.ui.table(df, page_size=20, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo):
    test_env_src = (REPO / "tests/test_env.py").read_text()
    working = []
    stubbed = []
    for line in test_env_src.splitlines():
        if line.strip().startswith("def ") and "(" in line:
            method_line = line.strip()
            method = method_line.split("(")[0].replace("def ", "")
            if method.startswith("_"):
                continue
            # crude heuristic: look 25 lines ahead for NotImplementedError
            idx = test_env_src.splitlines().index(line)
            tail = "\n".join(test_env_src.splitlines()[idx : idx + 25])
            if "NotImplementedError" in tail:
                stubbed.append(method)
            else:
                working.append(method)

    mo.vstack(
        [
            mo.md("## TestEnv API surface"),
            mo.md(
                "`TestEnv` is the helper class integration tests use to "
                "drive knot end-to-end. Methods that work today use "
                "primitives we already have (Neo4j driver, pyyaml, lake-dir). "
                "Stubbed methods raise `NotImplementedError` naming the "
                "specific `knot.*` dependency they need."
            ),
            mo.md(f"**Working today ({len(working)}):**"),
            mo.md("\n".join(f"- `{m}()`" for m in working)),
            mo.md(f"**Stubbed — needs knot core ({len(stubbed)}):**"),
            mo.md("\n".join(f"- `{m}()`" for m in stubbed)),
        ]
    )
    return


@app.cell
def _(mo):
    mo.md(
        r"""
        ---

        ## Test infrastructure

        - **Stack:** `docker compose up -d` brings up postgres:16 + neo4j:5-community (apoc plugin) with `tmpfs` for ephemeral state.
        - **Smoke tests:** `pytest tests/test_smoke.py` — 3 tests verifying postgres, neo4j, apoc all reachable.
        - **Integration smoke:** `pytest tests/integration/` — 3 working + 1 xfail-strict (placeholder for `run_full_pipeline` once knot core lands).
        - **Helper scripts:** `scripts/up.sh`, `scripts/down.sh`, `scripts/wait-ready.sh`.

        Total: **6 passing + 1 xfail strict**, no warnings.

        ## Next implementation slices

        Per the agent-week plan, in priority order:

        1. **Pydantic spec models** (`src/knot/metaschema.py`) — OntologyClass, Slot (with `resolution_policy`), TypeDefinition, Source, ReferencePattern. Real refs throughout.
        2. **Content-hashing** (`src/knot/canonical.py`) — `canonical_dump` (RFC 8785 JCS) + `compute_content_hash`.
        3. **DataContext walk + impact analysis** — single-dispatch over the typed entity tree.
        4. **SDK codegen** — generate `Movie`, `Movie.year`-style typed classes from spec.
        5. **sqlglot AST machinery** — forward-chain (publish) + backward-chain (translator) + validation.
        6. **Compiler** — spec + impls + configs + watermarks → WorkflowSpec.
        7. **Per-stage cache key** — content-addressed skip-flag emission.

        Tests at every PR are gated by the docker-compose stack + B2/C2 fixtures.
        """
    )
    return


if __name__ == "__main__":
    app.run()
