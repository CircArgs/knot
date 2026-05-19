"""Read-data path — resolved-view emission.

For each concrete ``OntologyClass``, emit a SQL ``VIEW`` named
``<schema>.<class>_resolved`` that joins all currently-open rows of
``<class>_bindings`` (``valid_to IS NULL``) and picks each slot's
winning value by argmax over the per-(source, class, slot) weight.
One row per ``canonical_id``.

Resolution semantics:

  - Each (source, class, slot) triple has a row in
    ``<schema>.<weight_table>`` (default ``source_weight``) carrying
    its current weight. The resolved view ``LEFT JOIN``s against that
    table at query time, so the weight is operational state — an
    operator can ``UPDATE source_weight SET weight = 12.5 WHERE
    source_name = 'imdb' AND class_name = 'Movie' AND slot_name =
    'year'`` and the resolver picks up the new value immediately
    without rebuilding the view.
  - Per slot, the winner is the binding with the highest weight among
    those whose value for that slot is non-null. Ties are broken by
    ``source_name`` alphabetical (deterministic).
  - Triples missing from ``source_weight`` fall to ``COALESCE(..., 0)``
    and lose every tie-break — effectively ignored.
  - Slots with no non-null claim resolve to ``NULL``.

Weights are opaque floats. The argmax doesn't care about scale or
calibration; whatever produced the numbers owns that.
"""

from __future__ import annotations

from knot.spec import ClassKind, OntologyClass, Slot, Spec


def _winning_value_expr(
    *,
    bindings_table: str,
    weight_table: str,
    class_name: str,
    canonical_id_slot: Slot,
    slot: Slot,
) -> str:
    """Correlated subquery that picks the winning value for ``slot``.
    The JOIN against the weight table is per-(source, class, slot) so
    each slot gets its own weight."""
    return (
        f"(SELECT b.{slot.name} "
        f"FROM {bindings_table} b "
        f"LEFT JOIN {weight_table} w "
        f"ON w.source_name = b.source_name "
        f"AND w.class_name = '{class_name}' "
        f"AND w.slot_name = '{slot.name}' "
        f"WHERE b.{canonical_id_slot.name} = cb.{canonical_id_slot.name} "
        f"AND b.valid_to IS NULL "
        f"AND b.{slot.name} IS NOT NULL "
        f"ORDER BY COALESCE(w.weight, 0) DESC, b.source_name "
        f"LIMIT 1)"
    )


def emit_resolved_view(
    spec: Spec,
    cls: OntologyClass,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    weight_table_name: str = "source_weight",
    if_not_exists: bool = False,
) -> str:
    """Return ``CREATE VIEW <schema>.<class><resolved_suffix>`` for one
    concrete class, resolving per-slot winners from its bindings table.

    The view depends on ``<schema>.<weight_table_name>`` existing —
    emit it via ``emit_ddl`` (which creates the table) before
    deploying this view, and upsert rows at runtime via
    ``binding.upsert_weight_sql()`` / ``upsert_weights_sql()``.
    """
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; resolved views are "
            f"only emitted for concrete classes"
        )

    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"
    weight_table = f"{schema}.{weight_table_name}"
    view_name = f"{schema}.{cls.name.lower()}{resolved_suffix}"
    ident = cls.identifier_slot()
    create = "CREATE OR REPLACE VIEW" if if_not_exists else "CREATE VIEW"

    select_lines: list[str] = [f"    cb.{ident.name}"]
    for slot in cls.effective_slots():
        if slot.name == ident.name:
            continue
        expr = _winning_value_expr(
            bindings_table=bindings_table,
            weight_table=weight_table,
            class_name=cls.name,
            canonical_id_slot=ident,
            slot=slot,
        )
        select_lines.append(f"    {expr} AS {slot.name}")

    # The outer FROM enumerates each canonical_id that has at least one
    # currently-open binding. The ``IS NOT NULL`` filter excludes
    # bindings whose canonical_id hasn't been assigned by ER yet — they
    # stay invisible to the resolved view until ER claims them.
    return (
        f"{create} {view_name} AS\n"
        "SELECT\n" + ",\n".join(select_lines) + "\n"
        "FROM (\n"
        f"    SELECT DISTINCT {ident.name}\n"
        f"    FROM {bindings_table}\n"
        f"    WHERE valid_to IS NULL AND {ident.name} IS NOT NULL\n"
        ") AS cb;"
    )


def emit_resolved_views(
    spec: Spec,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    resolved_suffix: str = "_resolved",
    weight_table_name: str = "source_weight",
    if_not_exists: bool = False,
) -> list[str]:
    """Return one ``CREATE VIEW`` per concrete class in ``spec``."""
    out: list[str] = []
    for cls in spec.classes.values():
        if isinstance(cls, OntologyClass) and cls.kind == ClassKind.CONCRETE:
            out.append(
                emit_resolved_view(
                    spec,
                    cls,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    resolved_suffix=resolved_suffix,
                    weight_table_name=weight_table_name,
                    if_not_exists=if_not_exists,
                )
            )
    return out


# ---------------------------------------------------------------------------
# All-sources / provenance view
# ---------------------------------------------------------------------------


def emit_all_sources_view(
    spec: Spec,
    cls: OntologyClass,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    all_sources_suffix: str = "_all_sources",
    weight_table_name: str = "source_weight",
    if_not_exists: bool = False,
) -> str:
    """Return ``CREATE VIEW <schema>.<class><all_sources_suffix>`` — the
    provenance view.

    Same shape as ``<class>_resolved`` (one row per ``canonical_id``)
    but every slot column carries a ``jsonb`` object keyed by source
    name, with ``{value, weight}`` payload per source::

        {
          "imdb": {"value": 1994, "weight": 0.85},
          "tmdb": {"value": 1995, "weight": 0.70}
        }

    Sources contributing ``NULL`` for a slot are filtered out per slot
    (so a partial-coverage source doesn't leave a ``{"src": {"value":
    null, "weight": …}}`` entry). The identifier slot stays as a plain
    column — it's the key, not a multi-source claim.

    This is the substrate downstream layers (GraphQL ``SlotValue``
    types, audit UIs, ER candidate-generation) read from when they
    want to surface provenance alongside the resolved value.
    """
    if cls.kind != ClassKind.CONCRETE:
        raise ValueError(
            f"class {cls.name!r} is {cls.kind.value!r}; all-sources views are "
            f"only emitted for concrete classes"
        )

    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"
    weight_table = f"{schema}.{weight_table_name}"
    view_name = f"{schema}.{cls.name.lower()}{all_sources_suffix}"
    ident = cls.identifier_slot()
    create = "CREATE OR REPLACE VIEW" if if_not_exists else "CREATE VIEW"

    select_lines: list[str] = [f"    b.{ident.name}"]
    join_lines: list[str] = []

    for slot in cls.effective_slots():
        if slot.name == ident.name:
            continue
        alias = f"w_{slot.name}"
        join_lines.append(
            f"LEFT JOIN {weight_table} {alias}\n"
            f"  ON {alias}.source_name = b.source_name "
            f"AND {alias}.class_name = '{cls.name}' "
            f"AND {alias}.slot_name = '{slot.name}'"
        )
        select_lines.append(
            f"    jsonb_object_agg(\n"
            f"      b.source_name,\n"
            f"      jsonb_build_object('value', b.{slot.name}, "
            f"'weight', COALESCE({alias}.weight, 0))\n"
            f"    ) FILTER (WHERE b.{slot.name} IS NOT NULL) AS {slot.name}"
        )

    joins = "\n".join(join_lines)
    return (
        f"{create} {view_name} AS\n"
        "SELECT\n" + ",\n".join(select_lines) + "\n"
        f"FROM {bindings_table} b\n"
        f"{joins}\n"
        f"WHERE b.valid_to IS NULL AND b.{ident.name} IS NOT NULL\n"
        f"GROUP BY b.{ident.name};"
    )


def emit_all_sources_views(
    spec: Spec,
    *,
    schema: str = "knot_data",
    bindings_suffix: str = "_bindings",
    all_sources_suffix: str = "_all_sources",
    weight_table_name: str = "source_weight",
    if_not_exists: bool = False,
) -> list[str]:
    """Return one ``CREATE VIEW <class>_all_sources`` per concrete class."""
    out: list[str] = []
    for cls in spec.classes.values():
        if isinstance(cls, OntologyClass) and cls.kind == ClassKind.CONCRETE:
            out.append(
                emit_all_sources_view(
                    spec,
                    cls,
                    schema=schema,
                    bindings_suffix=bindings_suffix,
                    all_sources_suffix=all_sources_suffix,
                    weight_table_name=weight_table_name,
                    if_not_exists=if_not_exists,
                )
            )
    return out
