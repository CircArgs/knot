"""Temporal activities — every knot SQL emitter is invoked from here.

Workflow code (in ingest.py / embed.py / er.py) is deterministic and
never touches the DB or the disk; activities are where I/O lives.

Each activity:
 - takes JSON-serializable inputs + outputs
 - loads the Spec via build_spec() (cheap; pure construction)
 - resolves the binding by name lookup
 - calls the corresponding ``binding.*_sql()`` template
 - executes via psycopg with the right `%(named)s` param dict
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from temporalio import activity

from hitch import config
from hitch import embeddings as emb
from hitch.db import connect, jsonb_param
from hitch.spec import build_spec

log = logging.getLogger("hitch.activities")

# Seed JSON lives next to the package; in the docker image this is
# /app/hitch/seeds because the package is copied there.
_SEEDS_DIR = Path(__file__).parent.parent / "seeds"


def _binding(source_name: str, class_name: str, *, schema: str, embedding_dim: int):
    spec = build_spec(schema=schema, embedding_dim=embedding_dim)
    return spec, spec.classes[class_name].binding_for(spec.sources[source_name])


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@activity.defn
def fetch_seed_batch(source_name: str, class_name: str) -> list[dict[str, Any]]:
    path = _SEEDS_DIR / f"{source_name}.json"
    if not path.exists():
        log.warning("no seed file at %s", path)
        return []
    data = json.loads(path.read_text())
    return data.get(class_name, [])


@activity.defn
def validate_and_bucket(
    source_name: str, class_name: str, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Run validate_rows_sql; split into clean + violations."""
    cfg = config.load()
    _, binding = _binding(
        source_name, class_name, schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim
    )
    sql = binding.validate_rows_sql()
    with connect(cfg.pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(sql, {"rows": jsonb_param(rows)})
        bad_records = list(cur.fetchall())
    bad_indexes = {int(r["row_index"]) for r in bad_records}
    clean = [r for i, r in enumerate(rows) if i not in bad_indexes]
    return {"clean": clean, "violations": bad_records}


@activity.defn
def upsert_rows(source_name: str, class_name: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    cfg = config.load()
    _, binding = _binding(
        source_name, class_name, schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim
    )
    sql = binding.write_sql()
    with connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, {"rows": jsonb_param(rows)})
    return len(rows)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


@activity.defn
def fetch_titles_to_embed(
    source_name: str, class_name: str, slot_name: str, limit: int
) -> list[dict[str, Any]]:
    """Per-source query: which binding rows still have a NULL embedding?"""
    cfg = config.load()
    spec, _ = _binding(
        source_name, class_name, schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim
    )
    cls = spec.classes[class_name]
    src = spec.sources[source_name]
    q = (
        cls.from_source(src)
        .where(cls.col[slot_name].is_null())
        .select(cls.bindings_col.source_identifier, cls.col.title)
        .limit(limit)
    )
    with connect(cfg.pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(q.sql())
        return list(cur.fetchall())


@activity.defn
def compute_embeddings(texts: list[str]) -> list[list[float]]:
    cfg = config.load()
    return emb.embed(texts, model_name=cfg.embedding_model)


@activity.defn
def write_embeddings(
    source_name: str,
    class_name: str,
    slot_name: str,
    rows: list[dict[str, Any]],
) -> int:
    """`rows` is [{source_identifier, <slot_name>: [...]}, ...]."""
    if not rows:
        return 0
    cfg = config.load()
    _, binding = _binding(
        source_name, class_name, schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim
    )
    sql = binding.update_slot_sql(slot_name)
    with connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, {"rows": jsonb_param(rows)})
    return len(rows)


# ---------------------------------------------------------------------------
# ER
# ---------------------------------------------------------------------------


@activity.defn
def fetch_unresolved(source_name: str, class_name: str) -> list[dict[str, Any]]:
    """All bindings rows from a source where canonical_id IS NULL,
    plus the identity field we ER-match on."""
    cfg = config.load()
    spec, _ = _binding(
        source_name, class_name, schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim
    )
    cls = spec.classes[class_name]
    src = spec.sources[source_name]
    id_col = {
        "Person": "name",
        "Studio": "name",
        "Movie": "title",
        "Credit": "role",
    }[class_name]
    refs = [cls.bindings_col.source_identifier, cls.col[id_col]]
    if class_name == "Movie":
        refs.append(cls.col.year)
    if class_name == "Studio":
        refs.append(cls.col.country)
    if class_name == "Credit":
        refs += [cls.col.movie, cls.col.person]
    q = cls.from_source(src).where(cls.col.canonical_id.is_null()).select(*refs)
    with connect(cfg.pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(q.sql())
        return list(cur.fetchall())


def _mint(*parts: str) -> str:
    """Deterministic canonical_id from ER identity parts. Sha1 keeps
    workflow re-runs convergent (per the docs/temporal-adapter.md
    replay-safety contract)."""
    raw = "::".join(p.strip().lower() for p in parts)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def _identity(r: dict[str, Any], *fields: str) -> list[str] | None:
    """Pull identity fields from a row. Returns None if any required
    field is missing, None, or empty/whitespace — those rows cannot
    be deterministically minted (every blank-name person would
    collide on the empty-string sha1). Caller skips them.

    Empty/whitespace optional fields (e.g. Studio.country) coerce to
    ``""`` for the mint — distinct from missing-required."""
    parts: list[str] = []
    for f in fields:
        v = r.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        parts.append(str(v))
    return parts


@activity.defn
def decide_canonicals(
    source_name: str, class_name: str, rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Return assignments shaped for assign_canonicals_sql:
    [{canonical_id, source_identifier, er_metadata}, ...].

    Rows whose identity fields are missing / null / blank are SKIPPED
    with a warning — silently minting them would collide every such
    row into one canonical_id (sha1("") = same hash for everyone)."""
    out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in rows:
        sid = r.get("source_identifier")
        if not sid:
            skipped.append({"reason": "no_source_identifier", "row": r})
            continue
        if class_name == "Person":
            ident = _identity(r, "name")
            if ident is None:
                skipped.append({"reason": "blank_name", "source_identifier": sid})
                continue
            cid = "p_" + _mint(*ident)
        elif class_name == "Studio":
            # Mint on (name, country) — same name in different countries
            # should NOT collapse (e.g. "Pathé" UK vs France). Country
            # falls back to "" if missing — only `name` is required.
            ident = _identity(r, "name")
            if ident is None:
                skipped.append({"reason": "blank_name", "source_identifier": sid})
                continue
            cid = "s_" + _mint(ident[0], r.get("country") or "")
        elif class_name == "Movie":
            ident = _identity(r, "title")
            if ident is None:
                skipped.append({"reason": "blank_title", "source_identifier": sid})
                continue
            cid = "m_" + _mint(ident[0], str(r.get("year") or ""))
        elif class_name == "Credit":
            ident = _identity(r, "movie", "person", "role")
            if ident is None:
                skipped.append(
                    {"reason": "missing_credit_fk_or_role", "source_identifier": sid}
                )
                continue
            cid = "c_" + _mint(*ident)
        else:
            cid = _mint(sid)
        out.append(
            {
                "canonical_id": cid,
                "source_identifier": sid,
                "er_metadata": {
                    "policy": "deterministic_sha1",
                    "source": source_name,
                },
            }
        )
    if skipped:
        log.warning(
            "decide_canonicals(%s/%s): skipped %d row(s): %s",
            source_name,
            class_name,
            len(skipped),
            skipped[:5],
        )
    return out


@activity.defn
def assign_canonicals(
    source_name: str, class_name: str, assignments: list[dict[str, Any]]
) -> int:
    if not assignments:
        return 0
    cfg = config.load()
    _, binding = _binding(
        source_name, class_name, schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim
    )
    sql = binding.assign_canonicals_sql()
    with connect(cfg.pg_dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, {"assignments": jsonb_param(assignments)})
    return len(assignments)


# ---------------------------------------------------------------------------
# Validation sweep
# ---------------------------------------------------------------------------


@activity.defn
def run_validation_sweep() -> dict[str, Any]:
    """Run every emit_validation SELECT. Returns rule → violations."""
    cfg = config.load()
    spec = build_spec(schema=cfg.pg_schema, embedding_dim=cfg.embedding_dim)
    report: dict[str, Any] = {}
    # spec.emit_validation returns (constraint_name, sql) tuples.
    # Look up severity from spec.constraints when the rule isn't a
    # built-in (built-ins aren't in spec.constraints; their name encodes
    # severity convention — _builtin_fk_orphan_* is ERROR,
    # _builtin_required_null_* is WARNING).
    by_name = {c.name: c.severity.value for c in spec.constraints}
    with connect(cfg.pg_dsn, autocommit=True) as conn, conn.cursor() as cur:
        for rule_name, sql in spec.emit_validation():
            cur.execute(sql)
            violations = list(cur.fetchall())
            if rule_name in by_name:
                severity = by_name[rule_name]
            elif rule_name.startswith("_builtin_fk_orphan_"):
                severity = "error"
            elif rule_name.startswith("_builtin_required_null_"):
                severity = "warning"
            else:
                severity = "unknown"
            report[rule_name] = {
                "severity": severity,
                "count": len(violations),
                "sample": violations[:3],
            }
    return report


# ---------------------------------------------------------------------------
# All activities — registered with the worker
# ---------------------------------------------------------------------------

ALL = [
    fetch_seed_batch,
    validate_and_bucket,
    upsert_rows,
    fetch_titles_to_embed,
    compute_embeddings,
    write_embeddings,
    fetch_unresolved,
    decide_canonicals,
    assign_canonicals,
    run_validation_sweep,
]
