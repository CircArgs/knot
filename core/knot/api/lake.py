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
from knot.api.auth.security import require_user
from knot.graph import lake as graph_lake

router = APIRouter(prefix="/lake", tags=["lake"])


class ClassMaterialization(BaseModel):
    name: str
    current: str  # SELECT body — flat current snapshot
    history: str  # SELECT body — full SCD2 timeline


class MaterializeResponse(BaseModel):
    spec_revision: int
    classes: list[ClassMaterialization]


@router.get(
    "/materialize",
    response_model=MaterializeResponse,
    dependencies=[Depends(require_user)],
)
async def materialize(
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
    async with db.connect() as conn:
        try:
            revision, materializations = await graph_lake.materialize(conn, class_filter=class_name)
        except graph_lake.NoSpecPublishedError as exc:
            raise HTTPException(409, str(exc)) from exc
        except graph_lake.ClassNotMaterializableError as exc:
            raise HTTPException(404, str(exc)) from exc

    return MaterializeResponse(
        spec_revision=revision,
        classes=[
            ClassMaterialization(name=m.name, current=m.current, history=m.history)
            for m in materializations
        ],
    )
