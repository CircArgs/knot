"""Builtin extension — runs ERROR-severity constraints post-INSERT.

Registers on RowsIngested at priority 50 (before DQ). Compiles each
ERROR-severity constraint whose primary class matches the inserted
source's class, executes it inside the current transaction, and raises
``ConstraintViolations`` if any rule fires (or fails to compile/execute
— that case surfaces as a synthetic violation row with
``offending_pk='*'``).

Skipped unless the route opts in via ``RowsIngested.validate_constraints``.
The route's ``conn.transaction()`` rolls back the INSERT (and any later
handler side-effects) when this raises.
"""

from __future__ import annotations

from typing import Any

from knot.extensions import RequestContext, dispatch
from knot.extensions.events import RowsIngested
from knot.spec import Constraint, OntologyClass, Severity


class ConstraintViolations(Exception):
    """Raised when post-INSERT ERROR-severity constraints find violations.

    Rolled back inside the route's transaction; ``violations`` carries
    the uniform shape ``{rule_id, class_name, slot_name, offending_pk,
    detail}``.
    """

    def __init__(self, violations: list[dict[str, Any]]) -> None:
        self.violations = violations
        super().__init__(f"{len(violations)} constraint violation(s)")


async def _violations_for_constraint(
    ctx: RequestContext, constraint: Constraint, cls: OntologyClass
) -> list[dict[str, Any]]:
    from knot.spec.compile.postgres import compile_constraint

    try:
        stmt, params = compile_constraint(constraint, cls)
    except Exception as exc:
        return [
            {
                "rule_id": constraint.name,
                "class_name": constraint.primary.name,
                "slot_name": None,
                "offending_pk": "*",
                "detail": f"compile failure: {exc}",
            }
        ]
    try:
        rows = await (await ctx.db.conn.execute(stmt, params)).fetchall()
    except Exception as exc:
        return [
            {
                "rule_id": constraint.name,
                "class_name": constraint.primary.name,
                "slot_name": None,
                "offending_pk": "*",
                "detail": f"execute failure: {exc}",
            }
        ]
    return [
        {
            "rule_id": row[0],
            "class_name": row[1],
            "slot_name": row[2],
            "offending_pk": str(row[3]),
            "detail": row[4] or "",
        }
        for row in rows
    ]


@dispatch.on(RowsIngested, priority=50)
async def _validate_constraints(ev: RowsIngested, ctx: RequestContext) -> None:
    if not ev.validate_constraints:
        return  # opt-in only

    cls = ev.source.entity_class
    relevant = [
        c
        for c in ev.spec.constraints
        if c.primary.name == cls.name and getattr(c, "severity", Severity.ERROR) == Severity.ERROR
    ]
    if not relevant:
        return

    violations: list[dict[str, Any]] = []
    for constraint in relevant:
        violations.extend(await _violations_for_constraint(ctx, constraint, cls))

    if violations:
        raise ConstraintViolations(violations)
