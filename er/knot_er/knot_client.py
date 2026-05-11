"""HTTP client to knot's API. Never imports knot.

Reserved for future spec-introspection (e.g. fetching identifier-slot
defaults or class metadata) when richer strategies need it.
"""

import httpx

from knot_er.config import get_settings


class KnotClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or get_settings().knot_url).rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=10.0)

    async def published_classes(self) -> list[dict]:
        r = await self._client.get("/spec/published/classes")
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._client.aclose()
