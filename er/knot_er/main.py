"""Entity-resolution sibling service for knot.

Stub. Real strategies (fuzzy, ML matchers, embedding similarity) land
later. Talks to knot via HTTP only - no Python import from `knot/`.
"""

from fastapi import FastAPI

from knot_er.routes import router

app = FastAPI(title="knot-er", version="0.0.0")
app.include_router(router)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "knot-er", "version": "0.0.0"}
