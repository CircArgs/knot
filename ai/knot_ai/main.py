"""AI sibling service for knot.

Stub. Real impl (NL->GraphQL, query explanation, etc.) lands later.
Talks to knot via HTTP only - no Python import from `knot/`.
"""

from fastapi import FastAPI

from knot_ai.routes import router

app = FastAPI(title="knot-ai", version="0.0.0")
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "knot-ai", "version": "0.0.0"}
