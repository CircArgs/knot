"""POST /graph/constraints/check — run all published constraints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from knot import db
from knot.api.auth.security import require_user
from knot.api.graph._common import StrictBase, published_or_409
from knot.graph import constraints as graph_constraints

router = APIRouter()


class ViolationRow(StrictBase):
    rule_id: str
    class_name: str
    slot_name: str | None
    offending_pk: str
    detail: str


class ConstraintCheckResponse(StrictBase):
    violations: list[ViolationRow]


@router.post(
    "/constraints/check",
    response_model=ConstraintCheckResponse,
    dependencies=[Depends(require_user)],
)
async def check_constraints() -> ConstraintCheckResponse:
    """Run every published constraint against the current data plane.

    Returns the union of offending rows across all constraints in the
    uniform violation shape: (rule_id, class_name, slot_name, offending_pk,
    detail). An empty ``violations`` list means all constraints pass.
    """
    async with db.connect() as conn:
        spec = await published_or_409(conn)
        violations = await graph_constraints.check_all_constraints(conn, spec)

    return ConstraintCheckResponse(
        violations=[
            ViolationRow(
                rule_id=v.rule_id,
                class_name=v.class_name,
                slot_name=v.slot_name,
                offending_pk=v.offending_pk,
                detail=v.detail,
            )
            for v in violations
        ]
    )
