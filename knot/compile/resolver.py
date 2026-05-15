"""Read-data path — resolved-view emission.

For each concrete ``OntologyClass``, emit a SQL ``VIEW`` named
``<schema>.<class>_resolved`` that joins all currently-open rows of
``<class>_bindings`` (``valid_to IS NULL``) and picks each slot's
winning value by argmax over the per-(source, class, slot) trust.
One row per ``canonical_id``.

Resolution semantics:

  - Each (source, class, slot) triple has a row in
    ``<schema>.<trust_table>`` (default ``source_trust``) carrying its
    current trust value. The resolved view ``LEFT JOIN``s against that
    table at query time, so trust is operational state — an operator
    can ``UPDATE source_trust SET trust = 0.8 WHERE source_name =
    'imdb' AND class_name = 'Movie' AND slot_name = 'year'`` and the
    resolver picks up the new value immediately without rebuilding the
    view.
  - Per slot, the winner is the binding with the highest trust among
    those whose value for that slot is non-null. Ties are broken by
    ``source_name`` alphabetical (deterministic).
  - Triples missing from ``source_trust`` fall to ``COALESCE(..., 0)``
    and lose every tie-break — effectively ignored.
  - Slots with no non-null claim resolve to ``NULL``.
"""

from __future__ import annotations

from knot.spec import ClassKind, OntologyClass, Slot, Spec


def _winning_value_expr(
    *,
    bindings_table: str,
    trust_table: str,
    class_name: str,
    canonical_id_slot: Slot,
    slot: Slot,
) -> str:
    """Correlated subquery that picks the winning value for ``slot``.
    The JOIN against the trust table is per-(source, class, slot) so
    each slot gets its own trust value."""
    return (
        f"(SELECT b.{slot.name} "
        f"FROM {bindings_table} b "
        f"LEFT JOIN {trust_table} t "
        f"ON t.source_name = b.source_name "
        f"AND t.class_name = '{class_name}' "
        f"AND t.slot_name = '{slot.name}' "
        f"WHERE b.{canonical_id_slot.name} = cb.{canonical_id_slot.name} "
        f"AND b.valid_to IS NULL "
        f"AND b.{slot.name} IS NOT NULL "
        f"ORDER BY COALESCE(t.trust, 0) DESC, b.source_name "
        f"LIMIT 1)"
    )


def emit_resolved_view(
    spec: Spec,
    cls: OntologyClass,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    trust_table_name: str = "source_trust",
    if_not_exists: bool = False,
) -> str:
    """Return ``CREATE VIEW <schema>.<class><resolved_suffix>`` for one
    concrete class, resolving per-slot winners from its bindings table.

    The view depends on ``<schema>.<trust_table_name>`` existing —
    emit it via ``emit_ddl`` (which creates the table) before
    deploying this view, and seed it via
    ``knot.compile.trust.emit_trust_seed`` to populate the rows.
    """
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; resolved views are "
            f"only emitted for concrete classes"
        )

    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"
    trust_table = f"{schema}.{trust_table_name}"
    view_name = f"{schema}.{cls.name.lower()}{resolved_suffix}"
    ident = cls.identifier_slot()
    create = "CREATE OR REPLACE VIEW" if if_not_exists else "CREATE VIEW"

    select_lines: list[str] = [f"    cb.{ident.name}"]
    for slot in cls.effective_slots():
        if slot.name == ident.name:
            continue
        expr = _winning_value_expr(
            bindings_table=bindings_table,
            trust_table=trust_table,
            class_name=cls.name,
            canonical_id_slot=ident,
            slot=slot,
        )
        select_lines.append(f"    {expr} AS {slot.name}")

    # The outer FROM enumerates each canonical_id that has at least one
    # currently-open binding.
    return (
        f"{create} {view_name} AS\n"
        "SELECT\n" + ",\n".join(select_lines) + "\n"
        "FROM (\n"
        f"    SELECT DISTINCT {ident.name}\n"
        f"    FROM {bindings_table}\n"
        f"    WHERE valid_to IS NULL\n"
        ") AS cb;"
    )


def emit_resolved_views(
    spec: Spec,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    trust_table_name: str = "source_trust",
    if_not_exists: bool = False,
) -> list[str]:
    """Return one ``CREATE VIEW`` per concrete class in ``spec``."""
    out: list[str] = []
    for cls in spec.classes:
        if isinstance(cls, OntologyClass) and cls.kind == ClassKind.CONCRETE:
            out.append(
                emit_resolved_view(
                    spec,
                    cls,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    resolved_suffix=resolved_suffix,
                    trust_table_name=trust_table_name,
                    if_not_exists=if_not_exists,
                )
            )
    return out


__all__ = ["emit_resolved_view", "emit_resolved_views"]
