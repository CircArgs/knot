"""knot — progress since 00_state_of_play.

Live mirror of what's landed in the repo. Cells re-run on dependency changes.
Companion to `00_state_of_play.py`.
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
        # 01 — progress since `00_state_of_play`

        What's landed since the initial state-of-play snapshot. Live mirror;
        cells re-run when their inputs change.
        """
    )
    return


@app.cell
def _(mo, pd):
    _rows = [
        ("passed", 32, 72, "+40"),
        ("xfailed (strict)", 20, 20, "0"),
        ("commits ahead of initial", 1, 13, "+12"),
        ("staging docs", 18, 22, "+4"),
        ("`src/knot/` Python lines", 0, 1118, "+1118"),
    ]
    _df = pd.DataFrame(
        _rows, columns=["metric", "00_state_of_play", "now", "delta"]
    )
    mo.vstack(
        [
            mo.md("## Test suite & repo progression"),
            mo.ui.table(_df, page_size=10, selection=None),
        ]
    )
    return


@app.cell
def _(REPO, mo):
    _sql = (REPO / "src/knot/control_schema.sql").read_text()
    _summary = """
- **Dropped:** `impl_source.is_published` (parallel state machine; collapsed into "row exists ⇒ registered")
- **Renamed:** `impl_source` → `impl_revision` (what persists is a revision, not detached source)
- **Added column:** `impl_revision.pinned_spec_hash CHAR(64) NOT NULL` — captures spec hash registration validated against
- **Added column:** `impl_revision.submitted_by` + `impl_config.submitted_by` (audit attribution; trust posture, no auth)
- **Added table:** `bound_impls` — satisfies commitment 6 (one binding per `(stage, class_name)`)
"""
    mo.vstack(
        [
            mo.md("## Schema cleanup (Sharpener-driven)"),
            mo.md(_summary),
            mo.md("**Live SQL:**"),
            mo.md(f"```sql\n{_sql}\n```"),
        ]
    )
    return


@app.cell
def _(mo, pd):
    from knot.protocols import (
        ConstraintEvaluator,
        DerivationEvaluator,
        DqMergeRunner,
        DqNormalizeRunner,
        DqPublishRunner,
        DqResolveRunner,
        ERProtocol,
        MaterializerProtocol,
        TranslatorProtocol,
    )

    _classes = [
        ("ERProtocol", ERProtocol, "ERResult", "score()"),
        ("MaterializerProtocol", MaterializerProtocol, "MaterializeResult", "materialize()"),
        ("TranslatorProtocol", TranslatorProtocol, "TranslateResult", "translate()"),
        ("ConstraintEvaluator", ConstraintEvaluator, "ConstraintResult", "evaluate()"),
        ("DerivationEvaluator", DerivationEvaluator, "DerivationResult", "evaluate()"),
        ("DqNormalizeRunner", DqNormalizeRunner, "DqResult", "check()"),
        ("DqResolveRunner", DqResolveRunner, "DqResult", "check()"),
        ("DqMergeRunner", DqMergeRunner, "DqResult", "check()"),
        ("DqPublishRunner", DqPublishRunner, "DqResult", "check()"),
    ]
    _rows = []
    for _name, _cls, _result, _method in _classes:
        _stance = getattr(_cls, "disagreement_stance", None)
        _stance_str = _stance.value if _stance is not None else "—"
        _rows.append(
            {
                "protocol": _name,
                "stance": _stance_str,
                "method": _method,
                "returns": _result,
            }
        )
    _df = pd.DataFrame(_rows)
    mo.vstack(
        [
            mo.md("## Protocol family — universal DI seam"),
            mo.md(
                "Each protocol pins its `disagreement_stance` (lens) at the class "
                "level and returns a typed knot-controlled result shape. Impl writers "
                "can't drift the contract by renaming columns."
            ),
            mo.ui.table(_df, page_size=15, selection=None),
        ]
    )
    return


@app.cell
def _(mo):
    from pathlib import Path as _Path

    from knot.protocols import ERResult, ScoreColumnMap

    _sample = ERResult(
        table=_Path("/lake/er_outputs/movie_pairs.parquet"),
        column_map=ScoreColumnMap(
            a_canonical="left_id",
            b_canonical="right_id",
            score="similarity",
        ),
    )

    _rendered = _sample.model_dump_json(indent=2)
    mo.vstack(
        [
            mo.md("## Sample typed return — `ERResult`"),
            mo.md(
                "Impl writer names columns however; the `column_map` declares "
                "which column means what. Knot owns the result shape."
            ),
            mo.md(f"```json\n{_rendered}\n```"),
        ]
    )
    return


@app.cell
def _(mo):
    _new_nodes = [
        ("Within", "set membership", "Movie.genres.within(['Action', 'Sci-Fi'])"),
        ("Between", "range predicate", "Movie.year.between(1990, 2000)"),
        ("RecursiveTraversal", "transitive walk", "Title.descendants() / Person.knows.transitive(max_depth=3)"),
    ]
    import pandas as _pd

    _df = _pd.DataFrame(
        _new_nodes, columns=["new node type", "purpose", "SDK surface example"]
    )
    mo.vstack(
        [
            mo.md("## Query-language verdict — typed AST stays; Gremlin earns place as Translator emit target"),
            mo.md(
                "3-persona debate (Type Maximalist / Comparative Anchorer / "
                "Reality Checker) converged: keep typed Pydantic AST as "
                "source-of-truth. Decisive scenarios were spec rename "
                "(silent-drift in Gremlin string until runtime AFTER hash "
                "dispatch) and lens-stance enforcement (only typed AST whose "
                "codegen reads protocol stance can do `MultiValued[T]` vs "
                "`Resolved[T]`)."
            ),
            mo.md("**Borrowed-from-Gremlin AST nodes (pending):**"),
            mo.ui.table(_df, page_size=10, selection=None),
            mo.md(
                "Anti-patterns flagged: stringly-typed escape hatches "
                "(SHACL/SPARQL, APOC, Jinja+SQL), two-surface DSLs (ksqlDB vs "
                "Streams DSL), schema-string adoption coupling (GraphQL "
                "deprecation cycles)."
            ),
        ]
    )
    return


@app.cell
def _(REPO, mo):
    _path = REPO / "design/staging/impl-dependencies.md"
    if _path.exists():
        _body = _path.read_text()
    else:
        _body = "*staging doc not found — check `design/staging/`*"
    mo.vstack(
        [
            mo.md("## Library-deps candidate models — pending your decision"),
            mo.md(
                "Brainstorm by `deps-worker` (Reality Checker + Comparative "
                "Anchorer + Trust Posture personas). 3-4 candidate models with "
                "tradeoffs. Orchestrator (you) picks one."
            ),
            mo.md("---"),
            mo.md(_body),
        ]
    )
    return


@app.cell
def _(REPO, mo):
    _path = REPO / "design/staging/query-language-rationale.md"
    _body = _path.read_text() if _path.exists() else "*not found*"
    mo.vstack(
        [
            mo.md("## Query-language rationale — full doc"),
            mo.md(_body),
        ]
    )
    return


@app.cell(hide_code=True)
def _(mo):
    mo.md(
        r"""
        ---

        ## What's still pending your call

        - **Library-deps model** — pick a candidate from `staging/impl-dependencies.md`
        - **Add `Within` / `Between` / `RecursiveTraversal` AST nodes** — captured in `staging/query-language-rationale.md`, not yet implemented
        - **Day 2 implementation slices** — DataContext walk + SDK codegen (depends on metaschema ✓ + protocols ✓; ready to dispatch)

        ## What's deferred (per design)

        - Browser UI (Marquez/Netflix port plan)
        - Auth (bound DI impl at Netflix port-time)
        - Real-data fixtures (B2-real with public IMDB/TMDB) — week 2
        """
    )
    return


if __name__ == "__main__":
    app.run()
