"""Weight runtime — read/write SQL emitters for the ``source_weight``
table.

Weights are **runtime-only** in knot. The spec describes structure
(what slots exist, what binds to what); the operator owns calibration
(what each (source, class, slot) weighs at argmax time). Removing
spec-level weight setters eliminates a real source of confusion —
"did this weight come from the spec or the runtime tuning" was a
question nobody could answer without grepping for set_default_weight
calls and comparing them to the table state.

This module emits three SQL shapes:

  - ``emit_read_weights_sql``    SELECT a binding's current weights
  - ``emit_upsert_weight_sql``   set one (source, class, slot) weight
  - ``emit_upsert_weights_sql``  bulk: set N slots in one statement

All three return parameterized SQL — the host's connector binds the
values. The first is for "what's already there?" inspection
(useful for gating: don't reset a weight an operator just tuned).
The next two are for setting; ``ON CONFLICT … UPDATE`` upsert
semantics so the same call works for first-time insert and
subsequent re-tuning.
"""

from __future__ import annotations

from knot.spec import CORRECTIONS_SOURCE_NAME, Source, SourceBinding, Spec


def emit_read_weights_sql(
    binding: SourceBinding,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """SELECT this binding's currently-stored weights.

    Returns rows of ``(slot_name, weight)``. Empty result means no
    runtime tuning has happened yet for this binding's (source, class)
    pair; the resolver falls back to ``COALESCE(weight, 0)`` for
    those slots, so unset → 0.

    The host typically uses this to gate write decisions: skip the
    upsert if the operator has already tuned the value, or merge
    spec-suggested defaults with operator overrides.
    """
    spec = binding._require_spec()
    return (
        f"SELECT slot_name, weight\n"
        f"FROM {spec.schema}.{weight_table_name}\n"
        f"WHERE source_name = '{binding.source.name}'\n"
        f"  AND class_name = '{binding.class_.name}';"
    )


def emit_upsert_weight_sql(
    binding: SourceBinding,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """Set the weight for one (source, class, slot) — INSERT … ON
    CONFLICT UPDATE.

    Two named placeholders the host binds:
      - ``%(slot_name)s`` — the class slot to weight
      - ``%(weight)s``    — the new weight value (opaque float)

    Same statement works for first-time insert and re-tuning.
    """
    spec = binding._require_spec()
    return (
        f"INSERT INTO {spec.schema}.{weight_table_name}\n"
        f"    (source_name, class_name, slot_name, weight)\n"
        f"VALUES ('{binding.source.name}', '{binding.class_.name}',\n"
        f"        %(slot_name)s, %(weight)s)\n"
        f"ON CONFLICT (source_name, class_name, slot_name)\n"
        f"DO UPDATE SET weight = EXCLUDED.weight;"
    )


def emit_upsert_weights_sql(
    binding: SourceBinding,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """Bulk-set N weights for this binding in one statement.

    One named placeholder the host binds:
      - ``%(weights)s`` — a JSON object ``{slot_name: weight, …}``

    Same upsert semantics as the single-slot form: existing rows get
    overwritten, missing rows get inserted. The host typically renders
    the dict to JSON via ``json.dumps`` before binding.
    """
    spec = binding._require_spec()
    return (
        f"INSERT INTO {spec.schema}.{weight_table_name}\n"
        f"    (source_name, class_name, slot_name, weight)\n"
        f"SELECT '{binding.source.name}', '{binding.class_.name}',\n"
        f"       key, (value)::text::double precision\n"
        f"FROM jsonb_each(%(weights)s::jsonb)\n"
        f"ON CONFLICT (source_name, class_name, slot_name)\n"
        f"DO UPDATE SET weight = EXCLUDED.weight;"
    )


def emit_delete_weight_sql(
    binding: SourceBinding,
    slot_name: str,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """DELETE the ``(source, class, slot)`` weight row, reverting to the
    resolver's ``COALESCE(weight, 0)`` fallback.

    Validates ``slot_name`` against the binding's class — ``KeyError`` on
    typo. Returns a single DELETE statement with all three key columns
    inlined as literals (no placeholders).
    """
    binding.class_.get_slot(slot_name)  # KeyError on typo
    spec = binding._require_spec()
    src = "'" + binding.source.name.replace("'", "''") + "'"
    cls = "'" + binding.class_.name.replace("'", "''") + "'"
    slot = "'" + slot_name.replace("'", "''") + "'"
    return (
        f"DELETE FROM {spec.schema}.{weight_table_name}\n"
        f"WHERE source_name = {src}\n"
        f"  AND class_name = {cls}\n"
        f"  AND slot_name = {slot};"
    )


# Default high-rank weight for the auto-bound `_user_corrections`
# source. Operators can override per-(class, slot) at runtime via
# `binding.upsert_weight_sql()` — this just guarantees the row exists
# so an un-seeded deploy can't silently weight corrections at 0.
CORRECTIONS_DEFAULT_WEIGHT: float = 1e6


def emit_weight_seed(
    spec: Spec,
    *,
    defaults: dict[str, float] | None = None,
    weight_table_name: str = "source_weight",
) -> list[tuple[str, dict[str, object]]]:
    """Return ``[(sql, params), …]`` — INSERT-only seed rows for the
    ``source_weight`` table.

    One row per ``(source, class, non-identifier-slot)`` triple. Existing
    rows are not touched (`ON CONFLICT DO NOTHING`) — runtime tuning by
    the operator is preserved across re-deploys.

    ``defaults`` maps ``source_name`` → weight float; sources not in the
    dict default to 0.0 (i.e. they will lose every argmax until the
    operator tunes them). The auto-bound ``_user_corrections`` source
    is **always** seeded at ``CORRECTIONS_DEFAULT_WEIGHT`` (1e6) if the
    caller didn't override — this closes the silent-failure window
    where an operator forgets to seed corrections and every mutation
    appears to succeed but later reads return the pre-correction value.
    Operators can still tune corrections weight per (class, slot)
    afterward; we just guarantee the row exists.
    """
    defaults = dict(defaults or {})
    defaults.setdefault(CORRECTIONS_SOURCE_NAME, CORRECTIONS_DEFAULT_WEIGHT)

    out: list[tuple[str, dict[str, object]]] = []
    for binding in spec.source_bindings:
        weight = defaults.get(binding.source.name, 0.0)
        ident_name = binding.class_.identifier_slot().name
        sql = (
            f"INSERT INTO {spec.schema}.{weight_table_name}\n"
            f"    (source_name, class_name, slot_name, weight)\n"
            f"VALUES (%(source_name)s, %(class_name)s, %(slot_name)s, %(weight)s)\n"
            f"ON CONFLICT (source_name, class_name, slot_name) DO NOTHING;"
        )
        for slot in binding.class_.effective_slots():
            if slot.name == ident_name:
                continue
            out.append(
                (
                    sql,
                    {
                        "source_name": binding.source.name,
                        "class_name": binding.class_.name,
                        "slot_name": slot.name,
                        "weight": weight,
                    },
                )
            )
    return out


def emit_source_read_weights_sql(
    source: Source,
    *,
    weight_table_name: str = "source_weight",
) -> str:
    """SELECT every weight for ``source`` across all of its bindings.
    Convenience for "show me everything this source currently weighs."
    Returns rows of ``(class_name, slot_name, weight)``."""
    if source._spec is None:
        raise RuntimeError(
            f"Source {source.name!r} is not attached to a Spec "
            f"(create via spec.add_source(...))"
        )
    return (
        f"SELECT class_name, slot_name, weight\n"
        f"FROM {source._spec.schema}.{weight_table_name}\n"
        f"WHERE source_name = '{source.name}'\n"
        f"ORDER BY class_name, slot_name;"
    )
