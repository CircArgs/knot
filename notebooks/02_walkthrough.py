"""knot — end-to-end walkthrough.

Twelve sections cover the full lifecycle: source registration → ontology
modelling → spec edit + impact tracing → draft impl + DataContext validation
→ bind → compile → dispatch via toy orchestrator → lake consumption →
materialized-target consumption → audit walk-back → multi-revision drafts
→ corrections overlay (OPEN until Round 4).

Every cell pokes real machinery — no status pages, no commit counts.

Stack: postgres + neo4j up via `bash scripts/up.sh`.
Lake: /tmp/knot_walkthrough_lake (created on first run of section 7).
"""

import marimo

__generated_with = "0.23.5"
app = marimo.App(width="medium")


# ===========================================================================
# Bootstrap
# ===========================================================================

@app.cell
def _():
    import sys as _sys
    from pathlib import Path

    REPO = Path("/mnt/main/code/knot")
    if str(REPO) not in _sys.path:
        _sys.path.insert(0, str(REPO))

    import marimo as mo
    import pandas as pd

    LAKE = Path("/tmp/knot_walkthrough_lake")
    DSN = "postgresql://knot:knot@localhost:5432/knot_control"
    NEO4J_URI = "bolt://localhost:7687"
    NEO4J_AUTH = ("neo4j", "knottest")
    NEO4J_DB = "neo4j"

    return DSN, LAKE, NEO4J_AUTH, NEO4J_DB, NEO4J_URI, REPO, mo, pd


@app.cell(hide_code=True)
def _(mo):
    mo.md(r"""
    # 02 — knot end-to-end walkthrough

    Real machinery for the full lifecycle. Every cell exercises actual code.

    **Prereqs:** stack up (`bash scripts/up.sh`).
    Lake at `/tmp/knot_walkthrough_lake`.

    1. Source registration (typed Pydantic)
    2. Ontology modelling — classes, slots, derivations, canonical hash
    3. Spec edit + impact tracing
    4. Draft impl + DataContext validation (live)
    5. Bind impl (postgres `bound_impls`)
    6. Compile → live `WorkflowSpec` + content-addressed hash
    7. Dispatch via toy orchestrator (button-gated)
    8. Lake consumption (DuckDB over `resolved_facts`)
    9. Materialized-target consumption (live Cypher against Neo4j)
    10. Audit walk-back (canonical fact → `KnotRun` → spec revs)
    11. Multi-revision drafts (new revision = new compile hash)
    12. Corrections overlay — **OPEN** until Round 4
    """)
    return


# ===========================================================================
# Section 1 — Source registration
# ===========================================================================

@app.cell
def _(mo, pd):
    from knot.metaschema import Source as _SourceCls
    from tests.fixtures.B2 import spec as b2_spec

    _all_sources = [
        getattr(b2_spec, _name)
        for _name in dir(b2_spec)
        if isinstance(getattr(b2_spec, _name, None), _SourceCls)
    ]

    _rows = [
        {
            "source": _s.name,
            "entity_class": _s.entity_class.name,
            "identifier_slot": _s.identifier_slot.name,
            "summary": (_s.description or "").split(".")[0],
        }
        for _s in _all_sources
    ]

    _by_class = {
        _c: sum(1 for _s in _all_sources if _s.entity_class.name == _c)
        for _c in ("Movie", "Person", "Credit")
    }

    mo.vstack([
        mo.md("## 1 · Source registration (typed Pydantic)"),
        mo.md(
            "Sources declare *what's in the lake* — name, real `OntologyClass` "
            "ref for `entity_class`, real `Slot` ref for `identifier_slot`. "
            "No connector code in knot; the team owns ingestion per "
            "`source-layer-contract.md`."
        ),
        mo.ui.table(pd.DataFrame(_rows), page_size=20, selection=None),
        mo.md(
            f"**{len(_all_sources)} sources** registered for B2: "
            + ", ".join(f"{n} {c}" for c, n in _by_class.items())
            + "."
        ),
    ])
    return (b2_spec,)


# ===========================================================================
# Section 2 — Ontology modelling
# ===========================================================================

@app.cell
def _(b2_spec, mo, pd):
    from knot.canonical import canonical_dump, compute_content_hash

    _bytes = canonical_dump(b2_spec.spec)
    _hash = compute_content_hash(b2_spec.spec)

    _class_rows = []
    for _cls in b2_spec.spec.classes:
        _stored = sum(1 for _s in _cls.slots if _s.derivation is None)
        _derived = sum(1 for _s in _cls.slots if _s.derivation is not None)
        _class_rows.append({
            "class": _cls.name,
            "slots_total": len(_cls.slots),
            "stored": _stored,
            "derived": _derived,
            "description": (_cls.description or "")[:80],
        })

    mo.vstack([
        mo.md("## 2 · Ontology modelling — classes, slots, derivations"),
        mo.md(
            "B2 spec: 3 classes, 19 slots (4 derived from `Credit.role`).  "
            "The whole spec is content-addressed: edit any field, the hash "
            "changes; equivalent revisions hash identically (commitment 3)."
        ),
        mo.ui.table(pd.DataFrame(_class_rows), page_size=10, selection=None),
        mo.callout(
            mo.md(
                f"**spec_content_hash:** `{_hash}`  \n"
                f"**canonical_dump bytes:** {len(_bytes)} (RFC 8785 JCS)"
            ),
            kind="info",
        ),
    ])
    return


@app.cell
def _(b2_spec, mo):
    slot_pick = mo.ui.dropdown(
        options=[
            f"{_c.name}.{_s.name}"
            for _c in b2_spec.spec.classes
            for _s in _c.slots
        ],
        label="Slot to inspect:",
        value="Movie.year",
    )
    slot_pick
    return (slot_pick,)


@app.cell
def _(b2_spec, mo, slot_pick):
    _path = slot_pick.value
    _cls_name, _slot_name = _path.split(".", 1)
    _cls = next(_c for _c in b2_spec.spec.classes if _c.name == _cls_name)
    _slot = next(_s for _s in _cls.slots if _s.name == _slot_name)

    _range = _slot.range.name if _slot.range else "None"
    _rp_raw = _slot.resolution_policy
    _rp = (
        _rp_raw.value if hasattr(_rp_raw, "value")
        else (str(_rp_raw) if _rp_raw else "—")
    )
    _deriv = type(_slot.derivation).__name__ if _slot.derivation is not None else "—"

    mo.callout(
        mo.md(
            f"**{_path}**  \n"
            f"- range: `{_range}`  \n"
            f"- multivalued: `{_slot.multivalued}` · required: `{_slot.required}` · identifier: `{_slot.identifier}`  \n"
            f"- resolution_policy: `{_rp}`  \n"
            f"- derivation: `{_deriv}`  \n"
            f"- description: {_slot.description or '_(none)_'}"
        ),
        kind="info",
    )
    return


# ===========================================================================
# Section 3 — Spec edit + impact tracing
# ===========================================================================

@app.cell
def _(mo):
    edit_pick = mo.ui.dropdown(
        options=[
            "rename_slot Movie.year → Movie.release_year",
            "rename_slot Person.name → Person.full_name",
            "delete_slot Credit.role",
            "rename_class Movie → Film",
        ],
        value="rename_slot Movie.year → Movie.release_year",
        label="Hypothetical spec edit:",
    )
    edit_pick
    return (edit_pick,)


@app.cell
def _(edit_pick, mo):
    import sys as _sys
    import types as _types
    from dataclasses import dataclass as _dc

    @_dc
    class _SpecEdit:
        edit_type: str
        payload: dict

    @_dc
    class _Impact:
        affected_classes: set
        affected_impls: set
        affected_workflows: set

    if "tests.test_env" not in _sys.modules:
        _mod = _types.ModuleType("tests.test_env")
        _mod.Impact = _Impact
        _mod.SpecEdit = _SpecEdit
        _sys.modules["tests.test_env"] = _mod

    _v = edit_pick.value
    if _v.startswith("rename_slot Movie.year"):
        _se = _SpecEdit("rename_slot", {"from_path": "Movie.year", "to_name": "release_year"})
    elif _v.startswith("rename_slot Person.name"):
        _se = _SpecEdit("rename_slot", {"from_path": "Person.name", "to_name": "full_name"})
    elif _v.startswith("delete_slot"):
        _se = _SpecEdit("delete_slot", {"path": "Credit.role"})
    else:
        _se = _SpecEdit("rename_class", {"from_name": "Movie", "to_name": "Film"})

    from knot.impact import BoundImpl, affected_workflows
    from tests.fixtures.B2.impls import er_credit, er_movie, er_person, neo4j_publisher

    _impls = [
        BoundImpl(er_movie.ERMovie, "ERMovie", "Movie"),
        BoundImpl(er_person.ERPerson, "ERPerson", "Person"),
        BoundImpl(er_credit.ERCredit, "ERCredit", "Credit"),
        BoundImpl(neo4j_publisher.Neo4jPublisher, "Neo4jPublisher", "Movie"),
    ]
    _impact = affected_workflows(_se, _impls)

    mo.vstack([
        mo.md("## 3 · Spec edit + impact tracing"),
        mo.md(
            "Single-dispatch over the typed entity tree; walks every bound "
            "impl's DataContexts and surfaces which ones reference the edit "
            "target.  No parallel meta-graph (commitment 10)."
        ),
        mo.callout(
            mo.md(
                f"**edit_type:** `{_se.edit_type}`  \n"
                f"**payload:** `{_se.payload}`  \n"
                f"**affected_classes:** `{sorted(_impact.affected_classes)}`  \n"
                f"**affected_impls:** `{sorted(_impact.affected_impls)}`  \n"
                f"**affected_workflows:** `{sorted(_impact.affected_workflows)}`"
            ),
            kind="warn" if _impact.affected_impls else "success",
        ),
    ])
    return


# ===========================================================================
# Section 4 — Draft impl + DataContext validation
# ===========================================================================

@app.cell
def _(mo):
    impl_source = mo.ui.text_area(
        label="Impl source (Python):",
        value=(
            "from typing import ClassVar\n"
            "from knot.protocols import DataContext, ERProtocol, ERResult, ScoreColumnMap\n"
            "from tests.fixtures.B2.spec import Movie, imdb_movies\n"
            "\n"
            "class DemoER(ERProtocol):\n"
            "    candidates: ClassVar[DataContext] = DataContext(\n"
            "        primary=Movie,\n"
            "        where=Movie.imdb_id.from_source(imdb_movies).is_not_null(),\n"
            "    )\n"
            "    def score(self, ctx, **dcs) -> ERResult:\n"
            "        return ERResult(\n"
            "            output_uri=\"\",\n"
            "            column_map=ScoreColumnMap(\n"
            "                a_canonical=\"a\", b_canonical=\"b\", score=\"score\"\n"
            "            ),\n"
            "        )\n"
        ),
        rows=14,
        full_width=True,
    )
    impl_source
    return (impl_source,)


@app.cell
def _(b2_spec, impl_source, mo):
    from knot.protocols import Protocol as _ProtocolBase
    from knot.registration import DataContextValidationError, validate_datacontexts

    _ns: dict = {}
    _exec_err = None
    try:
        exec(impl_source.value, _ns)
    except Exception as _e:
        _exec_err = f"{type(_e).__name__}: {_e}"

    _cls = None
    if _exec_err is None:
        for _v in _ns.values():
            if (
                isinstance(_v, type)
                and issubclass(_v, _ProtocolBase)
                and _v is not _ProtocolBase
            ):
                _cls = _v
                break

    if _exec_err is not None:
        _msg = f"exec failed: `{_exec_err}`"
        _kind = "danger"
    elif _cls is None:
        _msg = "Could not find a `Protocol` subclass in the source."
        _kind = "warn"
    else:
        try:
            validate_datacontexts(_cls, b2_spec.spec)
            _msg = f"✓ DataContexts on `{_cls.__name__}` validate against B2 spec."
            _kind = "success"
        except DataContextValidationError as _e:
            _msg = f"`DataContextValidationError`: {_e}"
            _kind = "danger"

    mo.vstack([
        mo.md("## 4 · Draft impl + DataContext validation"),
        mo.md(
            "Edit the source.  knot `exec()`s it (trusted-author posture, "
            "commitment 5) and walks every `DataContext` attribute.  Broken "
            "refs raise loudly with `did_you_mean` hints (commitment 16)."
        ),
        mo.callout(mo.md(_msg), kind=_kind),
    ])
    return


# ===========================================================================
# Section 5 — Bind impl (postgres bound_impls)
# ===========================================================================

@app.cell
def _(DSN, mo, pd):
    import psycopg as _pg

    try:
        with _pg.connect(DSN, autocommit=True) as _conn:
            _rows = _conn.execute(
                "SELECT stage, class_name, impl_name, current_revision, "
                "current_config_revision, updated_at "
                "FROM bound_impls ORDER BY stage, class_name"
            ).fetchall()
        _err = None
    except Exception as _e:
        _rows = []
        _err = f"{type(_e).__name__}: {_e}"

    _df = pd.DataFrame(
        _rows,
        columns=["stage", "class_name", "impl_name", "rev", "cfg_rev", "updated_at"],
    )

    mo.vstack([
        mo.md("## 5 · Bind impl (postgres `bound_impls`)"),
        mo.md(
            "One impl per `(stage, class_name)`.  Section 7 below populates "
            "this table when you click **Run pipeline**; on first load it's "
            "empty (or shows leftovers from a prior run)."
        ),
        (
            mo.callout(mo.md(f"DB error: `{_err}`"), kind="danger") if _err
            else mo.ui.table(_df, page_size=20, selection=None) if len(_df)
            else mo.md("_no bound impls — run section 7_")
        ),
    ])
    return


# ===========================================================================
# Section 6 — Live compile → WorkflowSpec
# ===========================================================================

@app.cell
def _(mo):
    scope_pick = mo.ui.dropdown(
        options=["Movie", "Person", "Credit", "full"],
        label="Compile scope:",
        value="Movie",
    )
    scope_pick
    return (scope_pick,)


@app.cell
def _(b2_spec, mo, pd, scope_pick):
    from knot.compiler import compile as _compile, compile_hash as _ch_fn

    _watermarks = {
        "imdb_movies": "wm1", "tmdb_movies": "wm2", "wikidata_movies": "wm3",
        "imdb_persons": "wm4", "tmdb_persons": "wm5",
        "imdb_credits": "wm6", "tmdb_credits": "wm7", "wikidata_credits": "wm8",
    }

    try:
        _wf = _compile(
            spec=b2_spec.spec,
            bound_impls=[],
            impl_configs={},
            source_watermarks=_watermarks,
            scope=scope_pick.value,
        )
        _hash = _ch_fn(_wf)
        _stage_rows = [
            {
                "kind": _s.kind,
                "class": _s.class_name or "—",
                "impl": _s.impl_name or "—",
                "cache_key": _s.cache_key[:12] + "…",
                "pinned_parents": (
                    ", ".join(f"{k}={v[:8]}" for k, v in _s.pinned_parent_runs.items())
                    if _s.pinned_parent_runs else "—"
                ),
            }
            for _s in _wf.stages
        ]
        _err = None
    except Exception as _e:
        _hash = None
        _stage_rows = []
        _err = f"{type(_e).__name__}: {_e}"

    mo.vstack([
        mo.md("## 6 · Live compile → `WorkflowSpec`"),
        mo.md(
            "`knot.compiler.compile()` reads the spec + bound impls + configs + "
            "watermarks and emits a typed `WorkflowSpec` with toposorted stages, "
            "per-stage cache keys, and (for relation classes) pinned parent run "
            "hashes.  `compile_hash` is the run identity (commitment 3)."
        ),
        (
            mo.callout(mo.md(f"compile error: `{_err}`"), kind="danger") if _err
            else mo.vstack([
                mo.callout(
                    mo.md(f"**compile_hash:** `{_hash}` · **stages:** {len(_stage_rows)}"),
                    kind="success",
                ),
                mo.ui.table(pd.DataFrame(_stage_rows), page_size=30, selection=None),
            ])
        ),
    ])
    return


# ===========================================================================
# Section 7 — Dispatch via toy orchestrator
# ===========================================================================

@app.cell
def _(mo):
    run_pipeline_btn = mo.ui.run_button(
        label="▶ Run end-to-end pipeline (resets state)"
    )
    run_pipeline_btn
    return (run_pipeline_btn,)


@app.cell
def _(DSN, LAKE, REPO, b2_spec, mo, pd, run_pipeline_btn):
    """Reset state, register impls, bind, compile, dispatch via ToyOrchestrator.

    Gated on the run-button so headless `python notebooks/02_walkthrough.py`
    does not fire the destructive reset.
    """
    import json as _json
    import shutil as _shutil
    import warnings as _warnings

    import psycopg as _pg

    if not run_pipeline_btn.value:
        _content = mo.md(
            "_Click **▶ Run end-to-end pipeline** to reset state, register "
            "fixture impls, bind, compile, and dispatch._"
        )
    else:
        from knot.compiler import compile as _compile
        from knot.impact import BoundImpl as _BI
        from knot.orchestrator import ToyOrchestrator, ToyOrchestratorConfig
        from tests.fixtures.B2.spec import (
            imdb_credits, imdb_movies, imdb_persons,
            tmdb_credits, tmdb_movies, tmdb_persons,
            wikidata_credits, wikidata_movies,
        )

        # 1. Reset lake_dir
        if LAKE.exists():
            _shutil.rmtree(LAKE)
        LAKE.mkdir(parents=True)
        (LAKE / "sources").mkdir()
        for _csv in (REPO / "tests/fixtures/B2/sources").glob("*.csv"):
            (LAKE / "sources" / _csv.name).symlink_to(_csv)

        # 2. Reset postgres tables
        with _pg.connect(DSN, autocommit=True) as _conn:
            for _t in (
                "pipeline_runs", "compiled_workflows", "bound_impls",
                "impl_config", "impl_revision",
                "_user_corrections", "_user_er_decisions",
            ):
                _conn.execute(f"DELETE FROM {_t}")

        # 3. Register impl revisions + configs + bind
        _b2_dir = REPO / "tests/fixtures/B2/impls"
        _impls_meta = [
            ("ERMovie",  "Movie",  _b2_dir / "er_movie.py", {
                "blocking_threshold": 0.6, "title_weight": 0.7,
                "year_tolerance": 1,
                "trust_imdb": 0.91, "trust_tmdb": 0.62, "trust_wikidata": 0.48,
            }),
            ("ERPerson", "Person", _b2_dir / "er_person.py", {
                "name_similarity_threshold": 0.85, "use_wikidata_id": True,
                "trust_imdb": 0.88, "trust_tmdb": 0.65,
            }),
            ("ERCredit", "Credit", _b2_dir / "er_credit.py", {
                "trust_imdb": 0.90, "trust_tmdb": 0.60, "trust_wikidata": 0.40,
            }),
        ]
        _registered = []
        with _pg.connect(DSN, autocommit=True) as _conn:
            for _name, _cls, _path, _cfg in _impls_meta:
                _src = _path.read_text()
                _conn.execute(
                    "INSERT INTO impl_revision (name, revision, source_bytes, "
                    "content_hash, pinned_spec_hash) VALUES (%s, %s, %s, %s, %s)",
                    (_name, 1, _src.encode(), f"hash_{_name}_v1", "spec_v1"),
                )
                _conn.execute(
                    "INSERT INTO impl_config (impl_name, revision, "
                    "config_snapshot, content_hash) VALUES (%s, %s, %s, %s)",
                    (_name, 1, _json.dumps(_cfg), f"cfg_{_name}_v1"),
                )
                _conn.execute(
                    "INSERT INTO bound_impls (stage, class_name, impl_name, "
                    "current_revision, current_config_revision) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    ("resolve", _cls, _name, 1, 1),
                )
                _registered.append({
                    "impl": _name, "stage": "resolve", "class": _cls,
                    "rev": 1, "cfg_rev": 1,
                })

        # 4. Compile + dispatch
        _all_sources = [
            imdb_movies, tmdb_movies, wikidata_movies,
            imdb_persons, tmdb_persons,
            imdb_credits, tmdb_credits, wikidata_credits,
        ]
        _watermarks = {_s.name: f"wm_{_s.name}" for _s in _all_sources}

        _bindings = [
            _BI(object, _name, _cls)
            for _name, _cls, _, _ in _impls_meta
        ]
        _wf = _compile(
            spec=b2_spec.spec,
            bound_impls=_bindings,
            impl_configs={
                _name: {"_impl_revision": 1, "_revision": 1}
                for _name, _, _, _ in _impls_meta
            },
            source_watermarks=_watermarks,
            scope="full",
        )

        _orch = ToyOrchestrator(ToyOrchestratorConfig(
            lake_dir=LAKE, postgres_dsn=DSN, sources=_all_sources,
        ))
        _run_id = _orch.insert_run(_wf, scope="full")

        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore", UserWarning)
            try:
                _orch.dispatch(_wf, _run_id)
                _err = None
            except Exception as _e:
                _err = f"{type(_e).__name__}: {_e}"

        with _pg.connect(DSN, autocommit=True) as _conn:
            _run_row = _conn.execute(
                "SELECT id, compile_hash, scope, status, started_at, "
                "completed_at, error FROM pipeline_runs WHERE id = %s",
                (_run_id,),
            ).fetchone()

        _parquets = sorted(p.relative_to(LAKE) for p in LAKE.rglob("*.parquet"))

        _status_kind = "success" if _run_row and _run_row[3] == "succeeded" else "danger"
        _content = mo.vstack([
            mo.callout(
                mo.md(
                    f"**run_id:** `{_run_row[0]}` · **status:** `{_run_row[3]}`  \n"
                    f"**compile_hash:** `{_run_row[1]}`  \n"
                    f"**started:** `{_run_row[4]}` → **completed:** `{_run_row[5]}`"
                    + (f"  \n**error:** `{_run_row[6]}`" if _run_row[6] else "")
                ),
                kind=_status_kind,
            ),
            mo.md(f"**Bound impls registered:** {len(_registered)}"),
            mo.ui.table(pd.DataFrame(_registered), page_size=10, selection=None),
            mo.md(f"**Parquet files written ({len(_parquets)}):**"),
            (
                mo.ui.table(
                    pd.DataFrame({"path": [str(p) for p in _parquets]}),
                    page_size=20, selection=None,
                ) if _parquets else mo.md("_(no parquet files)_")
            ),
        ])

    mo.vstack([
        mo.md("## 7 · Dispatch via toy orchestrator"),
        mo.md(
            "Wires real B2 fixture impls (rapidfuzz ER for Movie / Person / "
            "Credit) into postgres `bound_impls`, compiles the full workflow, "
            "and dispatches via `ToyOrchestrator`.  Lake at "
            "`/tmp/knot_walkthrough_lake`; postgres `pipeline_runs` carries "
            "the run record.  Resolve stages call into bound impls; publish "
            "and dq stages skip with warnings (no impls bound here)."
        ),
        _content,
    ])
    return


# ===========================================================================
# Section 8 — Lake consumption
# ===========================================================================

@app.cell
def _(LAKE, mo, pd):
    _resolved = LAKE / "resolved_facts" / "Movie" / "data.parquet"

    if not _resolved.exists():
        _content = mo.md(
            "_Run section 7 first to populate `resolved_facts/Movie/data.parquet`._"
        )
    else:
        from knot.lake.duckdb_reader import DuckDBReader, DuckDBReaderConfig

        _reader = DuckDBReader(DuckDBReaderConfig(lake_dir=LAKE))
        _reader.register_parquet_view("rf_movie", _resolved)

        _df = _reader.read(
            None,
            "SELECT _knot_source, COUNT(*) AS n_rows, "
            "COUNT(DISTINCT imdb_id) AS distinct_movies "
            "FROM rf_movie GROUP BY _knot_source ORDER BY _knot_source",
        ).to_pandas()

        _df_sample = _reader.read(
            None,
            "SELECT _knot_source, imdb_id, title, year, runtime_minutes "
            "FROM rf_movie ORDER BY imdb_id, _knot_source LIMIT 12",
        ).to_pandas()

        _content = mo.vstack([
            mo.md("**Per-source contribution counts** (multi-valued; no winner):"),
            mo.ui.table(_df, page_size=10, selection=None),
            mo.md("**First 12 contribution rows:**"),
            mo.ui.table(_df_sample, page_size=20, selection=None),
        ])

    mo.vstack([
        mo.md("## 8 · Lake consumption (DuckDB over `resolved_facts`)"),
        mo.md(
            "knot's built-in lake query path: SQL via `sql_gen` → "
            "`QueryReader.read()` → Arrow.  `resolved_facts` retains every "
            "source's contribution; trust resolution is query-time, not "
            "baked into the table (commitment 7)."
        ),
        _content,
    ])
    return


# ===========================================================================
# Section 9 — Materialized-target consumption (live Cypher)
# ===========================================================================

@app.cell
def _(mo):
    publish_btn = mo.ui.run_button(
        label="▶ Publish resolved Movies to Neo4j (clears db, writes nodes + audit)"
    )
    publish_btn
    return (publish_btn,)


@app.cell
def _(DSN, LAKE, NEO4J_AUTH, NEO4J_DB, NEO4J_URI, mo, pd, publish_btn):
    """Direct Neo4j write — sidesteps the orchestrator multi-class fan-out
    drift (see STATE.md "Open design tensions").  Demonstrates real
    materialized graph + audit affordance.
    """
    if not publish_btn.value:
        _content = mo.md(
            "_Click **▶ Publish resolved Movies to Neo4j** to clear the target "
            "database and write the resolved Movie graph + a `:KnotRun` audit node._"
        )
    else:
        _resolved = LAKE / "resolved_facts" / "Movie" / "data.parquet"
        if not _resolved.exists():
            _content = mo.callout(
                mo.md("Run section 7 first — no resolved_facts to publish."),
                kind="warn",
            )
        else:
            from datetime import datetime as _dt, timezone as _tz

            import neo4j as _neo4j
            import psycopg as _pg
            import pyarrow.parquet as _pq

            _table = _pq.read_table(_resolved)
            _all_rows = _table.to_pylist()

            # Deduplicate per imdb_id (UNIQUE_OR_FAIL by spec).
            _by_id: dict[str, dict] = {}
            for _row in _all_rows:
                _id = _row.get("imdb_id")
                if _id and _id not in _by_id:
                    _by_id[_id] = _row

            with _pg.connect(DSN, autocommit=True) as _conn:
                _r = _conn.execute(
                    "SELECT id, compile_hash FROM pipeline_runs "
                    "WHERE status = 'succeeded' ORDER BY id DESC LIMIT 1"
                ).fetchone()
            _run_id, _compile_hash = (_r if _r else (None, None))

            _driver = _neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
            try:
                with _driver.session(database=NEO4J_DB) as _sess:
                    _sess.run("MATCH (n) DETACH DELETE n")
                    _sess.run(
                        "UNWIND $rows AS row "
                        "MERGE (m:Movie {imdb_id: row.imdb_id}) "
                        "SET m.title = row.title, m.year = row.year, "
                        "m.runtime_minutes = row.runtime_minutes, "
                        "m.canonical_id = row.imdb_id",
                        rows=[
                            {
                                "imdb_id": _r2["imdb_id"],
                                "title": _r2.get("title"),
                                "year": _r2.get("year"),
                                "runtime_minutes": _r2.get("runtime_minutes"),
                            }
                            for _r2 in _by_id.values()
                        ],
                    )
                    _sess.run(
                        "CREATE (k:KnotRun {compile_hash: $ch, run_id: $rid, "
                        "completed_at: $ts})",
                        ch=_compile_hash, rid=_run_id,
                        ts=_dt.now(_tz.utc).isoformat(),
                    )
                    _audit = _sess.run(
                        "MATCH (k:KnotRun) "
                        "RETURN k.compile_hash AS hash, k.run_id AS run_id, "
                        "k.completed_at AS ts"
                    ).data()
                    _movie_count = _sess.run(
                        "MATCH (m:Movie) RETURN count(m) AS n"
                    ).single()["n"]
            finally:
                _driver.close()

            _content = mo.vstack([
                mo.callout(
                    mo.md(
                        f"**Published:** {_movie_count} `:Movie` nodes  \n"
                        f"**KnotRun audit:** {len(_audit)} node(s)"
                    ),
                    kind="success",
                ),
                mo.md("**`MATCH (k:KnotRun) RETURN k`** — live Cypher result:"),
                mo.ui.table(pd.DataFrame(_audit), page_size=10, selection=None),
            ])

    mo.vstack([
        mo.md("## 9 · Materialized-target consumption (live Cypher)"),
        mo.md(
            "Bypasses the orchestrator publish stage (known multi-class "
            "handoff drift in STATE.md *Open design tensions*) and writes "
            "`:Movie` nodes + a `:KnotRun` audit node directly via the neo4j "
            "driver.  Demonstrates real materialized graph + audit affordance."
        ),
        _content,
    ])
    return


# ===========================================================================
# Section 10 — Audit walk-back
# ===========================================================================

@app.cell
def _(DSN, NEO4J_AUTH, NEO4J_DB, NEO4J_URI, mo, pd):
    import json as _json

    import neo4j as _neo4j
    import psycopg as _pg

    try:
        _driver = _neo4j.GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)
        try:
            with _driver.session(database=NEO4J_DB) as _sess:
                _audit = _sess.run(
                    "MATCH (k:KnotRun) "
                    "RETURN k.compile_hash AS hash, k.run_id AS run_id, "
                    "k.completed_at AS ts ORDER BY k.completed_at DESC LIMIT 5"
                ).data()
        finally:
            _driver.close()
        _neo4j_err = None
    except Exception as _e:
        _audit = []
        _neo4j_err = f"{type(_e).__name__}: {_e}"

    if _neo4j_err:
        _content = mo.callout(mo.md(f"Neo4j error: `{_neo4j_err}`"), kind="danger")
    elif not _audit:
        _content = mo.md(
            "_Run sections 7 + 9 first to create a `:KnotRun` audit node._"
        )
    else:
        _ch = _audit[0]["hash"]
        with _pg.connect(DSN) as _conn:
            _runs = _conn.execute(
                "SELECT id, status, started_at, completed_at "
                "FROM pipeline_runs WHERE compile_hash = %s",
                (_ch,),
            ).fetchall()
            _spec_row = _conn.execute(
                "SELECT spec FROM compiled_workflows WHERE hash = %s",
                (_ch,),
            ).fetchone()

        _spec_json = _spec_row[0] if _spec_row else None
        if isinstance(_spec_json, str):
            _spec_json = _json.loads(_spec_json)
        _revs = (_spec_json or {}).get("spec_revision_ids", {})
        _stages = (_spec_json or {}).get("stages", [])

        _runs_df = pd.DataFrame(
            _runs, columns=["id", "status", "started_at", "completed_at"],
        )
        _stage_rows = [
            {
                "kind": _s.get("kind"),
                "class": _s.get("class_name"),
                "impl": _s.get("impl_name"),
                "cache_key": (_s.get("cache_key") or "")[:12] + "…",
            }
            for _s in _stages
        ]

        _content = mo.vstack([
            mo.callout(
                mo.md(
                    f"**`:KnotRun.compile_hash`** → `{_ch}`  \n"
                    f"→ `pipeline_runs`: {len(_runs)} match(es)  \n"
                    f"→ `compiled_workflows.spec_revision_ids`: `{_revs}`  \n"
                    f"→ {len(_stages)} pinned stages"
                ),
                kind="success",
            ),
            mo.md("**`pipeline_runs` for this compile_hash:**"),
            mo.ui.table(_runs_df, page_size=10, selection=None),
            mo.md("**Pinned stages from the compiled WorkflowSpec:**"),
            mo.ui.table(pd.DataFrame(_stage_rows), page_size=30, selection=None),
        ])

    mo.vstack([
        mo.md("## 10 · Audit walk-back (Neo4j fact → KnotRun → spec revs)"),
        mo.md(
            "Mechanical walk: `:KnotRun.compile_hash` → "
            "`pipeline_runs.compile_hash` → `compiled_workflows.spec` → "
            "pinned spec revision ids + pinned stages.  Deterministic, "
            "queryable as data, no exploratory hunt (commitment 3 + audit goal)."
        ),
        _content,
    ])
    return


# ===========================================================================
# Section 11 — Multi-revision drafts
# ===========================================================================

@app.cell
def _(mo):
    new_rev_btn = mo.ui.run_button(
        label="▶ Register ERMovie revision 2 (new threshold) and recompile"
    )
    new_rev_btn
    return (new_rev_btn,)


@app.cell
def _(DSN, REPO, b2_spec, mo, new_rev_btn, pd):
    import json as _json

    import psycopg as _pg

    if not new_rev_btn.value:
        _content = mo.md(
            "_Click **▶ Register ERMovie revision 2** to register a new impl "
            "revision with a different threshold and see the compile_hash change._"
        )
    else:
        from knot.compiler import compile as _compile, compile_hash as _ch_fn
        from knot.impact import BoundImpl as _BI

        _src = (REPO / "tests/fixtures/B2/impls/er_movie.py").read_text()
        _new_config = {
            "blocking_threshold": 0.7,
            "title_weight": 0.85,
            "year_tolerance": 2,
            "trust_imdb": 0.93,
            "trust_tmdb": 0.55,
            "trust_wikidata": 0.40,
        }

        with _pg.connect(DSN, autocommit=True) as _conn:
            _max_rev = _conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM impl_revision "
                "WHERE name = 'ERMovie'"
            ).fetchone()[0]
            _new_rev = _max_rev + 1
            _conn.execute(
                "INSERT INTO impl_revision (name, revision, source_bytes, "
                "content_hash, pinned_spec_hash) VALUES (%s, %s, %s, %s, %s)",
                ("ERMovie", _new_rev, _src.encode(),
                 f"hash_ERMovie_v{_new_rev}", "spec_v1"),
            )
            _max_cfg = _conn.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM impl_config "
                "WHERE impl_name = 'ERMovie'"
            ).fetchone()[0]
            _new_cfg = _max_cfg + 1
            _conn.execute(
                "INSERT INTO impl_config (impl_name, revision, "
                "config_snapshot, content_hash) VALUES (%s, %s, %s, %s)",
                ("ERMovie", _new_cfg, _json.dumps(_new_config),
                 f"cfg_ERMovie_v{_new_cfg}"),
            )
            _conn.execute(
                "UPDATE bound_impls SET current_revision = %s, "
                "current_config_revision = %s, updated_at = now() "
                "WHERE stage = 'resolve' AND class_name = 'Movie'",
                (_new_rev, _new_cfg),
            )

        _watermarks = {
            "imdb_movies": "wm1", "tmdb_movies": "wm2", "wikidata_movies": "wm3",
            "imdb_persons": "wm4", "tmdb_persons": "wm5",
            "imdb_credits": "wm6", "tmdb_credits": "wm7", "wikidata_credits": "wm8",
        }

        _wf_old = _compile(
            spec=b2_spec.spec,
            bound_impls=[_BI(object, "ERMovie", "Movie")],
            impl_configs={"ERMovie": {"_impl_revision": 1, "_revision": 1}},
            source_watermarks=_watermarks,
            scope="Movie",
        )
        _wf_new = _compile(
            spec=b2_spec.spec,
            bound_impls=[_BI(object, "ERMovie", "Movie")],
            impl_configs={"ERMovie": {"_impl_revision": _new_rev, "_revision": _new_cfg}},
            source_watermarks=_watermarks,
            scope="Movie",
        )
        _h_old = _ch_fn(_wf_old)
        _h_new = _ch_fn(_wf_new)

        _hashes_df = pd.DataFrame([
            {"label": "old (rev 1, cfg 1)", "compile_hash": _h_old},
            {"label": f"new (rev {_new_rev}, cfg {_new_cfg})", "compile_hash": _h_new},
        ])

        _content = mo.vstack([
            mo.callout(
                mo.md(
                    f"**ERMovie revision {_new_rev} registered**, "
                    f"config revision {_new_cfg} (threshold 0.7, "
                    f"year_tolerance 2)  \n"
                    f"**bound_impls re-pointed at the new revision.**"
                ),
                kind="success",
            ),
            mo.md("**Compile hash comparison** (Movie scope):"),
            mo.ui.table(_hashes_df, page_size=5, selection=None),
            mo.md(
                f"Old runs that referenced `compile_hash = {_h_old[:12]}…` "
                "remain valid historical artifacts — `compiled_workflows` is "
                "content-addressed, retained forever, and old `pipeline_runs` "
                "rows still walk back through them."
            ),
        ])

    mo.vstack([
        mo.md("## 11 · Multi-revision drafts"),
        mo.md(
            "Register a new impl revision + new config revision; rebind; "
            "recompile.  The compile_hash changes; old runs survive as "
            "historical artifacts (commitment 3 + audit walk-back stays valid)."
        ),
        _content,
    ])
    return


# ===========================================================================
# Section 12 — Corrections overlay (OPEN)
# ===========================================================================

@app.cell
def _(mo):
    mo.vstack([
        mo.md("## 12 · Corrections overlay — **OPEN**"),
        mo.callout(
            mo.md(
                "**Open until Round 4 lands.**  \n\n"
                "Per `pipeline-stages.md` § *In-flight corrections overlay*: "
                "`POST /corrections` writes to `_user_corrections` "
                "(postgres-control) **today** — the API endpoint exists.  "
                "The query-time SDK overlay that joins postgres on top of "
                "`resolved_facts` for consumer-facing reads is **not yet "
                "implemented**.\n\n"
                "When Round 4 (translator endpoint + correction overlay path) "
                "lands, this section will demonstrate:\n\n"
                "1. `POST /corrections` for `Movie.runtime_minutes`  \n"
                "2. `POST /query` against `Movie.runtime_minutes` — fresh "
                "value visible immediately via the postgres overlay  \n"
                "3. Pipeline-stage impls (Materializer / Translator over "
                "non-lake targets) still see lake-only views — replay stays "
                "deterministic at the pinned watermark."
            ),
            kind="warn",
        ),
    ])
    return


if __name__ == "__main__":
    app.run()
