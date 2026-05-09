"""GET/POST /graph/query — Strawberry GraphQL over the published spec.

GET serves the GraphiQL IDE HTML (Strawberry's bundled static file) so
operators can poke at the schema in a browser. The page POSTs to its
own URL, so the same path serves both the IDE and the executor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import ConfigDict, Field

from knot import db
from knot.api.auth.security import require_user
from knot.api.graph._common import StrictBase, published_or_409

router = APIRouter()


_GRAPHIQL_HTML: str | None = None


def _graphiql_html() -> str:
    """Lazy-load Strawberry's bundled GraphiQL HTML."""
    global _GRAPHIQL_HTML
    if _GRAPHIQL_HTML is None:
        import strawberry

        path = Path(strawberry.__file__).parent / "static" / "graphiql.html"
        _GRAPHIQL_HTML = path.read_text(encoding="utf-8")
    return _GRAPHIQL_HTML


@router.get("/query", include_in_schema=False)
def graphiql_ui() -> HTMLResponse:
    """Serve GraphiQL IDE — POSTs back to this same URL via JS."""
    return HTMLResponse(_graphiql_html())


class GraphQLBody(StrictBase):
    # GraphiQL and most clients send `operationName` per the GraphQL spec —
    # accept both that and the snake_case alias so notebooks calling with
    # ``operation_name=`` keep working.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    query: str
    variables: dict[str, Any] | None = None
    operation_name: str | None = Field(default=None, alias="operationName")


@router.post("/query", dependencies=[Depends(require_user)], tags=["graph"])
async def graphql_query(body: GraphQLBody) -> dict[str, Any]:
    """Execute a GraphQL query against the published graph.

    Schema is derived from the currently-published spec. Each OntologyClass
    is queryable with optional ``where``, ``limit``, ``offset``, and
    ``as_of`` arguments. Returns ``{data: ..., errors: ...}`` in the
    standard GraphQL response envelope.
    """
    from knot.db import spec_store
    from knot.spec.compile.graphql import get_or_build_schema

    async with db.connect() as conn:
        spec = await published_or_409(conn)
        content_hash = await spec_store.get_published_content_hash(conn) or ""

    schema = get_or_build_schema(spec, content_hash)
    result = await schema.execute(
        body.query,
        variable_values=body.variables,
        operation_name=body.operation_name,
    )
    response: dict[str, Any] = {}
    if result.data is not None:
        response["data"] = result.data
    if result.errors:
        response["errors"] = [
            {"message": str(e), "locations": getattr(e, "locations", None)} for e in result.errors
        ]
    return response
