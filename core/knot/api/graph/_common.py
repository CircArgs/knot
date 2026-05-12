"""Shared helpers used across the graph subrouters."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from knot.db import spec_store
from knot.spec import DefinedClass, OntologyClass, Spec


class StrictBase(BaseModel):
    """Forbid-extra base for every request/response model in the graph router."""

    model_config = ConfigDict(extra="forbid")


def resolve_class(spec: Spec, class_name: str) -> OntologyClass | DefinedClass:
    """Look up a class on the published spec; 404 if missing, 400 if abstract.

    Defined classes are readable surfaces (backed by a VIEW), so they
    pass the abstract-gate. Only abstract OntologyClasses (with no
    stored rows) are rejected.
    """
    cls = next((c for c in spec.classes if c.name == class_name), None)
    if cls is None:
        raise HTTPException(404, f"Class {class_name!r} not on the published spec.")
    if isinstance(cls, OntologyClass) and cls.abstract:
        raise HTTPException(400, f"Class {class_name!r} is abstract; no rows are stored.")
    return cls


async def published_or_409(conn) -> Spec:
    """Return the currently-published Spec; 409 if no spec is published yet."""
    spec = await spec_store.get_published(conn)
    if spec is None:
        raise HTTPException(409, "No spec is published yet.")
    return spec
