# knot-er

Entity-resolution sibling service for knot. Sketch only — real
strategies (fuzzy, ML, embedding similarity) land in later commits.

## Posture

- Runs as its own process at `:8002`.
- Talks to knot **only** over HTTP. Never imports the `knot` Python
  package. The seam is the knot REST API.
- knot core's `knot/extensions/er.py` POSTs `RowsIngesting` rows to
  `POST /resolve` when `KNOT_ER_URL` is set; otherwise knot falls back
  to identifier-slot passthrough internally.

## Run

```bash
cd er
pip install -e .
uvicorn knot_er.main:app --port 8002
```

## Smoke check

```bash
curl http://localhost:8002/healthz
curl -X POST http://localhost:8002/resolve \
  -H 'content-type: application/json' \
  -d '{
        "source": "imdb",
        "class_name": "Movie",
        "identifier_slot": "imdb_id",
        "rows": [{"imdb_id": "tt001"}, {"imdb_id": "tt002"}]
      }'
```

## Tests

```bash
cd er
pip install -e '.[dev]'
python -m pytest tests/ -q
```

## Adding a new strategy

1. Add a function to `knot_er/strategies.py`. Signature:
   `fn(rows: list[dict], **kwargs) -> list[str]`.
2. Wire selection in `knot_er/routes.py` (probably config-driven on
   `source` or `class_name`).
3. Add a smoke test in `tests/`.

## Env vars (all prefixed `KNOT_ER_`)

| Var                       | Default                  | Meaning                          |
| ------------------------- | ------------------------ | -------------------------------- |
| `KNOT_ER_KNOT_URL`        | `http://localhost:8000`  | Where knot core lives (for intro). |
| `KNOT_ER_DEFAULT_STRATEGY`| `identifier_passthrough` | Strategy when source-specific config absent. |
