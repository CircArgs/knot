# knot-ai

AI sibling service for knot. Sketch only — real LLM-backed features
(NL→GraphQL, query explanation, suggestion ranking) land in later
commits.

## Posture

- Runs as its own process at `:8001`.
- Talks to knot **only** over HTTP. Never imports the `knot` Python
  package. The seam is the knot REST/GraphQL API.
- Owned by the same single team that owns knot; same trust posture, no
  sandboxing.

## Run

```bash
cd ai
pip install -e .
uvicorn knot_ai.main:app --port 8001
```

## Smoke check

```bash
curl http://localhost:8001/healthz
curl -X POST http://localhost:8001/suggest \
  -H 'content-type: application/json' \
  -d '{"prompt": "show me all movies"}'
```

## Tests

```bash
cd ai
pip install -e '.[dev]'
python -m pytest tests/ -q
```

## Env vars (all prefixed `KNOT_AI_`)

| Var                    | Default                       | Meaning                              |
| ---------------------- | ----------------------------- | ------------------------------------ |
| `KNOT_AI_KNOT_URL`     | `http://localhost:8000`       | Where the knot core API lives.       |
| `KNOT_AI_LLM_PROVIDER` | `stub`                        | `stub` \| `anthropic` \| `openai`.   |
| `KNOT_AI_LLM_API_KEY`  | _(none)_                      | Provider API key.                    |
| `KNOT_AI_LLM_MODEL`    | `claude-opus-4-20250514`      | Model id passed to provider.         |

## Module map

- `knot_ai/main.py` — FastAPI app factory + `/healthz`.
- `knot_ai/routes.py` — `POST /suggest` (stub).
- `knot_ai/prompts.py` — prompt-construction stub.
- `knot_ai/llm.py` — provider-agnostic `LLMClient` stub.
- `knot_ai/knot_client.py` — async HTTP client to knot core.
- `knot_ai/config.py` — pydantic-settings.
