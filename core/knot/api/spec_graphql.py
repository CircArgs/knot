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
from knot.spec import Array, ClassRef, OntologyClass, Primitive, Slot, Source, SourceBinding, Spec
from knot.spec.effective_slots import effective_slots as _effective_slots
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
# ``Slot.type`` is encoded as (type_kind, type_name) — same shape as the
# REST ``/spec/published/slots`` endpoint, so consumers don't need a
# second discriminator.  type_kind is one of:
#   "primitive" | "class" | "array_of_primitive" | "array_of_class"
# ---------------------------------------------------------------------------


@strawberry.type
class SlotGQL:
    name: str
    identifier: bool
    required: bool
    description: str | None
    pattern: str | None
    minimum_value: float | None
    maximum_value: float | None
    permissible_values: list[str]
    resolution_policy: str
    type_kind: str | None
    type_name: str | None


@strawberry.type
class OntologyClassGQL:
    name: str
    abstract: bool
    description: str | None
    is_a_name: str | None
    mixin_names: list[str]
    slots: list[SlotGQL]
    effective_slots: list[SlotGQL]
    definition: str | None  # SQL predicate body when this is a defined class (VIEW)


@strawberry.type
class SourceGQL:
    name: str
    description: str | None


@strawberry.type
class SlotMappingGQL:
    slot_name: str
    source_field: str
    null_semantics: str
    has_prior: bool


@strawberry.type
class SourceBindingGQL:
    binding_id: str
    source_name: str
    class_name: str
    identifier_slot_name: str
    trust_prior: list[float]
    required_slot_names: list[str]
    mappings: list[SlotMappingGQL]
    description: str | None


@strawberry.type
class ConstraintGQL:
    name: str
    primary_class_name: str
    body: str
    severity: str
    message: str | None


@strawberry.type
class PublishedSpec:
    id: str
    version: str
    revision: int
    content_hash: str
    classes: list[OntologyClassGQL]
    sources: list[SourceGQL]
    source_bindings: list[SourceBindingGQL]
    constraints: list[ConstraintGQL]


# ---------------------------------------------------------------------------
# Pydantic → Strawberry adapters
# ---------------------------------------------------------------------------


def _type_kind_name(s: Slot) -> tuple[str | None, str | None]:
    """Return (type_kind, type_name) for a slot's TypeExpression."""
    t = s.type
    if t is None:
        return None, None
    if isinstance(t, Primitive):
        return "primitive", t.name
    if isinstance(t, ClassRef):
        return "class", t.target_class.name
    if isinstance(t, Array):
        inner = t.of
        if isinstance(inner, Primitive):
            return "array_of_primitive", inner.name
        if isinstance(inner, ClassRef):
            return "array_of_class", inner.target_class.name
    return None, None


def _to_slot(s: Slot) -> SlotGQL:
    type_kind, type_name = _type_kind_name(s)
    rp = s.resolution_policy
    c = s.constraints
    return SlotGQL(
        name=s.name,
        identifier=s.identifier,
        required=s.required,
        description=s.description,
        pattern=c.pattern if c else None,
        minimum_value=c.min_value if c else None,
        maximum_value=c.max_value if c else None,
        permissible_values=c.permissible_values if (c and c.permissible_values) else [],
        resolution_policy=rp.value if hasattr(rp, "value") else str(rp),
        type_kind=type_kind,
        type_name=type_name,
    )


def _to_class(c: OntologyClass) -> OntologyClassGQL:
    return OntologyClassGQL(
        name=c.name,
        abstract=c.abstract,
        description=c.description,
        is_a_name=c.is_a.name if c.is_a is not None else None,
        mixin_names=[m.name for m in c.mixins],
        slots=[_to_slot(s) for s in c.slots],
        effective_slots=[_to_slot(s) for s in _effective_slots(c)],
        definition=c.definition,
    )


def _to_source(s: Source) -> SourceGQL:
    return SourceGQL(name=s.name, description=s.description)


def _to_slot_mapping(m: Any) -> SlotMappingGQL:
    ns = m.null_semantics
    return SlotMappingGQL(
        slot_name=m.slot.name,
        source_field=m.source_field,
        null_semantics=ns.value if hasattr(ns, "value") else str(ns),
        has_prior=m.prior is not None,
    )


def _to_source_binding(b: SourceBinding) -> SourceBindingGQL:
    return SourceBindingGQL(
        binding_id=b.binding_id,
        source_name=b.source.name,
        class_name=b.class_.name,
        identifier_slot_name=b.identifier_slot.name,
        trust_prior=list(b.trust_prior),
        required_slot_names=[s.name for s in b.required_slots],
        mappings=[_to_slot_mapping(m) for m in b.mappings],
        description=b.description,
    )


def _to_constraint(c: Constraint) -> ConstraintGQL:
    severity = c.severity.value if hasattr(c.severity, "value") else str(c.severity)
    return ConstraintGQL(
        name=c.name,
        primary_class_name=c.primary.name,
        body=c.body,
        severity=severity,
        message=c.message,
    )


def _to_published_spec(spec: Spec, *, revision: int, content_hash: str) -> PublishedSpec:
    return PublishedSpec(
        id=spec.id,
        version=spec.version,
        revision=revision,
        content_hash=content_hash,
        classes=[_to_class(c) for c in spec.classes],
        sources=[_to_source(s) for s in spec.sources],
        source_bindings=[_to_source_binding(b) for b in spec.source_bindings],
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
