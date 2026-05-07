"""knot FastAPI surface — compiler + control schema over HTTP.

Spec source: loaded from tests.fixtures.B2 at startup.
TODO: replace with "load spec from postgres" when that slice is built.

Single-team posture per core-design.md commitment 5.
No auth, no rate limiting, no multi-tenant defenses.
Network access control is the team's responsibility.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import psycopg
from fastapi import FastAPI, HTTPException

from knot.canonical import canonical_dump, compute_content_hash
from knot.compiler import compile as knot_compile, compile_hash
from knot.control_db import apply_schema
from knot.impact import BoundImpl
from knot.registration import DataContextValidationError, validate_datacontexts

from knot.api_models import (
    BoundImplListItem,
    BoundImplRequest,
    BoundImplResponse,
    CompiledWorkflowListItem,
    CompiledWorkflowResponse,
    ConfigRevisionRequest,
    ConfigRevisionResponse,
    CorrectionRequest,
    CorrectionResponse,
    ERDecisionRequest,
    ERDecisionResponse,
    ImplRevisionRequest,
    ImplRevisionResponse,
    ImplRevisionRow,
    RunRequest,
    RunResponse,
    RunStatusResponse,
)

# ---------------------------------------------------------------------------
# App + startup
# ---------------------------------------------------------------------------

# KNOT_CONTROL_DSN — postgres DSN for the knot control database.
# Default targets the local dev stack (docker-compose).
DSN = os.environ.get("KNOT_CONTROL_DSN", "postgresql://knot:knot@localhost:5432/knot_control")

app = FastAPI(title="knot", description="Knowledge graph + ontology compiler")


def _get_conn() -> psycopg.Connection:
    return psycopg.connect(DSN, autocommit=True)


# Load spec at import time from B2 fixture.
# TODO: replace with postgres-backed spec loading in a future slice.
from tests.fixtures.B2.spec import spec as _SPEC  # noqa: E402


def _spec_hash() -> str:
    return compute_content_hash(_SPEC)


# ---------------------------------------------------------------------------
# Helper: exec impl source into a fresh namespace, return the class named by
# impl_name (exact match required).
# Per core-design.md commitment 5: trusted authors, no sandboxing.
# ---------------------------------------------------------------------------

def _load_impl_class(source: str, impl_name: str) -> type:
    """exec() source into a fresh dict; return the class named impl_name.

    Trusted-author exec per commitment 5: no sandbox, no restricted Python.
    Exact name match required — raises HTTPException(400) if not found.
    """
    ns: dict[str, Any] = {}
    try:
        exec(source, ns)  # noqa: S102 (trusted team impl source)
    except Exception as exc:
        raise ValueError(f"Source failed to execute: {exc}") from exc

    candidate = ns.get(impl_name)
    if isinstance(candidate, type):
        return candidate

    raise HTTPException(
        status_code=400,
        detail=f"Source defines no class named {impl_name!r}. "
               f"Ensure the class name exactly matches the impl name.",
    )


def _content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _content_hash_str(s: str) -> str:
    return _content_hash_bytes(s.encode())


def _content_hash_json(obj: Any) -> str:
    return _content_hash_bytes(
        json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()
    )


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# POST /impls/{name}/revisions
# ---------------------------------------------------------------------------

@app.post("/impls/{name}/revisions", response_model=ImplRevisionResponse)
def post_impl_revision(name: str, body: ImplRevisionRequest) -> ImplRevisionResponse:
    """Register a new impl source revision.

    Executes the source, validates DataContexts against the current spec,
    writes to impl_revision, returns revision + hashes.
    """
    source = body.source_bytes
    try:
        impl_class = _load_impl_class(source, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        validate_datacontexts(impl_class, _SPEC)
    except DataContextValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    content_hash = _content_hash_str(source)
    pinned_spec_hash = _spec_hash()

    with _get_conn() as conn:
        # Next revision = max(existing) + 1, or 1.
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM impl_revision WHERE name = %s",
            (name,),
        ).fetchone()
        next_rev = (row[0] if row else 0) + 1

        conn.execute(
            """
            INSERT INTO impl_revision (name, revision, source_bytes, content_hash, pinned_spec_hash)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (name, next_rev, source.encode(), content_hash, pinned_spec_hash),
        )

    return ImplRevisionResponse(
        revision=next_rev,
        content_hash=content_hash,
        pinned_spec_hash=pinned_spec_hash,
    )


# ---------------------------------------------------------------------------
# GET /impls/{name}/revisions
# ---------------------------------------------------------------------------

@app.get("/impls/{name}/revisions", response_model=list[ImplRevisionRow])
def list_impl_revisions(name: str) -> list[ImplRevisionRow]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT name, revision, content_hash, pinned_spec_hash, created_at
            FROM impl_revision WHERE name = %s ORDER BY revision
            """,
            (name,),
        ).fetchall()
    return [
        ImplRevisionRow(
            name=r[0],
            revision=r[1],
            content_hash=r[2],
            pinned_spec_hash=r[3],
            created_at=r[4].isoformat(),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# POST /configs/{impl_name}/revisions
# ---------------------------------------------------------------------------

@app.post("/configs/{impl_name}/revisions", response_model=ConfigRevisionResponse)
def post_config_revision(
    impl_name: str, body: ConfigRevisionRequest
) -> ConfigRevisionResponse:
    """Register a new config snapshot for an impl."""
    content_hash = _content_hash_json(body.config)

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(revision), 0) FROM impl_config WHERE impl_name = %s",
            (impl_name,),
        ).fetchone()
        next_rev = (row[0] if row else 0) + 1

        conn.execute(
            """
            INSERT INTO impl_config (impl_name, revision, config_snapshot, content_hash)
            VALUES (%s, %s, %s, %s)
            """,
            (impl_name, next_rev, json.dumps(body.config), content_hash),
        )

    return ConfigRevisionResponse(revision=next_rev, content_hash=content_hash)


# ---------------------------------------------------------------------------
# PUT /bound_impls/{stage}/{class_name}
# ---------------------------------------------------------------------------

@app.put("/bound_impls/{stage}/{class_name}", response_model=BoundImplResponse)
def put_bound_impl(
    stage: str, class_name: str, body: BoundImplRequest
) -> BoundImplResponse:
    """Bind an impl revision to a (stage, class) pair."""
    now = _now_utc()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bound_impls
                (stage, class_name, impl_name, current_revision, current_config_revision, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (stage, class_name) DO UPDATE SET
                impl_name = EXCLUDED.impl_name,
                current_revision = EXCLUDED.current_revision,
                current_config_revision = EXCLUDED.current_config_revision,
                updated_at = EXCLUDED.updated_at
            """,
            (stage, class_name, body.impl_name, body.revision, body.config_revision, now),
        )
        row = conn.execute(
            """
            SELECT stage, class_name, impl_name, current_revision,
                   current_config_revision, updated_at
            FROM bound_impls WHERE stage = %s AND class_name = %s
            """,
            (stage, class_name),
        ).fetchone()

    return BoundImplResponse(
        stage=row[0],
        class_name=row[1],
        impl_name=row[2],
        current_revision=row[3],
        current_config_revision=row[4],
        updated_at=row[5].isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /bound_impls
# ---------------------------------------------------------------------------

@app.get("/bound_impls", response_model=list[BoundImplListItem])
def list_bound_impls() -> list[BoundImplListItem]:
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT stage, class_name, impl_name, current_revision,
                   current_config_revision, updated_at
            FROM bound_impls ORDER BY stage, class_name
            """
        ).fetchall()
    return [
        BoundImplListItem(
            stage=r[0],
            class_name=r[1],
            impl_name=r[2],
            current_revision=r[3],
            current_config_revision=r[4],
            updated_at=r[5].isoformat(),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# POST /runs
# ---------------------------------------------------------------------------

def _load_bound_impls_from_db(conn: psycopg.Connection) -> list[BoundImpl]:
    """Load all bound_impls rows and reconstruct BoundImpl objects."""
    rows = conn.execute(
        "SELECT stage, class_name, impl_name, current_revision FROM bound_impls"
    ).fetchall()
    result: list[BoundImpl] = []
    for stage, class_name, impl_name, revision in rows:
        # Fetch the source for this impl revision.
        src_row = conn.execute(
            "SELECT source_bytes FROM impl_revision WHERE name = %s AND revision = %s",
            (impl_name, revision),
        ).fetchone()
        if src_row is None:
            continue
        source = src_row[0]
        if isinstance(source, memoryview):
            source = bytes(source).decode()
        elif isinstance(source, bytes):
            source = source.decode()
        try:
            impl_class = _load_impl_class(source, impl_name)
        except Exception:
            continue
        result.append(BoundImpl(impl_class=impl_class, impl_name=impl_name, workflow=class_name))
    return result


def _load_impl_configs_from_db(conn: psycopg.Connection) -> dict[str, dict]:
    """Load current config snapshots for all bound impls."""
    rows = conn.execute(
        """
        SELECT bi.impl_name, ic.config_snapshot, bi.current_revision,
               bi.current_config_revision
        FROM bound_impls bi
        LEFT JOIN impl_config ic
            ON ic.impl_name = bi.impl_name
           AND ic.revision = bi.current_config_revision
        """
    ).fetchall()
    configs: dict[str, dict] = {}
    for impl_name, config_snapshot, impl_rev, config_rev in rows:
        data = config_snapshot if isinstance(config_snapshot, dict) else {}
        data = dict(data)
        data["_impl_revision"] = impl_rev
        data["_revision"] = config_rev
        configs[impl_name] = data
    return configs


@app.post("/runs", response_model=RunResponse)
def post_run(body: RunRequest) -> RunResponse:
    """Compile a workflow and record a pipeline run."""
    with _get_conn() as conn:
        bound_impls = _load_bound_impls_from_db(conn)
        impl_configs = _load_impl_configs_from_db(conn)

        # Source watermarks: read from bound impls' pinned_spec_hash as a proxy.
        # Real watermarks come from the lake layer; empty dict is correct for now.
        source_watermarks: dict[str, str] = {}

        try:
            workflow = knot_compile(
                spec=_SPEC,
                bound_impls=bound_impls,
                impl_configs=impl_configs,
                source_watermarks=source_watermarks,
                scope=body.scope,
                parents_override=body.parents_override,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        wf_hash = compile_hash(workflow)
        wf_canonical = json.loads(canonical_dump(workflow))

        # Upsert compiled_workflows (content-addressed; idempotent).
        conn.execute(
            """
            INSERT INTO compiled_workflows (hash, spec)
            VALUES (%s, %s)
            ON CONFLICT (hash) DO NOTHING
            """,
            (wf_hash, json.dumps(wf_canonical)),
        )

        # Insert pipeline_runs row.
        now = _now_utc()
        row = conn.execute(
            """
            INSERT INTO pipeline_runs
                (compile_hash, scope, pinned_parent_runs, started_at, status,
                 runtime_image_identity)
            VALUES (%s, %s, %s, %s, 'pending', %s)
            RETURNING id
            """,
            (
                wf_hash,
                body.scope,
                json.dumps(body.parents_override or {}),
                now,
                body.runtime_image_identity,
            ),
        ).fetchone()
        run_id = row[0]

    return RunResponse(
        run_id=run_id,
        compile_hash=wf_hash,
        workflow_spec=wf_canonical,
    )


# ---------------------------------------------------------------------------
# GET /runs/{run_id}
# ---------------------------------------------------------------------------

@app.get("/runs/{run_id}", response_model=RunStatusResponse)
def get_run(run_id: int) -> RunStatusResponse:
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT pr.id, pr.compile_hash, pr.scope, pr.status,
                   pr.started_at, pr.completed_at, pr.error, cw.spec
            FROM pipeline_runs pr
            JOIN compiled_workflows cw ON cw.hash = pr.compile_hash
            WHERE pr.id = %s
            """,
            (run_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

    spec_data = row[7] if isinstance(row[7], dict) else json.loads(row[7])

    return RunStatusResponse(
        run_id=row[0],
        compile_hash=row[1],
        scope=row[2],
        status=row[3],
        started_at=row[4].isoformat(),
        completed_at=row[5].isoformat() if row[5] else None,
        error=row[6],
        workflow_spec=spec_data,
    )


# ---------------------------------------------------------------------------
# GET /compiled_workflows
# ---------------------------------------------------------------------------

@app.get("/compiled_workflows", response_model=list[CompiledWorkflowListItem])
def list_compiled_workflows(limit: int = 100) -> list[CompiledWorkflowListItem]:
    """List compiled workflows newest-first.  Per-row run_count + stage_count
    let callers see at-a-glance which compile_hashes have been dispatched."""
    with _get_conn() as conn:
        rows = conn.execute(
            """
            SELECT cw.hash,
                   cw.canonicalizer_version,
                   cw.created_at,
                   COUNT(pr.id)                        AS run_count,
                   COALESCE(jsonb_array_length(cw.spec -> 'stages'), 0) AS stage_count
            FROM compiled_workflows cw
            LEFT JOIN pipeline_runs pr ON pr.compile_hash = cw.hash
            GROUP BY cw.hash, cw.canonicalizer_version, cw.created_at, cw.spec
            ORDER BY cw.created_at DESC
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
    return [
        CompiledWorkflowListItem(
            hash=r[0].strip(),
            canonicalizer_version=r[1],
            created_at=r[2].isoformat(),
            run_count=r[3],
            stage_count=r[4],
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /compiled_workflows/{hash}
# ---------------------------------------------------------------------------

@app.get("/compiled_workflows/{hash}", response_model=CompiledWorkflowResponse)
def get_compiled_workflow(hash: str) -> CompiledWorkflowResponse:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT hash, spec, created_at FROM compiled_workflows WHERE hash = %s",
            (hash,),
        ).fetchone()

    if row is None:
        raise HTTPException(status_code=404, detail=f"Compiled workflow {hash!r} not found")

    spec_data = row[1] if isinstance(row[1], dict) else json.loads(row[1])
    return CompiledWorkflowResponse(
        hash=row[0].strip(),
        spec=spec_data,
        created_at=row[2].isoformat(),
    )


# ---------------------------------------------------------------------------
# GET /spec
# ---------------------------------------------------------------------------

@app.get("/spec")
def get_spec() -> dict:
    """Return the current loaded spec as canonical-dump JSON."""
    return json.loads(canonical_dump(_SPEC))


# ---------------------------------------------------------------------------
# POST /corrections
# ---------------------------------------------------------------------------

@app.post("/corrections", response_model=CorrectionResponse)
def post_correction(body: CorrectionRequest) -> CorrectionResponse:
    """Write a user correction to _user_corrections."""
    with _get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO _user_corrections (class_name, canonical_id, slot_name, value)
            VALUES (%s, %s, %s, %s)
            RETURNING id, class_name, canonical_id, slot_name, submitted_at
            """,
            (body.class_name, body.canonical_id, body.slot, json.dumps(body.value)),
        ).fetchone()

    return CorrectionResponse(
        id=row[0],
        class_name=row[1],
        canonical_id=row[2],
        slot_name=row[3],
        submitted_at=row[4].isoformat(),
    )


# ---------------------------------------------------------------------------
# POST /er_decisions
# ---------------------------------------------------------------------------

@app.post("/er_decisions", response_model=ERDecisionResponse)
def post_er_decision(body: ERDecisionRequest) -> ERDecisionResponse:
    """Write an ER force-merge or force-split decision to _user_er_decisions."""
    if body.decision_type not in ("force_merge", "force_split"):
        raise HTTPException(
            status_code=400,
            detail=f"decision_type must be 'force_merge' or 'force_split', got {body.decision_type!r}",
        )
    with _get_conn() as conn:
        row = conn.execute(
            """
            INSERT INTO _user_er_decisions (class_name, decision_type, canonical_ids, reason)
            VALUES (%s, %s, %s, %s)
            RETURNING id, class_name, decision_type, canonical_ids, submitted_at
            """,
            (body.class_name, body.decision_type, body.canonical_ids, body.reason),
        ).fetchone()

    return ERDecisionResponse(
        id=row[0],
        class_name=row[1],
        decision_type=row[2],
        canonical_ids=list(row[3]),
        submitted_at=row[4].isoformat(),
    )
