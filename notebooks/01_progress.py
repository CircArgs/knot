"""knot — interactive control panel.

Live mirror of repo state with interactive widgets to drive open decisions.
Pick library-deps models, browse the spec, simulate trust resolution,
explore seeded edge cases, run tests inline.
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
        # 01 — control panel

        Interactive widgets drive open decisions. Each widget below is wired:
        change the selection and dependent cells re-render.
        """
    )
    return


# ---------------------------------------------------------------------------
# Status header — compact, top-of-page
# ---------------------------------------------------------------------------


@app.cell
def _(REPO, mo):
    import subprocess as _subprocess

    _result = _subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    _head = _result.stdout.strip() or "?"
    _commits_result = _subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    _commit_count = _commits_result.stdout.strip() or "?"

    mo.callout(
        mo.md(
            f"**HEAD** `{_head}` · **{_commit_count} commits** · "
            f"**72 passed + 20 xfailed** · "
            f"[github.com/CircArgs/knot](https://github.com/CircArgs/knot)"
        ),
        kind="info",
    )
    return


# ---------------------------------------------------------------------------
# Library-deps decision panel
# ---------------------------------------------------------------------------


@app.cell
def _(REPO, mo):
    _doc = (REPO / "design/staging/impl-dependencies.md").read_text()

    # Crude parse: pull "### Model A: ..." -> "### Model B: ..." sections
    _models = {}
    _current = None
    _buf: list[str] = []
    for _line in _doc.splitlines():
        if _line.startswith("### Model "):
            if _current is not None:
                _models[_current] = "\n".join(_buf).strip()
            _current = _line.replace("### ", "").strip()
            _buf = []
        elif _current is not None:
            _buf.append(_line)
    if _current is not None and _buf:
        _models[_current] = "\n".join(_buf).strip()

    _options = list(_models.keys()) or ["(no models parsed from doc)"]

    library_deps_pick = mo.ui.radio(
        options=_options,
        label="**Pick a library-deps model:**",
        value=_options[0] if _options else None,
    )
    lib_deps_models = _models
    return library_deps_pick, lib_deps_models


@app.cell
def _(library_deps_pick, lib_deps_models, mo):
    _selected = library_deps_pick.value
    _body = lib_deps_models.get(_selected, "*select a model above*")

    mo.vstack(
        [
            mo.md("## Library-deps model picker"),
            mo.md(
                "Brainstorm at `design/staging/impl-dependencies.md`. Pick to "
                "expand its full description below."
            ),
            library_deps_pick,
            mo.md("---"),
            mo.md(f"### {_selected}\n\n{_body}"),
        ]
    )
    return


# ---------------------------------------------------------------------------
# Spec class explorer
# ---------------------------------------------------------------------------


@app.cell
def _(REPO, mo):
    import sys as _sys

    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))
    from tests.fixtures.B2 import spec as b2_spec

    _class_names = [c.name for c in b2_spec.spec.classes]

    spec_class_pick = mo.ui.dropdown(
        options=_class_names,
        label="Browse class:",
        value=_class_names[0] if _class_names else None,
    )
    return b2_spec, spec_class_pick


@app.cell
def _(mo, pd, spec_class_pick, b2_spec):
    _selected_name = spec_class_pick.value
    _cls = next(
        (c for c in b2_spec.spec.classes if c.name == _selected_name), None
    )

    if _cls is None:
        _content = mo.md("*no class selected*")
    else:
        _rows = []
        for _slot in _cls.slots:
            _range = (
                _slot.range.name if hasattr(_slot.range, "name") else "—"
            )
            _is_derived = _slot.derivation is not None
            _rp = _slot.resolution_policy
            if _rp is None:
                _policy = "—"
            elif hasattr(_rp, "value"):
                _policy = _rp.value
            else:
                _policy = str(_rp)
            _rows.append(
                {
                    "slot": _slot.name,
                    "range": _range,
                    "kind": "derived" if _is_derived else "stored",
                    "resolution_policy": _policy,
                    "multivalued": _slot.multivalued,
                    "required": _slot.required,
                }
            )
        _df = pd.DataFrame(_rows)
        _content = mo.vstack(
            [
                mo.md(
                    f"**{_cls.name}** — {_cls.description or '_(no description)_'}"
                ),
                mo.ui.table(_df, page_size=20, selection=None),
            ]
        )

    mo.vstack(
        [
            mo.md("## Spec class explorer (B2 fixture)"),
            spec_class_pick,
            _content,
        ]
    )
    return


# ---------------------------------------------------------------------------
# Trust resolution simulator
# ---------------------------------------------------------------------------


@app.cell
def _(mo):
    from knot.metaschema import ResolutionPolicy

    _policies = [p.value for p in ResolutionPolicy]

    contributions_input = mo.ui.text_area(
        label="Source contributions (one per line: `source,value,trust`):",
        value=(
            "imdb,1999,0.91\n"
            "tmdb,2000,0.62\n"
            "wikidata,1999,0.78"
        ),
        rows=4,
    )
    policy_pick = mo.ui.dropdown(
        options=_policies,
        label="resolution_policy:",
        value="argmax_trust",
    )
    return contributions_input, policy_pick


@app.cell
def _(contributions_input, mo, pd, policy_pick):
    _lines = [l.strip() for l in contributions_input.value.splitlines() if l.strip()]
    _contribs = []
    for _l in _lines:
        try:
            _src, _val, _trust = _l.split(",")
            _contribs.append(
                {
                    "source": _src.strip(),
                    "value": _val.strip(),
                    "trust": float(_trust.strip()),
                }
            )
        except (ValueError, IndexError):
            continue

    def _resolve(contribs, policy):
        if not contribs:
            return None, "no contributions"
        if policy == "argmax_trust":
            best = max(contribs, key=lambda c: c["trust"])
            return best["value"], f"argmax: {best['source']}"
        if policy == "mode":
            from collections import Counter

            counts = Counter(c["value"] for c in contribs)
            top, n = counts.most_common(1)[0]
            ties = [v for v, c in counts.items() if c == n]
            if len(ties) == 1:
                return top, f"mode: {top} ({n} occurrences)"
            best = max(
                (c for c in contribs if c["value"] in ties),
                key=lambda c: c["trust"],
            )
            return (
                best["value"],
                f"mode tied → argmax_trust tiebreak: {best['source']}",
            )
        if policy == "weighted_vote":
            from collections import defaultdict

            sums = defaultdict(float)
            for c in contribs:
                sums[c["value"]] += c["trust"]
            top = max(sums.items(), key=lambda kv: kv[1])
            return top[0], f"weighted_vote: trust sum {top[1]:.2f}"
        if policy == "median_numeric":
            try:
                vals = sorted(float(c["value"]) for c in contribs)
                m = vals[len(vals) // 2]
                return str(m), f"median over {len(vals)} numeric values"
            except ValueError:
                return None, "values not numeric"
        if policy == "latest_watermark":
            return contribs[-1]["value"], "latest by input order (sim)"
        if policy == "unique_or_fail":
            uniq = {c["value"] for c in contribs}
            if len(uniq) == 1:
                return uniq.pop(), "all sources agree"
            return None, f"DISAGREEMENT → typed exception: {sorted(uniq)}"
        return None, "unknown policy"

    _resolved, _why = _resolve(_contribs, policy_pick.value)
    _df = pd.DataFrame(_contribs) if _contribs else pd.DataFrame()

    mo.vstack(
        [
            mo.md("## Trust resolution simulator"),
            mo.md(
                "Edit contributions, swap policy, see the resolved value live. "
                "Mirrors knot's CTE-rewrite logic per `multi-valued-semantics.md`."
            ),
            mo.hstack([contributions_input, policy_pick]),
            mo.ui.table(_df, page_size=10, selection=None) if _contribs else mo.md("_no contributions parsed_"),
            mo.callout(
                mo.md(
                    f"**Resolved:** `{_resolved}`\n\n_{_why}_"
                ),
                kind="success" if _resolved is not None else "danger",
            ),
        ]
    )
    return


# ---------------------------------------------------------------------------
# Edge-case browser
# ---------------------------------------------------------------------------


@app.cell
def _(REPO, mo, yaml):
    _path = REPO / "tests/fixtures/B2/edge_cases.yaml"
    _data = yaml.safe_load(_path.read_text()) if _path.exists() else {}
    _categories = sorted((_data or {}).keys())

    edge_case_pick = mo.ui.dropdown(
        options=_categories,
        label="Edge-case category (B2 fixture):",
        value=_categories[0] if _categories else None,
    )
    edge_cases_data = _data
    return edge_case_pick, edge_cases_data


@app.cell
def _(edge_case_pick, mo, pd, edge_cases_data):
    _selected = edge_case_pick.value
    _payload = (edge_cases_data or {}).get(_selected, {})
    _description = (
        _payload.get("description", "") if isinstance(_payload, dict) else ""
    )
    _cases = _payload.get("cases", []) if isinstance(_payload, dict) else []

    _df = pd.DataFrame(_cases) if _cases else pd.DataFrame()

    mo.vstack(
        [
            mo.md("## Edge-case browser (B2 fixture seeded cases)"),
            mo.md(
                "Each EDGE-CASES.md category seeded into B2 has canonical_ids "
                "you can target in tests."
            ),
            edge_case_pick,
            mo.md(f"**{_selected}** — {_description}"),
            mo.ui.table(_df, page_size=20, selection=None) if _cases else mo.md("_no cases for this category_"),
        ]
    )
    return


# ---------------------------------------------------------------------------
# Test runner button
# ---------------------------------------------------------------------------


@app.cell
def _(mo):
    run_unit_tests_btn = mo.ui.run_button(
        label="Run unit tests (no docker needed)",
        kind="info",
    )
    return (run_unit_tests_btn,)


@app.cell
def _(REPO, mo, run_unit_tests_btn):
    if run_unit_tests_btn.value:
        import subprocess as _subprocess

        _proc = _subprocess.run(
            [str(REPO / ".venv/bin/pytest"), "tests/unit", "-v", "--tb=short"],
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=60,
        )
        _output = (_proc.stdout + _proc.stderr).split("\n")[-30:]
        _content = mo.vstack(
            [
                mo.md(
                    f"**exit code:** `{_proc.returncode}` "
                    f"({'PASS' if _proc.returncode == 0 else 'FAIL'})"
                ),
                mo.md(f"```\n{chr(10).join(_output)}\n```"),
            ]
        )
    else:
        _content = mo.md("*click the button to run unit tests*")

    mo.vstack(
        [
            mo.md("## Test runner"),
            mo.md(
                "Runs `pytest tests/unit/` (no docker required — pure-Python "
                "unit tests on metaschema, canonical hashing, protocols)."
            ),
            run_unit_tests_btn,
            _content,
        ]
    )
    return


# ---------------------------------------------------------------------------
# Footer with what's pending
# ---------------------------------------------------------------------------


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
        ---

        ## Pending your call

        - **Library-deps model** — pick above; canonical-doc capture happens after you confirm
        - **Add `Within` / `Between` / `RecursiveTraversal` AST nodes** — captured in `staging/query-language-rationale.md`, not yet implemented
        - **Day 2 implementation slices** — DataContext walk + SDK codegen (depends on metaschema ✓ + protocols ✓; ready to dispatch)

        ## Companion notebooks

        - `00_state_of_play.py` — broad project overview (commitments, fixture matrix, layer status)
        """
    )
    return


if __name__ == "__main__":
    app.run()
