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
    return REPO, mo, pd, yaml


@app.cell(hide_code=True)
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
    _goals_md = (REPO / "design/goals.md").read_text()
    _lines = _goals_md.splitlines()
    _start = next(_i for _i, _l in enumerate(_lines) if _l.startswith("## What knot is"))
    _end = next(
        _i
        for _i, _l in enumerate(_lines[_start + 1 :], _start + 1)
        if _l.startswith("## ")
    )
    _what_section = "\n".join(_lines[_start:_end])

    mo.md(f"---\n\n{_what_section}")
    return


@app.cell
def _(REPO, mo, pd):
    _core = (REPO / "design/core-design.md").read_text()
    _commits = []
    for _line in _core.splitlines():
        if _line.startswith("## "):
            _head = _line[3:].strip()
            if _head and _head[0].isdigit():
                _parts = _head.split(".", 1)
                if len(_parts) == 2 and _parts[0].isdigit():
                    _commits.append(
                        {"#": int(_parts[0]), "commitment": _parts[1].strip()}
                    )
    _df = pd.DataFrame(_commits).set_index("#")
    mo.vstack(
        [
            mo.md("## 17 architectural commitments"),
            mo.ui.table(_df, page_size=20, selection=None),
            mo.md(
                "_Each is load-bearing — remove or invert and knot becomes a "
                "different system. Full prose at `design/core-design.md`._"
            ),
        ]
    )
    return


@app.cell
def _(mo, pd):
    _layers = [
        ("1 — Why knot exists", "complete"),
        ("2 — Architectural commitments", "complete"),
        ("3 — Open items resolved", "in progress"),
        ("4 — vs alternatives", "not started"),
        ("5 — What this enables / costs", "not started"),
    ]
    _df = pd.DataFrame(_layers, columns=["Layer", "Status"])
    mo.vstack(
        [
            mo.md("## Design write-up layer status"),
            mo.ui.table(_df, page_size=10, selection=None),
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
    _fixtures = [
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
    _df = pd.DataFrame(
        _fixtures, columns=["Tier", "Classes", "Sources", "Purpose", "Status"]
    )
    _impl_count = (_df["Status"] == "✅ implemented").sum()

    _edge_cases_md = (REPO / "tests/fixtures/EDGE-CASES.md").read_text()
    _n_categories = sum(
        1 for _l in _edge_cases_md.splitlines() if _l.startswith("## Category ")
    )

    mo.vstack(
        [
            mo.md("## Test fixture matrix (9 tiers, 3 implemented)"),
            mo.ui.table(_df, page_size=10, selection=None),
            mo.md(
                f"**Implemented set ({_impl_count} of 9)** seeds every one of "
                f"the **{_n_categories} edge-case categories** in `EDGE-CASES.md` "
                "at least once."
            ),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    _a1_csv = REPO / "tests/fixtures/A1/sources/imdb_movies.csv"
    _a1_df = pd.read_csv(_a1_csv)
    mo.vstack(
        [
            mo.md("## A1 fixture (smoke) — sample rows"),
            mo.md(
                f"Single-class smoke fixture. **{len(_a1_df)} rows** in "
                "`imdb_movies.csv`. Edge cases seeded: nulls in optionals, "
                "null titles, empty-vs-null, whitespace, numeric edge values, "
                "missing identifiers."
            ),
            mo.ui.table(_a1_df, page_size=15, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    _b2_dir = REPO / "tests/fixtures/B2/sources"
    _rows_per = []
    for _csv_path in sorted(_b2_dir.glob("*.csv")):
        _df = pd.read_csv(_csv_path)
        _rows_per.append({"source": _csv_path.stem, "rows": len(_df)})
    _summary = pd.DataFrame(_rows_per)

    _movie_sample = pd.read_csv(_b2_dir / "imdb_movies.csv").head(10)
    _credit_sample = pd.read_csv(_b2_dir / "imdb_credits.csv").head(10)

    mo.vstack(
        [
            mo.md("## B2 fixture (kitchen-sink default) — row counts + samples"),
            mo.md(
                "**Volumes:** "
                f"{_summary['rows'].sum()} total rows across "
                f"{len(_summary)} sources. Movie/Person/Credit ontology, "
                "3 sources per class, multi-source resolution + cross-class "
                "pinning + derivations + Neo4j publish."
            ),
            mo.ui.table(_summary, page_size=10, selection=None),
            mo.md("**Sample — `imdb_movies.csv` (first 10):**"),
            mo.ui.table(_movie_sample, page_size=10, selection=None),
            mo.md("**Sample — `imdb_credits.csv` (first 10):**"),
            mo.ui.table(_credit_sample, page_size=10, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd):
    _c2_dir = REPO / "tests/fixtures/C2/sources"
    _rows_per = []
    for _csv_path in sorted(_c2_dir.glob("*.csv")):
        _df = pd.read_csv(_csv_path)
        _rows_per.append({"source": _csv_path.stem, "rows": len(_df)})
    _summary = pd.DataFrame(_rows_per)

    _movie_sample = pd.read_csv(_c2_dir / "imdb_movies.csv").head(8)
    _series_sample = pd.read_csv(_c2_dir / "imdb_series.csv").head(8)
    _identifier_sample = pd.read_csv(_c2_dir / "imdb_identifiers.csv").head(8)

    mo.vstack(
        [
            mo.md("## C2 fixture (rich-ontology stress) — sources"),
            mo.md(
                "Title hierarchy (Movie/Series/Episode/Game), Person/Credit, "
                "polymorphic Identifier, Studio/Award/Country. 10+ classes, "
                "discriminator-routed sources, polymorphic refs, deep "
                "derivation chains."
            ),
            mo.ui.table(_summary, page_size=15, selection=None),
            mo.md("**Sample — `imdb_movies.csv`:**"),
            mo.ui.table(_movie_sample, page_size=10, selection=None),
            mo.md("**Sample — `imdb_series.csv`:**"),
            mo.ui.table(_series_sample, page_size=10, selection=None),
            mo.md(
                "**Sample — `imdb_identifiers.csv` (polymorphic — "
                "`entity_class` discriminates the target):**"
            ),
            mo.ui.table(_identifier_sample, page_size=10, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo, pd, yaml):
    _edge_cases_path = REPO / "tests/fixtures/B2/edge_cases.yaml"
    with _edge_cases_path.open() as _fh:
        _b2_cases = yaml.safe_load(_fh)

    _summary_rows = []
    for _case_id, _payload in (_b2_cases or {}).items():
        _cases = _payload.get("cases", []) if isinstance(_payload, dict) else []
        _summary_rows.append(
            {
                "case_id": _case_id,
                "description": (_payload or {}).get("description", "")
                if isinstance(_payload, dict)
                else "",
                "n_seeded": len(_cases),
            }
        )
    _df = pd.DataFrame(_summary_rows).sort_values("case_id")

    mo.vstack(
        [
            mo.md("## B2 edge-case coverage"),
            mo.md(
                "Each row is a category from `EDGE-CASES.md` with a count of "
                "seeded canonical_ids in B2's fixture data. Tests target "
                "these cases by id."
            ),
            mo.ui.table(_df, page_size=20, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo):
    _test_env_src = (REPO / "tests/test_env.py").read_text()
    _src_lines = _test_env_src.splitlines()
    _working = []
    _stubbed = []
    for _idx, _ln in enumerate(_src_lines):
        _stripped = _ln.strip()
        if _stripped.startswith("def ") and "(" in _stripped:
            _method = _stripped.split("(")[0].replace("def ", "")
            if _method.startswith("_"):
                continue
            _tail = "\n".join(_src_lines[_idx : _idx + 25])
            if "NotImplementedError" in _tail:
                _stubbed.append(_method)
            else:
                _working.append(_method)

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
            mo.md(f"**Working today ({len(_working)}):**"),
            mo.md("\n".join(f"- `{_m}()`" for _m in _working)),
            mo.md(f"**Stubbed — needs knot core ({len(_stubbed)}):**"),
            mo.md("\n".join(f"- `{_m}()`" for _m in _stubbed)),
        ]
    )
    return


@app.cell(hide_code=True)
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
