"""Constraint validation emitter — turn each ``Constraint.body`` (an
``Expr`` from the semantic builder) into a SELECT that surfaces rows
of the primary class which violate the predicate.

Each emitted SELECT has the uniform shape:

    SELECT
        '<rule_id>'    AS rule_id,
        '<class_name>' AS class_name,
        '<severity>'   AS severity,
        <message>      AS message,
        <pk_col>       AS offending_pk
    FROM <schema>.<class><target_suffix>
    WHERE NOT (<body_sql>);

Empty result → constraint passes. Non-empty rows are violations; the host
inspects ``severity`` to decide block-vs-warn.

``target_suffix`` defaults to ``"_resolved"`` so validation runs against
the resolver's per-slot argmax view. Pass ``""`` to target the canonical
table directly — appropriate only for write-direct workflows where the
host writes the canonical table itself.

The body's ``.to_sql(schema, target_suffix)`` does the qualification —
no AST rewriting needed here, because the builder produces references
keyed by class name + slot name that render with the same suffix used
in the wrapping FROM clause.
"""

from __future__ import annotations

from knot.spec import Spec


def _escape_literal(s: str) -> str:
    """Escape a string for a single-quoted SQL literal."""
    return s.replace("'", "''")


def emit_validation(
    spec: Spec,
    *,
    schema: str = "knot_data",
    target_suffix: str = "_resolved",
) -> list[tuple[str, str]]:
    """Return ``(constraint_name, validation_sql)`` pairs.

    Each ``validation_sql`` returns zero rows when the constraint holds
    and one row per violating canonical_id otherwise.
    """
    out: list[tuple[str, str]] = []
    for c in spec.constraints:
        primary = c.primary
        identifier = primary.identifier_slot()
        table = f"{schema}.{primary.name.lower()}{target_suffix}"
        message_literal = f"'{_escape_literal(c.message)}'" if c.message else "NULL"
        body_sql = c.body.to_sql(schema=schema, target_suffix=target_suffix)
        sql = (
            f"SELECT\n"
            f"    '{_escape_literal(c.name)}' AS rule_id,\n"
            f"    '{_escape_literal(primary.name)}' AS class_name,\n"
            f"    '{_escape_literal(c.severity)}' AS severity,\n"
            f"    {message_literal} AS message,\n"
            f"    {identifier.name} AS offending_pk\n"
            f"FROM {table}\n"
            f"WHERE NOT ({body_sql});"
        )
        out.append((c.name, sql))
    return out


def emit_validation_union(
    spec: Spec,
    *,
    schema: str = "knot_data",
    target_suffix: str = "_resolved",
) -> str | None:
    """Return a single ``UNION ALL`` of every constraint's validation
    SELECT, or ``None`` if the spec has no constraints."""
    parts = [
        sql.rstrip(";")
        for _, sql in emit_validation(spec, schema=schema, target_suffix=target_suffix)
    ]
    if not parts:
        return None
    return "\nUNION ALL\n".join(parts) + ";"


__all__ = ["emit_validation", "emit_validation_union"]
