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

from collections.abc import Iterator

from knot.ast.select import Layer
from knot.ast.types import ClassRef
from knot.compile.expr import compile_sql
from knot.spec import ClassKind, Constraint, OntologyClass, Severity, Spec


def _escape_literal(s: str) -> str:
    """Escape a string for a single-quoted SQL literal."""
    return s.replace("'", "''")


def builtin_constraints(spec: Spec) -> Iterator[Constraint]:
    """Yield invariants knot ships automatically from the spec's shape.

    Two families today, both prefixed ``_builtin_`` so hosts can
    filter them out by name if they want:

    1. ``_builtin_fk_orphan_<Class>_<slot>`` — every ClassRef slot.
       Body: ``slot.is_null() | slot.target_exists()`` — valid rows
       are those where the FK is null (not provided) or points at a
       real canonical_id in the target's resolved view. ER's forward
       FK translation should keep this satisfied; this constraint
       catches the case where it didn't (e.g. a referenced binding
       was never ER-stamped).
    2. ``_builtin_required_null_<Class>_<slot>`` — every required
       non-identifier slot. Body: ``slot.is_not_null()`` — catches
       the case where every source's claim for a required slot was
       null, leaving the resolved row with NULL.

    Severity defaults to ERROR for FK orphans (structural — should
    never fail post-ER) and WARNING for required nulls (sources are
    expected to fill required slots but the host may want to
    tolerate gaps during early ingest).
    """
    for cls in spec.classes.values():
        # Built-ins only apply to concrete OntologyClass — virtual
        # classes inherit constraints from their concrete root.
        if not isinstance(cls, OntologyClass):
            continue
        if cls.kind != ClassKind.CONCRETE:
            continue
        for slot in cls.effective_slots():
            if isinstance(slot.type, ClassRef):
                fk_ref = cls.col[slot.name]
                yield Constraint(
                    name=f"_builtin_fk_orphan_{cls.name}_{slot.name}",
                    primary=cls,
                    body=fk_ref.is_null() | fk_ref.target_exists(),
                    severity=Severity.ERROR,
                    message=(
                        f"{cls.name}.{slot.name} → {slot.type.target.name}: "
                        f"FK value does not match any canonical_id"
                    ),
                )
            if slot.required and not slot.identifier:
                yield Constraint(
                    name=f"_builtin_required_null_{cls.name}_{slot.name}",
                    primary=cls,
                    body=cls.col[slot.name].is_not_null(),
                    severity=Severity.WARNING,
                    message=(
                        f"required slot {cls.name}.{slot.name} resolved to NULL "
                        f"(no source provided a non-null value)"
                    ),
                )


def emit_validation(
    spec: Spec,
    *,
    schema: str = "knot_data",
    layer: Layer = Layer.RESOLVED,
    scope_to_source_identifiers: dict[str, list[str]] | None = None,
    include_builtins: bool = True,
) -> list[tuple[str, str]]:
    """Return ``(constraint_name, validation_sql)`` pairs.

    Each ``validation_sql`` returns zero rows when the constraint holds
    and one row per violating canonical_id otherwise.

    ``include_builtins`` (default ``True``): also emit invariants
    knot derives from the spec shape — FK-orphan checks per
    ClassRef slot, required-slot-null checks per required slot.
    See ``builtin_constraints`` for details. Pass ``False`` to get
    only the user-declared constraints.

    ``scope_to_source_identifiers`` — when provided, each validation
    SELECT is restricted to the canonical_ids touched by that batch.
    The dict maps ``source_name`` → list of ``source_identifier`` values.
    Inlines the (source, identifier) tuples as SQL literals — no
    parameter list (same posture as ``Query.sql``). ``None`` or empty
    dict means no scoping (full-table validation, the default).
    """
    # Build the optional IN-subquery fragment once (shared across all constraints).
    scope_sql: str | None = None
    if scope_to_source_identifiers:
        pairs: list[str] = []
        for src, idents in scope_to_source_identifiers.items():
            src_lit = "'" + src.replace("'", "''") + "'"
            for ident in idents:
                ident_lit = "'" + ident.replace("'", "''") + "'"
                pairs.append(f"({src_lit}, {ident_lit})")
        if pairs:
            pairs_sql = ", ".join(pairs)
            scope_sql = pairs_sql  # stored; injected per-constraint below

    all_constraints = list(spec.constraints)
    if include_builtins:
        all_constraints.extend(builtin_constraints(spec))

    out: list[tuple[str, str]] = []
    for c in all_constraints:
        primary = c.primary
        identifier = primary.identifier_slot()
        table = f"{schema}.{primary.name.lower()}{layer}"
        bindings_table = f"{schema}.{primary.name.lower()}_bindings"
        message_literal = f"'{_escape_literal(c.message)}'" if c.message else "NULL"
        # outer_class threads the constraint's primary class through so
        # correlated ``this.<Primary>`` refs inside Aggregate predicates
        # bind to the FROM <primary> row, not the subquery's row.
        body_sql = compile_sql(
            c.body, schema=schema, layer=layer, outer_class=primary.name
        )
        scope_clause = ""
        if scope_sql is not None:
            scope_clause = (
                f"\n  AND {table}.{identifier.name} IN (\n"
                f"    SELECT DISTINCT canonical_id FROM {bindings_table}\n"
                f"    WHERE (source_name, source_identifier) IN ({scope_sql})\n"
                f"      AND canonical_id IS NOT NULL\n"
                f"  )"
            )
        sql = (
            f"SELECT\n"
            f"    '{_escape_literal(c.name)}' AS rule_id,\n"
            f"    '{_escape_literal(primary.name)}' AS class_name,\n"
            f"    '{_escape_literal(c.severity)}' AS severity,\n"
            f"    {message_literal} AS message,\n"
            f"    {identifier.name} AS offending_pk\n"
            f"FROM {table}\n"
            f"WHERE NOT ({body_sql}){scope_clause};"
        )
        out.append((c.name, sql))
    return out


def emit_validation_union(
    spec: Spec,
    *,
    schema: str = "knot_data",
    layer: Layer = Layer.RESOLVED,
    scope_to_source_identifiers: dict[str, list[str]] | None = None,
    include_builtins: bool = True,
) -> str | None:
    """Return a single ``UNION ALL`` of every constraint's validation
    SELECT, or ``None`` if the spec has no constraints."""
    parts = [
        sql.rstrip(";")
        for _, sql in emit_validation(
            spec,
            schema=schema,
            layer=layer,
            scope_to_source_identifiers=scope_to_source_identifiers,
            include_builtins=include_builtins,
        )
    ]
    if not parts:
        return None
    return "\nUNION ALL\n".join(parts) + ";"
