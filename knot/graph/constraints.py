"""Constraint orchestration — compile + execute every constraint.

Composes the predicate compiler at ``knot.spec.compile.postgres`` with the
data plane via ``conn.execute``. Returns rows in the uniform violation
shape ``(rule_id, class_name, slot_name, offending_pk, detail)``.

Note: this module DOES execute SQL — but only SQL produced by the
compiler, never hand-written strings. The compiler lives outside ``db/``
because it's the spec → SQL primitive; this orchestrator stitches the
compiled fragments to ``conn`` and aggregates the results.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from knot.spec import Spec


@dataclass(frozen=True)
class ViolationRow:
    rule_id: str
    class_name: str
    slot_name: str | None
    offending_pk: str
    detail: str


async def check_all_constraints(conn: psycopg.AsyncConnection, spec: Spec) -> list[ViolationRow]:
    """Compile every concrete-class constraint, execute, collect violations.

    Skips abstract classes (no rows stored). Compile- or execute-time
    failures on a single constraint surface as a synthetic violation row
    (``offending_pk='*'``, ``detail='compile failure: ...'`` /
    ``'execute failure: ...'``) so operators can see the broken rule rather
    than silently getting zero violations.
    """
    from knot.spec.compile.postgres import compile_constraint

    violations: list[ViolationRow] = []
    classes_by_name = {c.name: c for c in spec.classes}

    for constraint in spec.constraints:
        cls = classes_by_name.get(constraint.primary.name)
        if cls is None or cls.abstract:
            continue
        try:
            stmt, params = compile_constraint(constraint, cls)
        except Exception as exc:
            violations.append(
                ViolationRow(
                    rule_id=constraint.name,
                    class_name=constraint.primary.name,
                    slot_name=None,
                    offending_pk="*",
                    detail=f"compile failure: {exc}",
                )
            )
            continue
        try:
            rows = await (await conn.execute(stmt, params)).fetchall()
        except Exception as exc:
            violations.append(
                ViolationRow(
                    rule_id=constraint.name,
                    class_name=constraint.primary.name,
                    slot_name=None,
                    offending_pk="*",
                    detail=f"execute failure: {exc}",
                )
            )
            continue
        for row in rows:
            violations.append(
                ViolationRow(
                    rule_id=row[0],
                    class_name=row[1],
                    slot_name=row[2],
                    offending_pk=str(row[3]),
                    detail=row[4] or "",
                )
            )
    return violations
