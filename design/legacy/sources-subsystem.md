---
title: 'Sources registration: source kind in ontology + sources_state runtime + register/list
  endpoints'
status: applied
created_at: '2026-04-28T12:31:54.432281+00:00'
epic: knot-v1
---

> ⚠️ **LEGACY / UNVERIFIED.** This document was authored during the prototype/exploration phase and has not been reconciled against the current codebase. Treat as historical context, not specification. Cross-check anything you intend to act on. See [`../README.md`](../README.md) for the current entry point.

## Plan

Adds the **sources** subsystem per `knot-architecture-v1.md` § "Other
control-plane tables" and § "Lifecycle invariants". Sources are
ontology nodes with `kind='source'` (LinkML class describing
connection + ontology-class-mapping + initial-trust). Runtime
mutable state (current trust score, last-refreshed) lives in a
separate `sources_state` table joined 1:1 by node id.

Reference: `lifecycle-source.md` for the full lifecycle.

### Migration `006_sources_state.sql`
```sql
CREATE TABLE sources_state (
  source_node_id UUID PRIMARY KEY REFERENCES ontology_nodes(id),
  trust_score    NUMERIC NOT NULL DEFAULT 0.5,
  last_refreshed_at TIMESTAMPTZ,
  ingested_row_count BIGINT NOT NULL DEFAULT 0,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION sources_state_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER sources_state_updated_at BEFORE UPDATE ON sources_state
  FOR EACH ROW EXECUTE FUNCTION sources_state_touch_updated_at();
```

### LinkML schema for source-kind nodes (a meta-ontology)

Document this convention; sources should be defined as LinkML docs of
this shape. Save canonical example to `tests/fixtures/source_v1.yaml`:

```yaml
id: https://knot.test/imdb_source
name: imdb_source
classes:
  ImdbSource:
    description: IMDB title metadata feed
    attributes:
      ontology_class: { range: string, required: true }   # e.g. "Movie"
      key_field: { range: string, required: true }        # e.g. "tt_id"
      property_map: { range: string, multivalued: true }  # source field → ontology slot
      initial_trust: { range: float }                     # default 0.5
```

(In v1 we don't statically enforce this shape — the LinkML payload is
just JSON. Future: a validator-side check.)

### Repository — `server/repositories/sources.py`

```python
@dataclass
class SourceState:
    source_node_id: UUID
    trust_score: Decimal
    last_refreshed_at: datetime | None
    ingested_row_count: int
    notes: str | None
    created_at: datetime
    updated_at: datetime

async def get_source_state(pool, source_node_id) -> SourceState | None: ...
async def upsert_source_state(pool, source_node_id, *, trust_score=None, last_refreshed_at=None, notes=None) -> SourceState: ...
async def increment_ingested_rows(pool, source_node_id, delta) -> None: ...
async def list_sources(pool, *, only_published=True) -> list[tuple[NodeRow, SourceState | None]]: ...
```

### Endpoints — `server/api/sources.py`

```python
router = APIRouter(prefix="/sources", tags=["sources"])

@router.get("", response_model=list[SourceListItem])
async def list_sources_endpoint(only_published: bool = True, pool=Depends(get_pool)): ...

@router.get("/{name}", response_model=SourceDetail)
async def get_source(name: str, pool=Depends(get_pool)): ...

@router.post("/{name}/state", response_model=SourceDetail)
async def update_source_state(name: str, body: UpdateSourceStateRequest, pool=Depends(get_pool)):
    """Operational endpoint to set trust_score, notes."""
```

`SourceListItem` joins `ontology_nodes` (kind='source') ⨝ `sources_state`.
On first publish of a `kind='source'` node, a `sources_state` row is
upserted with defaults — handle in publish endpoint or via INSERT
trigger that watches for new source-kind nodes (prefer endpoint logic,
keep DB triggers minimal).

### Hook into publish endpoint
When `node-publish` lands a source-kind node's first publication,
upsert `sources_state` with `trust_score = payload.initial_trust ?? 0.5`.

### Tests
- migration 006 applies
- POST /nodes (kind=source), publish → sources_state row exists with defaults
- GET /sources lists it
- POST /sources/{name}/state updates trust_score
- GET /sources/{name} returns combined view (ontology metadata + state)
- only-published filter works (drafts not in list)

## Acceptance
- [ ] migration 006 lands
- [ ] First publish of source-kind node creates sources_state row
- [ ] /sources endpoints work end-to-end with 5+ tests passing

## Journal
