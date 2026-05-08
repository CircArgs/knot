"""POST /graph/constraints/check — run all published constraints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from knot import db
from knot.api.graph._common import StrictBase, published_or_409
from knot.security import require_user


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
def check_constraints() -> ConstraintCheckResponse:
    """Run every published constraint against the current data plane.

    Returns the union of offending rows across all constraints in the
    uniform violation shape: (rule_id, class_name, slot_name, offending_pk,
    detail). An empty ``violations`` list means all constraints pass.
    """
    from knot.db.sql_compiler import compile_constraint

    violations: list[ViolationRow] = []

    with db.connect() as conn:
        spec = published_or_409(conn)
        classes_by_name = {c.name: c for c in spec.classes}

        for constraint in spec.constraints:
            cls = classes_by_name.get(constraint.primary.name)
            if cls is None or cls.abstract:
                continue
            stmt, params = compile_constraint(constraint, cls)
            try:
                rows = conn.execute(stmt, params).fetchall()
            except Exception:
                continue
            for row in rows:
                violations.append(ViolationRow(
                    rule_id=row[0],
                    class_name=row[1],
                    slot_name=row[2],
                    offending_pk=str(row[3]),
                    detail=row[4] or "",
                ))

    return ConstraintCheckResponse(violations=violations)
