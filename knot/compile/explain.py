"""Resolver observability — explain-winner SQL emitter.

``emit_explain_winner_sql`` returns a single SELECT that shows, for
every (canonical_id, slot_name, source_name) triple in a concrete
class's bindings, who won the argmax and by how much.

One row per (canonical_id, slot_name, source_name).  Columns:

  canonical_id  — the entity being resolved
  slot_name     — which slot is being contested
  source_name   — which source makes this claim
  slot_value    — the source's claim, cast to text
  weight        — the (source, class, slot) weight from source_weight;
                  NULL if no row exists in that table
  is_winner     — TRUE iff this source has the highest weight for this
                  (canonical_id, slot_name) pair
  margin        — for the winning row: winner_weight −
                  second_place_weight (0.0 when only one source claims
                  a non-null value); NULL for non-winner rows

Identifier slots are excluded — there is no resolution decision for
the canonical_id itself.  Virtual classes raise ValueError; a slot-
name typo raises KeyError.
"""

from __future__ import annotations

from knot.spec import ClassKind, OntologyClass, VirtualClass

_DEFAULT_SCHEMA = "knot_data"
_DEFAULT_BINDINGS_SUFFIX = "_bindings"
_DEFAULT_WEIGHT_TABLE = "source_weight"


def emit_explain_winner_sql(
    cls: OntologyClass,
    *,
    slot: str | None = None,
    schema: str = _DEFAULT_SCHEMA,
    bindings_suffix: str = _DEFAULT_BINDINGS_SUFFIX,
    weight_table: str = _DEFAULT_WEIGHT_TABLE,
) -> str:
    """Return one SELECT showing who won (and by how much) for each slot.

    Parameters
    ----------
    cls
        A concrete ``OntologyClass``.  Virtual classes raise
        ``ValueError``; abstract classes also raise ``ValueError``
        (no bindings table).
    slot
        When given, emit for that one slot only.  A typo raises
        ``KeyError`` (via ``cls.get_slot``).  When ``None``, emit for
        all non-identifier slots.
    schema
        Postgres schema name (default ``"knot_data"``).
    bindings_suffix
        Suffix for the bindings table name (default ``"_bindings"``).
    weight_table
        Name of the weight-policy table (default ``"source_weight"``).

    Returns
    -------
    str
        A single SQL SELECT string with literals inlined.  No
        placeholders — the host calls ``cur.execute(sql)`` directly.
    """
    if isinstance(cls, VirtualClass) or cls.kind != ClassKind.CONCRETE:
        kind_label = "virtual" if isinstance(cls, VirtualClass) else cls.kind.value
        raise ValueError(
            f"class {cls.name!r} is {kind_label!r}; "
            f"emit_explain_winner_sql is only supported for concrete classes"
        )

    ident = cls.identifier_slot()

    if slot is not None:
        # Validate — raises KeyError on typo.
        cls.get_slot(slot)
        target_slots = [s for s in cls.effective_slots() if s.name == slot]
    else:
        target_slots = [s for s in cls.effective_slots() if s.name != ident.name]

    if not target_slots:
        raise ValueError(f"class {cls.name!r} has no non-identifier slots to explain")

    bindings_table = f"{schema}.{cls.name.lower()}{bindings_suffix}"
    weight_table_fq = f"{schema}.{weight_table}"

    # per_source CTE — one UNION ALL branch per slot, unpivoting the
    # bindings table into (canonical_id, slot_name, source_name,
    # slot_value) rows.  Only rows with a non-null value and a stamped
    # canonical_id are included.
    union_branches: list[str] = []
    for s in target_slots:
        union_branches.append(
            f"  SELECT {ident.name}, '{s.name}' AS slot_name, source_name,\n"
            f"         ({s.name})::text AS slot_value\n"
            f"  FROM {bindings_table}\n"
            f"  WHERE {ident.name} IS NOT NULL AND {s.name} IS NOT NULL"
        )

    per_source_body = "\n  UNION ALL\n".join(union_branches)

    # weighted CTE — attach the runtime weight via LEFT JOIN.
    # weighted CTE — window functions for ranking.
    # ranked CTE  — RANK() and MAX() window fns for winner detection
    #               and margin calculation.

    sql = (
        "WITH per_source AS (\n"
        f"{per_source_body}\n"
        "),\n"
        "weighted AS (\n"
        "  SELECT\n"
        "    ps.canonical_id,\n"
        "    ps.slot_name,\n"
        "    ps.source_name,\n"
        "    ps.slot_value,\n"
        "    w.weight\n"
        "  FROM per_source ps\n"
        f"  LEFT JOIN {weight_table_fq} w\n"
        "    ON w.source_name = ps.source_name\n"
        f"   AND w.class_name = '{cls.name}'\n"
        "   AND w.slot_name = ps.slot_name\n"
        "),\n"
        "ranked AS (\n"
        "  SELECT\n"
        "    canonical_id,\n"
        "    slot_name,\n"
        "    source_name,\n"
        "    slot_value,\n"
        "    weight,\n"
        "    -- ROW_NUMBER (not RANK) with (weight, source_name) tuple\n"
        "    -- matches the resolver view's tie-break exactly: weight DESC\n"
        "    -- then alphabetical source_name. RANK() over weight-only\n"
        "    -- ties would falsely label two sources is_winner = true\n"
        "    -- while the resolver picked exactly one.\n"
        "    ROW_NUMBER() OVER (\n"
        "      PARTITION BY canonical_id, slot_name\n"
        "      ORDER BY weight DESC NULLS LAST, source_name\n"
        "    ) AS rn,\n"
        "    MAX(weight) OVER (\n"
        "      PARTITION BY canonical_id, slot_name\n"
        "    ) AS max_w,\n"
        "    LAG(weight) OVER (\n"
        "      PARTITION BY canonical_id, slot_name\n"
        "      ORDER BY weight DESC NULLS LAST, source_name\n"
        "    ) AS prev_w\n"
        "  FROM weighted\n"
        ")\n"
        "SELECT\n"
        "  canonical_id,\n"
        "  slot_name,\n"
        "  source_name,\n"
        "  slot_value,\n"
        "  weight,\n"
        "  (rn = 1) AS is_winner,\n"
        "  -- margin = winner.weight - runner_up.weight. NULL when the\n"
        "  -- winner has no runner-up (a single source for this slot) —\n"
        "  -- more honest than 0.0 which reads as 'tied with second'.\n"
        "  CASE\n"
        "    WHEN rn = 1 THEN\n"
        "      max_w - (\n"
        "        SELECT r2.weight FROM ranked r2\n"
        "        WHERE r2.canonical_id = ranked.canonical_id\n"
        "          AND r2.slot_name = ranked.slot_name\n"
        "          AND r2.rn = 2\n"
        "      )\n"
        "  END AS margin\n"
        "FROM ranked\n"
        "ORDER BY canonical_id, slot_name, rn;"
    )

    # Replace the generic canonical_id reference with the actual
    # identifier slot name throughout (handles specs where it has a
    # different name, though the convention is canonical_id).
    if ident.name != "canonical_id":
        sql = sql.replace("canonical_id", ident.name)

    return sql
