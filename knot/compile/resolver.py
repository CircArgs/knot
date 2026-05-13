"""Read-data path — resolved-view emission.

For each concrete ``OntologyClass``, emit a SQL ``VIEW`` named
``<schema>.<class>_resolved`` that joins all currently-open rows of
``<class>_bindings`` (``valid_to IS NULL``) and picks each slot's
winning value by argmax over the source's ``accuracy``. One row per
``canonical_id``.

Resolution semantics (prototype, no observed-evidence updates yet):

  - Each ``SourceBinding`` carries a single ``accuracy`` (0-1). Because
    no posterior updates from data are applied yet, the prior mean
    equals the posterior mean equals the host's stated accuracy.
  - Per slot, the winner is the binding with the highest accuracy
    among those whose value for that slot is non-null. Ties are broken
    by ``source_name`` alphabetical (deterministic).
  - Slots with no non-null claim resolve to ``NULL``.

The accuracy table for each class is inlined as a ``CASE WHEN
source_name = '…' THEN <accuracy> … ELSE 0`` expression so the view is
self-contained — it doesn't depend on the meta-tables. A future
refactor could JOIN to ``knot_meta.source_bindings(accuracy)`` instead.

Unknown sources (rows in the bindings table whose ``source_name`` isn't
declared as a binding in the spec) fall to ``ELSE 0`` accuracy and lose
every tie-break — effectively ignored.
"""

from __future__ import annotations

from knot.spec import ClassKind, OntologyClass, Slot, Spec


def _accuracy_case(spec: Spec, cls: OntologyClass) -> str:
    """``CASE WHEN source_name = 'imdb' THEN 0.85 … ELSE 0`` for ``cls``'s
    declared bindings."""
    bindings = [b for b in spec.source_bindings if b.class_ is cls]
    if not bindings:
        # No declared sources → every value loses; the view stays empty
        # except for canonical_ids actually present in bindings (which
        # would only exist if something wrote unbound source claims).
        return "0::double precision"
    parts = [
        f"WHEN b.source_name = '{b.source.name}' THEN {b.accuracy}"
        for b in bindings
    ]
    return "CASE " + " ".join(parts) + " ELSE 0 END"


def _winning_value_expr(
    bindings_table: str,
    canonical_id_slot: Slot,
    slot: Slot,
    accuracy_case: str,
) -> str:
    """Correlated subquery that picks the winning value for ``slot``."""
    return (
        f"(SELECT b.{slot.name} "
        f"FROM {bindings_table} b "
        f"WHERE b.{canonical_id_slot.name} = cb.{canonical_id_slot.name} "
        f"AND b.valid_to IS NULL "
        f"AND b.{slot.name} IS NOT NULL "
        f"ORDER BY ({accuracy_case}) DESC, b.source_name "
        f"LIMIT 1)"
    )


def emit_resolved_view(
    spec: Spec,
    cls: OntologyClass,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    if_not_exists: bool = False,
) -> str:
    """Return ``CREATE VIEW <schema>.<class><resolved_suffix>`` for one
    concrete class, resolving per-slot winners from its bindings table.
    """
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; resolved views are "
            f"only emitted for concrete classes"
        )

    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"
    view_name = f"{schema}.{cls.name.lower()}{resolved_suffix}"
    ident = cls.identifier_slot()
    accuracy = _accuracy_case(spec, cls)
    create = "CREATE OR REPLACE VIEW" if if_not_exists else "CREATE VIEW"

    select_lines: list[str] = [f"    cb.{ident.name}"]
    for slot in cls.effective_slots():
        if slot.name == ident.name:
            continue
        expr = _winning_value_expr(bindings_table, ident, slot, accuracy)
        select_lines.append(f"    {expr} AS {slot.name}")

    # The outer FROM enumerates each canonical_id that has at least one
    # currently-open binding.
    return (
        f"{create} {view_name} AS\n"
        "SELECT\n"
        + ",\n".join(select_lines)
        + "\n"
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
                    if_not_exists=if_not_exists,
                )
            )
    return out


__all__ = ["emit_resolved_view", "emit_resolved_views"]
