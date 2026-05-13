"""Constraint validation emitter — turn each ``Constraint.body`` into a
SELECT that surfaces rows of the primary class which violate the predicate.

Each emitted SELECT has the uniform shape:

    SELECT
        '<rule_id>'    AS rule_id,
        '<class_name>' AS class_name,
        '<severity>'   AS severity,
        <message>      AS message,
        <pk_col>       AS offending_pk
    FROM <schema>.<class><target_suffix>
    WHERE NOT (<body>);

Empty result → constraint passes. Non-empty rows are violations; the host
inspects ``severity`` to decide block-vs-warn.

Constraint bodies are written in *spec-relative* SQL: the user names
classes by their spec name (``Movie``, ``Credit``) and slots by class-
qualified column references (``Credit.role``). Before emission the body
is parsed with sqlglot, walked, and rewritten so that:

  - ``FROM Credit``  → ``FROM <schema>.credit<target_suffix>``
  - ``Credit.role``  → ``<schema>.credit<target_suffix>.role``
  - ``Movie.year``   → ``<schema>.movie<target_suffix>.year``

``target_suffix`` defaults to ``"_resolved"`` so validation runs against
the resolver's per-slot argmax view (``knot.compile.resolver``). Pass
``""`` to target the canonical table directly — appropriate only for
write-direct workflows where the host writes the canonical table itself.

Bare unqualified columns (``year``) in the predicate root are left
alone; postgres resolves them against the primary class target named in
the wrapping ``FROM`` clause.
"""

from __future__ import annotations

import sqlglot
from sqlglot import expressions as exp

from knot.spec import OntologyClass, Spec


def _escape_literal(s: str) -> str:
    """Escape a string for a single-quoted SQL literal."""
    return s.replace("'", "''")


def _resolve_body(
    body: str,
    spec: Spec,
    *,
    schema: str,
    target_suffix: str,
) -> str:
    """Rewrite spec-relative class/slot references in ``body`` to
    schema-qualified storage references. Unknown identifiers (aliases,
    builtins, external tables) are left alone."""
    classes_by_name = {
        c.name: c for c in spec.classes if isinstance(c, OntologyClass)
    }
    tree = sqlglot.parse_one(body, dialect="postgres")

    # Table refs:  FROM Credit  →  FROM <schema>.credit<target_suffix>
    for table in tree.find_all(exp.Table):
        if table.name in classes_by_name:
            table.set(
                "this",
                exp.to_identifier(f"{table.name.lower()}{target_suffix}"),
            )
            table.set("db", exp.to_identifier(schema))

    # Class-qualified column refs:
    #   Credit.role  →  <schema>.credit<target_suffix>.role
    for column in tree.find_all(exp.Column):
        tbl = column.table
        if tbl and tbl in classes_by_name:
            column.set("table", exp.to_identifier(f"{tbl.lower()}{target_suffix}"))
            column.set("db", exp.to_identifier(schema))

    return tree.sql(dialect="postgres")


def emit_validation(
    spec: Spec,
    *,
    schema: str = "knot_data",
    target_suffix: str = "_resolved",
) -> list[tuple[str, str]]:
    """Return ``(constraint_name, validation_sql)`` pairs.

    Each ``validation_sql`` returns zero rows when the constraint holds
    and one row per violating canonical_id otherwise.

    ``target_suffix`` controls which per-class object the validation
    queries. Default ``"_resolved"`` hits the resolver view; pass ``""``
    to validate the canonical table directly (write-direct workflows).
    """
    out: list[tuple[str, str]] = []
    for c in spec.constraints:
        primary = c.primary
        identifier = primary.identifier_slot()
        table = f"{schema}.{primary.name.lower()}{target_suffix}"
        message_literal = (
            f"'{_escape_literal(c.message)}'" if c.message else "NULL"
        )
        resolved_body = _resolve_body(
            c.body, spec, schema=schema, target_suffix=target_suffix
        )
        sql = (
            f"SELECT\n"
            f"    '{_escape_literal(c.name)}' AS rule_id,\n"
            f"    '{_escape_literal(primary.name)}' AS class_name,\n"
            f"    '{_escape_literal(c.severity)}' AS severity,\n"
            f"    {message_literal} AS message,\n"
            f"    {identifier.name} AS offending_pk\n"
            f"FROM {table}\n"
            f"WHERE NOT ({resolved_body});"
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
    SELECT, or ``None`` if the spec has no constraints. Useful for
    running every check in a single round-trip."""
    parts = [
        sql.rstrip(";")
        for _, sql in emit_validation(
            spec, schema=schema, target_suffix=target_suffix
        )
    ]
    if not parts:
        return None
    return "\nUNION ALL\n".join(parts) + ";"


__all__ = ["emit_validation", "emit_validation_union"]
