---
title: 'Judgments: evaluative feedback on ER/DQ/materialization outcomes for retraining'
status: backlog
created_at: '2026-04-28T20:22:29.935345+00:00'
epic: knot-v1
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.

## Context

Knot has `corrections` (users fix specific facts) but no first-class
**judgment** signal — "this ER cluster was a bad merge", "this DQ
check is too strict", "this materialization run produced wrong
output." That's evaluative-on-outcomes, distinct from
corrections-of-facts.

Captured in `knot-architecture-v1` § "Judgments — evaluative feedback
on outcomes" (locked schema). This task implements it end-to-end:
table, repository, API, UI surfaces, and the read paths that feed
ER/DQ retraining.

## Vault references

- `knot-architecture-v1` § "Judgments — evaluative feedback on
  outcomes" — locked data model, API surface, UI surfaces
- `knot-architecture-v1` § "Lifecycle invariants" — append-only
  pattern (judgments share with `history`)
- `node-publish-with-strict-validation` — established pattern for
  forensic columns (user_id, request_id, source_ip, trace_id) +
  append-only via trigger

## Plan

### Step 1 — Migration `006_judgments.sql`

```sql
CREATE TABLE judgments (
  id            UUID PRIMARY KEY,
  target_kind   TEXT NOT NULL,
  target_id     TEXT NOT NULL,
  verdict       TEXT NOT NULL CHECK (verdict IN ('good', 'bad', 'ambiguous')),
  reason        TEXT,
  user_id       TEXT NOT NULL,
  request_id    TEXT,
  source_ip     INET,
  trace_id      TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX judgments_target_idx ON judgments(target_kind, target_id);
CREATE INDEX judgments_user_idx ON judgments(user_id, created_at DESC);

-- Append-only via trigger (same pattern as history).
CREATE TRIGGER judgments_no_update BEFORE UPDATE ON judgments
  FOR EACH ROW EXECUTE FUNCTION history_append_only();
CREATE TRIGGER judgments_no_delete BEFORE DELETE ON judgments
  FOR EACH ROW EXECUTE FUNCTION history_append_only();
```

**Acceptance:**
- [ ] Migration applies cleanly after 005
- [ ] Verdict CHECK constraint enforced
- [ ] UPDATE/DELETE rejected by trigger

### Step 2 — Repository — `server/repositories/judgments.py`

```python
@dataclass
class JudgmentRow:
    id: UUID
    target_kind: str
    target_id: str
    verdict: str
    reason: str | None
    user_id: str
    request_id: str | None
    source_ip: str | None
    trace_id: str | None
    created_at: datetime

async def insert_judgment(conn, *, target_kind, target_id, verdict, reason,
                          user_id, request_id, source_ip, trace_id) -> JudgmentRow: ...
async def list_judgments_for_target(pool, target_kind, target_id) -> list[JudgmentRow]: ...
async def list_judgments_by_user(pool, user_id, limit=100) -> list[JudgmentRow]: ...
async def aggregate_verdicts_for_target(pool, target_kind, target_id) -> dict[str, int]:
    """Returns {good: N, bad: M, ambiguous: K}."""
```

**Acceptance:**
- [ ] All 4 functions covered by unit tests
- [ ] insert_judgment writes all forensic fields; source_ip cast to INET
- [ ] aggregate_verdicts returns counts grouped by verdict

### Step 3 — API endpoints — `server/api/judgments.py`

```python
class CreateJudgmentRequest(BaseModel):
    target_kind: Literal["er_cluster", "dq_check_run",
                          "materialization_run", "er_strategy_revision"]
    target_id: str
    verdict: Literal["good", "bad", "ambiguous"]
    reason: str | None = None

class JudgmentResponse(BaseModel):
    id: UUID
    target_kind: str
    target_id: str
    verdict: str
    reason: str | None
    user_id: str
    created_at: datetime

@router.post("/judgments", response_model=JudgmentResponse, status_code=201)
async def submit_judgment(...): ...

@router.get("/judgments", response_model=list[JudgmentResponse])
async def list_judgments(target_kind: str, target_id: str, ...): ...

@router.get("/judgments/by-user/{user_id}", response_model=list[JudgmentResponse])
async def list_by_user(user_id: str, limit: int = 100, ...): ...

@router.get("/judgments/aggregate", response_model=dict[str, int])
async def aggregate(target_kind: str, target_id: str, ...): ...
```

Wire `judgments_router` into `server/main.py`.

**Acceptance:**
- [ ] POST /judgments returns 201 + created row
- [ ] Authz check: `submit:judgment` allowed for editor / curator / admin; viewer denied
- [ ] GET /judgments?target_kind=…&target_id=… returns full list
- [ ] Aggregate endpoint returns `{good, bad, ambiguous}` counts

### Step 4 — UI: judgment widget + Entities page integration

`ui/src/components/features/JudgmentControl.tsx`:
- Two-button row: 👍 good / 👎 bad
- Optional "ambiguous / needs review" tertiary
- Optional reason textarea revealed on click
- Aggregate display: "12 good, 3 bad" pill via `/judgments/aggregate`
- Optimistic update on submit

Wire into `EntitiesPage`:
- Per-row JudgmentControl with `target_kind="er_cluster"`, `target_id=entity.uuid`

Wire into pipeline run detail (Pipelines page):
- Footer JudgmentControl with `target_kind="materialization_run"`, `target_id=run_id`

**Acceptance:**
- [ ] Submitting a judgment writes a row and updates the aggregate badge
- [ ] Viewer role: control disabled with "no permission" tooltip
- [ ] Re-submitting a different verdict creates a NEW row (history of judgments preserved)

### Step 5 — Read into ER strategy + DQ check

Wire `aggregate_verdicts_for_target()` into the default
`ERStrategy` and `DataQualityCheck` implementations:
- ER: cluster-shape signature with N "bad" judgments in last 30 days
  → cost penalty during merge.
- DQ: check ID with N "bad" (too-strict) judgments → log warning
  + suggest threshold relaxation in run summary.

These are advisory (not enforcement) in v1 — they appear in run
metadata so operators see the signal. Auto-tuning is v2.

**Acceptance:**
- [ ] ER strategy run produces a `judgment_signal` field in its
  per-cluster output describing the read aggregate
- [ ] DQ run summary includes a `judgment_signal` line when
  judgments exist for the check

### Step 6 — Tests

- `tests/test_judgments.py` — unit tests for repo + API
- `tests/test_judgments_e2e.py` — submit a judgment, read aggregate,
  verify ER strategy reads signal in next run

## Acceptance criteria

- [ ] `/judgments` POST/GET endpoints work end-to-end
- [ ] Append-only enforced via trigger
- [ ] UI: judge an entity from the Entities page
- [ ] UI: judge a pipeline run from the Pipelines page
- [ ] ER and DQ read judgment signals as advisory input
- [ ] All tests green

## Journal
