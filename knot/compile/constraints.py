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
    FROM <schema>.<class><layer>
    WHERE NOT (<body_sql>);

Empty result → constraint passes. Non-empty rows are violations; the host
inspects ``severity`` to decide block-vs-warn.

``layer`` defaults to ``Layer.RESOLVED`` so validation runs against
the resolver's per-slot argmax view. Pass ``Layer.CANONICAL`` to target
the canonical table directly — appropriate only for write-direct
workflows where the host writes the canonical table itself.

``compile_sql(body, schema, layer)`` does the qualification —
no AST rewriting needed here, because the builder produces references
keyed by class name + slot name that render with the same suffix used
in the wrapping FROM clause.
"""

from __future__ import annotations

from knot.ast.select import Layer
from knot.compile.expr import compile_sql
from knot.spec import Spec


def _escape_literal(s: str) -> str:
    """Escape a string for a single-quoted SQL literal."""
    return s.replace("'", "''")


def emit_validation(
    spec: Spec,
    *,
    schema: str = "knot_data",
    layer: Layer = Layer.RESOLVED,
) -> list[tuple[str, str]]:
    """Return ``(constraint_name, validation_sql)`` pairs.

    Each ``validation_sql`` returns zero rows when the constraint holds
    and one row per violating canonical_id otherwise.
    """
    out: list[tuple[str, str]] = []
    for c in spec.constraints:
        primary = c.primary
        identifier = primary.identifier_slot()
        table = f"{schema}.{primary.name.lower()}{layer}"
        message_literal = f"'{_escape_literal(c.message)}'" if c.message else "NULL"
        # outer_class threads the constraint's primary class through so
        # correlated ``this.<Primary>`` refs inside Aggregate predicates
        # bind to the FROM <primary> row, not the subquery's row.
        body_sql = compile_sql(
            c.body, schema=schema, layer=layer, outer_class=primary.name
        )
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
    layer: Layer = Layer.RESOLVED,
) -> str | None:
    """Return a single ``UNION ALL`` of every constraint's validation
    SELECT, or ``None`` if the spec has no constraints."""
    parts = [
        sql.rstrip(";") for _, sql in emit_validation(spec, schema=schema, layer=layer)
    ]
    if not parts:
        return None
    return "\nUNION ALL\n".join(parts) + ";"
