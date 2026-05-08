"""Management REST router for lake materialization SQL.

Mounted at ``/lake``. Returns SELECT bodies that an external operator
can wrap in whatever DDL/DML their lake target requires (Trino CTAS,
dbt model file, Iceberg MERGE INTO, etc.). knot does not execute the
materialization — it just emits the queries.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from knot import db
from knot.security import require_user
from knot.db import lake, spec_store


router = APIRouter(prefix="/lake", tags=["lake"])


class ClassMaterialization(BaseModel):
    name: str
    current: str   # SELECT body — flat current snapshot
    history: str   # SELECT body — full SCD2 timeline


class MaterializeResponse(BaseModel):
    spec_revision: int
    classes: list[ClassMaterialization]


@router.get(
    "/materialize",
    response_model=MaterializeResponse,
    dependencies=[Depends(require_user)],
)
def materialize(
    class_name: str | None = Query(
        None,
        alias="class",
        description="Restrict output to one class. Defaults to all materializable classes.",
    ),
) -> MaterializeResponse:
    """Generate SELECT bodies for materializing the data plane to a lake.

    Output: per concrete class (skipping abstract / defined-class views),
    a ``current`` body (resolved SCD2 snapshot) and a ``history`` body
    (full timeline). Caller wraps these in their target dialect.
    """
    with db.connect() as conn:
        spec = spec_store.get_published(conn)
        if spec is None:
            raise HTTPException(409, "No spec is published yet.")
        revision = spec_store.get_published_revision(conn) or 0

        classes = [c for c in spec.classes if lake.is_materializable(c)]
        if class_name is not None:
            classes = [c for c in classes if c.name == class_name]
            if not classes:
                raise HTTPException(
                    404,
                    f"Class {class_name!r} is not on the published spec or "
                    "is not materializable (abstract / defined-class view).",
                )

    return MaterializeResponse(
        spec_revision=revision,
        classes=[
            ClassMaterialization(
                name=c.name,
                current=lake.materialize_current(c),
                history=lake.materialize_history(c),
            )
            for c in classes
        ],
    )
