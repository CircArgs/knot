"""knot — interactive sandbox.

Real machinery the user can poke. No git stats, no commit counts, no walkthrough.
- Spec class explorer (browse B2 ontology classes + slots)
- Expression-tree builder (type SDK expression, see Pydantic AST + canonical hash)
- Canonical-dump preview (see RFC 8785 JCS bytes for any spec)
- DuckDB SQL sandbox over B2 fixture CSVs (write SQL, see results)
- Pydantic validation playground (paste a broken spec dict, see the error)
- Trust resolution simulator (live policy comparisons over multi-source contributions)
- SDK codegen output viewer (see the generated typed classes for B2 spec under each lens)
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


@app.cell
def _():
    import sys as _sys
    from pathlib import Path

    REPO = Path("/mnt/main/code/knot")
    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))

    import marimo as mo
    import pandas as pd
    import yaml

    return REPO, mo, pd


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # 01 — knot interactive sandbox

    Poke at the actual machinery. Each section is wired live to the repo —
    edit a fixture, change a class, and downstream cells re-render.
    """)
    return


@app.cell
def _(mo):
    from tests.fixtures.B2 import spec as b2_spec

    spec_class_pick = mo.ui.dropdown(
        options=[c.name for c in b2_spec.spec.classes],
        label="Class:",
        value=b2_spec.spec.classes[0].name if b2_spec.spec.classes else None,
    )
    return b2_spec, spec_class_pick


@app.cell
def _(b2_spec, mo, pd, spec_class_pick):
    _cls = next(
        (c for c in b2_spec.spec.classes if c.name == spec_class_pick.value),
        None,
    )

    if _cls is None:
        _content = mo.md("*no class selected*")
    else:
        _rows = []
        for _slot in _cls.slots:
            _range = _slot.range.name if hasattr(_slot.range, "name") else "—"
            _rp = _slot.resolution_policy
            _policy = (
                _rp.value if hasattr(_rp, "value") else (str(_rp) if _rp else "—")
            )
            _rows.append(
                {
                    "slot": _slot.name,
                    "range": _range,
                    "kind": "derived" if _slot.derivation else "stored",
                    "policy": _policy,
                    "multi": _slot.multivalued,
                    "required": _slot.required,
                }
            )
        _content = mo.vstack(
            [
                mo.md(
                    f"**{_cls.name}** — {_cls.description or '_(no description)_'}"
                ),
                mo.ui.table(pd.DataFrame(_rows), page_size=20, selection=None),
            ]
        )

    mo.vstack(
        [
            mo.md("## 1 · Spec class explorer (B2 fixture)"),
            spec_class_pick,
            _content,
        ]
    )
    return


@app.cell
def _(mo):
    expr_input = mo.ui.text(
        label="SDK expression:",
        value="Movie.year > 1990",
        full_width=True,
    )
    return (expr_input,)


@app.cell
def _(b2_spec, expr_input, mo):
    _src = expr_input.value.strip()

    _names = {
        c.name: c for c in b2_spec.spec.classes
    }
    _names.update({s.name: s for s in b2_spec.spec.types})
    _ns = dict(_names)

    def _summarize(node, depth=0, max_depth=4):
        _indent = "  " * depth
        _type_name = type(node).__name__
        if depth >= max_depth:
            return f"{_indent}{_type_name}(...)"
        if not hasattr(node, "model_fields"):
            if isinstance(node, (str, int, float, bool, type(None))):
                return f"{_indent}{node!r}"
            return f"{_indent}{type(node).__name__}({node!r})"
        _parts = [f"{_indent}{_type_name}("]
        for _fname in node.model_fields:
            try:
                _fval = getattr(node, _fname)
            except Exception:
                continue
            if hasattr(_fval, "model_fields"):
                _parts.append(f"{_indent}  {_fname}=")
                _parts.append(_summarize(_fval, depth + 1, max_depth))
            elif isinstance(_fval, list):
                _parts.append(f"{_indent}  {_fname}=[")
                for _item in _fval:
                    _parts.append(_summarize(_item, depth + 2, max_depth))
                _parts.append(f"{_indent}  ]")
            else:
                _short = repr(_fval)
                if len(_short) > 80:
                    _short = _short[:80] + "..."
                _parts.append(f"{_indent}  {_fname}={_short}")
        _parts.append(f"{_indent})")
        return "\n".join(_parts)

    try:
        _tree = eval(_src, {"__builtins__": {}}, _ns)
        _rendered = _summarize(_tree)
        _ok = True
    except Exception as _e:
        _rendered = f"{type(_e).__name__}: {_e}"
        _ok = False

    mo.vstack(
        [
            mo.md("## 2 · Expression-tree builder"),
            mo.md(
                "Type any SDK expression using B2's classes (Movie, Person, Credit). "
                "See the typed Pydantic AST it constructs. Try: "
                "`Movie.year > 1990`, `Movie.year.between(1990, 2000)`, "
                "`Movie.title.matches('^The .*')`, "
                "`(Movie.year > 1990) & (Movie.runtime_minutes > 90)`."
            ),
            expr_input,
            mo.callout(
                mo.md(f"```json\n{_rendered}\n```"),
                kind="success" if _ok else "danger",
            ),
        ]
    )
    return


@app.cell
def _(b2_spec, mo):
    from knot.canonical import canonical_dump, compute_content_hash

    try:
        _bytes = canonical_dump(b2_spec.spec)
        _hash = compute_content_hash(b2_spec.spec)
        _preview = _bytes[:600].decode("utf-8", errors="replace")
        _ok = True
        _err = None
    except Exception as _e:
        _bytes = b""
        _hash = None
        _preview = ""
        _ok = False
        _err = f"{type(_e).__name__}: {_e}"

    if _ok:
        _content = mo.vstack(
            [
                mo.callout(
                    mo.md(f"**hash:** `{_hash}`\n\n**bytes:** {len(_bytes)}"),
                    kind="info",
                ),
                mo.md(f"**First 600 bytes preview:**\n\n```json\n{_preview}\n…\n```"),
            ]
        )
    else:
        _content = mo.callout(
            mo.md(
                f"**`canonical_dump` failed on B2 spec.** "
                f"Likely a known cycle-handling gap in `src/knot/canonical.py` "
                f"when applied to specs with circular slot.range references "
                f"(B2 has Movie ↔ Person via Credit). \n\n"
                f"Error: `{_err}`"
            ),
            kind="warn",
        )

    mo.vstack(
        [
            mo.md("## 3 · Canonical dump + content hash (B2 spec)"),
            mo.md(
                "Live `canonical_dump` (RFC 8785 JCS) bytes and sha256 hash. "
                "Two semantically-equivalent specs hash identically; "
                "RUNTIME fields (`description`, `last_modified`, etc.) excluded."
            ),
            _content,
        ]
    )
    return


@app.cell
def _(mo):
    sql_dialect_pick = mo.ui.dropdown(
        options=["duckdb", "trino", "spark"],
        label="SQL dialect:",
        value="duckdb",
    )
    return (sql_dialect_pick,)


@app.cell
def _(REPO, b2_spec, expr_input, mo, sql_dialect_pick):
    from knot.sql_gen import emit_sql

    _names = {c.name: c for c in b2_spec.spec.classes}
    _names.update({s.name: s for s in b2_spec.spec.types})
    _ns = dict(_names)

    try:
        _tree = eval(expr_input.value.strip(), {"__builtins__": {}}, _ns)
        _sql = emit_sql(_tree, dialect=sql_dialect_pick.value)
        _emit_ok = True
        _emit_err = None
    except Exception as _e:
        _sql = None
        _emit_ok = False
        _emit_err = f"{type(_e).__name__}: {_e}"

    if _emit_ok and _sql:
        try:
            from knot.lake.duckdb_reader import DuckDBReader, DuckDBReaderConfig

            _b2_dir = REPO / "tests/fixtures/B2/sources"
            _reader = DuckDBReader(DuckDBReaderConfig(lake_dir=_b2_dir))
            _reader.register_csv_view("movie", _b2_dir / "imdb_movies.csv")
            _reader.register_csv_view("person", _b2_dir / "imdb_persons.csv")
            _reader.register_csv_view("credit", _b2_dir / "imdb_credits.csv")
            _wrapped_sql = (
                f"SELECT * FROM movie WHERE EXISTS (SELECT 1 WHERE {_sql})"
                if not _sql.lower().lstrip().startswith("select")
                else _sql
            )
            _arrow = _reader.read(None, _wrapped_sql)
            _df = _arrow.to_pandas()
            _run_ok = True
            _run_err = None
        except Exception as _e:
            _df = None
            _run_ok = False
            _run_err = f"{type(_e).__name__}: {_e}"
    else:
        _df = None
        _run_ok = False
        _run_err = "SQL emission failed; can't run"

    _sql_block = (
        mo.md(f"```sql\n{_sql}\n```")
        if _emit_ok
        else mo.callout(mo.md(f"**emit_sql failed:** `{_emit_err}`"), kind="danger")
    )

    if _run_ok:
        _result_block = mo.vstack(
            [
                mo.md(f"**{len(_df)} rows** matched in `imdb_movies.csv`:"),
                mo.ui.table(_df.head(50), page_size=20, selection=None),
            ]
        )
    elif _emit_ok:
        _result_block = mo.callout(
            mo.md(
                f"**Couldn't run against B2 fixture:** `{_run_err}`\n\n"
                "(Some predicates need a `WHERE`-shaped wrap or table refs that "
                "aren't registered. SQL above is what `sql_gen` emits.)"
            ),
            kind="warn",
        )
    else:
        _result_block = mo.md("")

    mo.vstack(
        [
            mo.md("## 4 · SDK → SQL → run against B2 fixtures"),
            mo.md(
                "Type an SDK expression in section 2 above. This cell calls "
                "`sql_gen.emit_sql(tree, dialect)` for what knot would generate, "
                "then runs it via `DuckDBReader` against the B2 fixture CSVs "
                "(registered as views: `movie`, `person`, `credit`). Real bytes."
            ),
            sql_dialect_pick,
            mo.md("**Generated SQL:**"),
            _sql_block,
            mo.md("**Result on B2 fixture:**"),
            _result_block,
        ]
    )
    return


@app.cell
def _(mo):
    pydantic_input = mo.ui.text_area(
        label="OntologyClass dict (will be parsed as Pydantic):",
        value=(
            '{\n'
            '  "name": "Movie",\n'
            '  "slots": [],\n'
            '  "description": "A film with a theatrical release",\n'
            '  "extra_field_that_should_fail": "boom"\n'
            '}'
        ),
        rows=10,
    )
    return (pydantic_input,)


@app.cell
def _(mo, pydantic_input):
    import json as _json

    from knot.metaschema import OntologyClass

    try:
        _data = _json.loads(pydantic_input.value)
        _instance = OntologyClass.model_validate(_data)
        _ok = True
        _output = _instance.model_dump_json(indent=2)
    except Exception as _e:
        _ok = False
        _output = f"{type(_e).__name__}:\n{_e}"

    mo.vstack(
        [
            mo.md("## 5 · Pydantic validation playground"),
            mo.md(
                "Edit the dict and see what knot's spec-layer validation accepts "
                "or rejects. `extra='forbid'` means unknown fields raise loudly "
                "(commitment 16)."
            ),
            pydantic_input,
            mo.callout(
                mo.md(f"```\n{_output}\n```"),
                kind="success" if _ok else "danger",
            ),
        ]
    )
    return


@app.cell
def _(mo):
    from knot.metaschema import ResolutionPolicy

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
        options=[p.value for p in ResolutionPolicy],
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
            return best["value"], f"mode tied → argmax tiebreak: {best['source']}"
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
            mo.md("## 6 · Trust resolution simulator"),
            mo.md(
                "Mirrors knot's CTE-rewrite logic per `multi-valued-semantics.md`. "
                "Edit contributions, swap policy, see resolved value live."
            ),
            mo.hstack([contributions_input, policy_pick]),
            mo.ui.table(_df, page_size=10, selection=None) if _contribs else mo.md("_no contributions parsed_"),
            mo.callout(
                mo.md(f"**Resolved:** `{_resolved}`\n\n_{_why}_"),
                kind="success" if _resolved is not None else "danger",
            ),
        ]
    )
    return


@app.cell
def _(mo):
    from knot.protocols import ProtocolKind

    codegen_lens_pick = mo.ui.dropdown(
        options=[k.value for k in ProtocolKind],
        label="ProtocolKind (lens stance):",
        value="resolved",
    )
    return (codegen_lens_pick,)


@app.cell
def _(b2_spec, codegen_lens_pick, mo):
    from knot.codegen import generate_sdk
    from knot.protocols import ProtocolKind as _PK

    try:
        _kind = _PK(codegen_lens_pick.value)
        _src = generate_sdk(b2_spec.spec, _kind)
        _ok = True
    except Exception as _e:
        _src = f"{type(_e).__name__}: {_e}"
        _ok = False

    _preview = _src[:3000] + ("\n\n... (truncated; full source is the codegen output)" if len(_src) > 3000 else "")

    mo.vstack(
        [
            mo.md("## 7 · SDK codegen output (B2 spec)"),
            mo.md(
                "What `generate_sdk(b2_spec.spec, kind)` emits for the chosen "
                "lens. Same spec, different lens = different generated shapes "
                "for the same `Movie.year` slot. Under DISAGREEMENT_AWARE the "
                "comparison operators are absent; under RESOLVED they're present."
            ),
            codegen_lens_pick,
            mo.md(f"```python\n{_preview}\n```") if _ok else mo.callout(mo.md(_src), kind="danger"),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
