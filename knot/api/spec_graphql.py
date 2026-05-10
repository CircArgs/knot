"""GET/POST /spec/graphql — Strawberry GraphQL over the published spec.

The spec metaschema is **static** (commitment 2: typed Pydantic entities, no
generated classes), so the GraphQL schema is also static — defined once,
mirroring the shape of ``knot.spec.metaschema``.  Compare with the data-plane
``/graph/query`` endpoint, where the schema is compiled per-published-spec
because the queryable shape (which classes / which slots) varies with the
spec graph.

GET serves Strawberry's bundled GraphiQL IDE so operators can explore the
schema in a browser.  POST executes a query against the currently-published
spec via ``knot.graph.spec.get_published`` (the orchestration tier — the
GraphQL layer never reaches into ``knot.db.spec_store`` directly).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import strawberry
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import ConfigDict, Field

from knot import db
from knot.api.graph._common import StrictBase
from knot.db import spec_store
from knot.graph import spec as graph_spec
from knot.spec import OntologyClass, Slot, Source, Spec, TypeDefinition
from knot.spec.metaschema import Constraint

router = APIRouter()


# ---------------------------------------------------------------------------
# GraphiQL HTML — lazy-loaded from Strawberry's bundle
# ---------------------------------------------------------------------------

_GRAPHIQL_HTML: str | None = None


def _graphiql_html() -> str:
    """Lazy-load Strawberry's bundled GraphiQL HTML."""
    global _GRAPHIQL_HTML
    if _GRAPHIQL_HTML is None:
        path = Path(strawberry.__file__).parent / "static" / "graphiql.html"
        _GRAPHIQL_HTML = path.read_text(encoding="utf-8")
    return _GRAPHIQL_HTML


# ---------------------------------------------------------------------------
# Strawberry types — mirror the static spec metaschema
#
# ``Slot.range`` is flattened to (range_kind, range_name) — same shape as the
# REST ``/spec/published/slots`` endpoint, so consumers don't need to learn a
# second discriminator.  This keeps queries simple and reusable.
# ---------------------------------------------------------------------------


@strawberry.type
class TypeDefinitionGQL:
    name: str
    base: str | None
    pattern: str | None
    description: str | None


@strawberry.type
class SlotGQL:
    name: str
    identifier: bool
    required: bool
    multivalued: bool
    description: str | None
    pattern: str | None
    minimum_value: float | None
    maximum_value: float | None
    permissible_values: list[str]
    resolution_policy: str
    range_kind: str | None  # "type" | "class" | None
    range_name: str | None


@strawberry.type
class OntologyClassGQL:
    name: str
    abstract: bool
    description: str | None
    is_a_name: str | None
    mixin_names: list[str]
    slot_names: list[str]


@strawberry.type
class SourceGQL:
    name: str
    entity_class_name: str
    identifier_slot_name: str
    description: str | None


@strawberry.type
class ConstraintGQL:
    name: str
    primary_class_name: str
    severity: str
    message: str | None


@strawberry.type
class PublishedSpec:
    id: str
    version: str
    revision: int
    content_hash: str
    types: list[TypeDefinitionGQL]
    slots: list[SlotGQL]
    classes: list[OntologyClassGQL]
    sources: list[SourceGQL]
    constraints: list[ConstraintGQL]


# ---------------------------------------------------------------------------
# Pydantic → Strawberry adapters
# ---------------------------------------------------------------------------


def _to_type(t: TypeDefinition) -> TypeDefinitionGQL:
    return TypeDefinitionGQL(
        name=t.name,
        base=t.base,
        pattern=t.pattern,
        description=t.description,
    )


def _to_slot(s: Slot) -> SlotGQL:
    range_kind: str | None = None
    range_name: str | None = None
    if isinstance(s.range, OntologyClass):
        range_kind, range_name = "class", s.range.name
    elif isinstance(s.range, TypeDefinition):
        range_kind, range_name = "type", s.range.name
    rp = s.resolution_policy
    return SlotGQL(
        name=s.name,
        identifier=s.identifier,
        required=s.required,
        multivalued=s.multivalued,
        description=s.description,
        pattern=s.pattern,
        minimum_value=s.minimum_value,
        maximum_value=s.maximum_value,
        permissible_values=[pv.text for pv in (s.permissible_values or [])],
        resolution_policy=rp.value if hasattr(rp, "value") else str(rp),
        range_kind=range_kind,
        range_name=range_name,
    )


def _to_class(c: OntologyClass) -> OntologyClassGQL:
    return OntologyClassGQL(
        name=c.name,
        abstract=c.abstract,
        description=c.description,
        is_a_name=c.is_a.name if c.is_a is not None else None,
        mixin_names=[m.name for m in c.mixins],
        slot_names=[s.name for s in c.slots],
    )


def _to_source(s: Source) -> SourceGQL:
    return SourceGQL(
        name=s.name,
        entity_class_name=s.entity_class.name,
        identifier_slot_name=s.identifier_slot.name,
        description=s.description,
    )


def _to_constraint(c: Constraint) -> ConstraintGQL:
    severity = c.severity.value if hasattr(c.severity, "value") else str(c.severity)
    return ConstraintGQL(
        name=c.name,
        primary_class_name=c.primary.name,
        severity=severity,
        message=c.message,
    )


def _to_published_spec(spec: Spec, *, revision: int, content_hash: str) -> PublishedSpec:
    return PublishedSpec(
        id=spec.id,
        version=spec.version,
        revision=revision,
        content_hash=content_hash,
        types=[_to_type(t) for t in spec.types],
        slots=[_to_slot(s) for s in spec.slots],
        classes=[_to_class(c) for c in spec.classes],
        sources=[_to_source(s) for s in spec.sources],
        constraints=[_to_constraint(c) for c in spec.constraints],
    )


# ---------------------------------------------------------------------------
# Root query
# ---------------------------------------------------------------------------


@strawberry.type
class Query:
    @strawberry.field
    async def published_spec(self) -> PublishedSpec | None:
        """The currently-published spec, or null if nothing is published.

        Reads land at the orchestration tier (``knot.graph.spec``), not at
        the storage tier (``knot.db.spec_store``).
        """
        async with db.connect() as conn:
            spec = await graph_spec.get_published(conn)
            if spec is None:
                return None
            revision = await spec_store.get_published_revision(conn)
            content_hash = await spec_store.get_published_content_hash(conn)
        return _to_published_spec(
            spec,
            revision=revision or 0,
            content_hash=content_hash or "",
        )


schema = strawberry.Schema(query=Query)


# ---------------------------------------------------------------------------
# HTTP layer
# ---------------------------------------------------------------------------


class GraphQLBody(StrictBase):
    # Accept both spec-form ``operationName`` and snake_case alias.
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    query: str
    variables: dict[str, Any] | None = None
    operation_name: str | None = Field(default=None, alias="operationName")


@router.get("/graphql", include_in_schema=False)
def graphiql_ui() -> HTMLResponse:
    """Serve GraphiQL IDE — POSTs back to this same URL via JS."""
    return HTMLResponse(_graphiql_html())


@router.post("/graphql", tags=["spec"])
async def graphql_query(body: GraphQLBody) -> dict[str, Any]:
    """Execute a GraphQL query against the published spec.

    Returns ``{data: ..., errors: ...}`` per the standard GraphQL response
    envelope.  No auth dep — published spec reads are public, mirroring
    the existing ``/spec/published/*`` REST endpoints.
    """
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
