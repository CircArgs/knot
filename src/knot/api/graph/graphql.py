"""POST /graph/query — Strawberry GraphQL over the published spec."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from knot import db
from knot.api.graph._common import StrictBase, published_or_409
from knot.security import require_user


router = APIRouter()


class GraphQLBody(StrictBase):
    query: str
    variables: dict[str, Any] | None = None
    operation_name: str | None = None


@router.post("/query", dependencies=[Depends(require_user)], tags=["graph"])
def graphql_query(body: GraphQLBody) -> dict[str, Any]:
    """Execute a GraphQL query against the published graph.

    Schema is derived from the currently-published spec. Each OntologyClass
    is queryable with optional ``where``, ``limit``, ``offset``, and
    ``as_of`` arguments. Returns ``{data: ..., errors: ...}`` in the
    standard GraphQL response envelope.
    """
    from knot.api.graphql_schema import get_or_build_schema
    from knot.db import spec_store

    with db.connect() as conn:
        spec = published_or_409(conn)
        content_hash = spec_store.get_published_content_hash(conn) or ""

    schema = get_or_build_schema(spec, content_hash)
    result = schema.execute_sync(
        body.query,
        variable_values=body.variables,
        operation_name=body.operation_name,
    )
    response: dict[str, Any] = {}
    if result.data is not None:
        response["data"] = result.data
    if result.errors:
        response["errors"] = [
            {"message": str(e), "locations": getattr(e, "locations", None)}
            for e in result.errors
        ]
    return response
